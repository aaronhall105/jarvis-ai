"""Production composition for Jarvis external capabilities and agent plans.

This module is intentionally provider-neutral.  It composes real connector
adapters, exposes redacted discovery, and adapts the durable planner to the
connector registry.  Setup-only service descriptions never enter the
executable registry.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import uuid
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from pathlib import Path
from typing import Any

from app.agent_planner import (
    CapabilityAccess as PlanAccess,
    CapabilityExecutionRequest as PlanExecutionRequest,
    CapabilityExecutionResult as PlanExecutionResult,
    CapabilityRequirement,
    CapabilityState,
    EvidenceRequirement,
    ExecutionStatus as PlanExecutionStatus,
    PersonalAgentPlanner,
    ProposedStep,
    RequestRoute,
    RiskLevel as PlanRisk,
    SQLitePlanStore,
)
from app.connectors import (
    ActionReceipt,
    ActionReceiptStore,
    CapabilityAccess,
    CapabilityMetadata,
    CapabilityRequest,
    ConfirmationMode,
    ConnectorRegistry,
    ExecutionStatus,
    ReceiptStatus,
    VerificationMode,
    redact_secrets,
)
from app.home_assistant_connector import HomeAssistantConnector
from app.google_integration import (
    GOOGLE_MODEL_TOOL,
    GoogleConnector,
    GoogleOAuthConfig,
    GoogleOAuthService,
    google_model_tool,
)
from app.integration_accounts import CredentialCipher, IntegrationAccountStore
from app.openai_web_search import OpenAIWebSearchClient, SafeWebFetcher
from app.research_engine import ResearchEngine
from app.service_connectors import UNAVAILABLE_CONNECTOR_CATALOG
from app.web_connector import OpenAIWebSearchConnector, PublicWebFetchConnector


_PLAN_RISK = {
    "low": PlanRisk.LOW,
    "medium": PlanRisk.MODERATE,
    "high": PlanRisk.HIGH,
    "critical": PlanRisk.CRITICAL,
}

_EXTERNAL_SERVICE_WORDS = {
    "browser",
    "calendar",
    "contact",
    "contacts",
    "dating",
    "email",
    "facebook",
    "flight",
    "gmail",
    "hotel",
    "inbox",
    "instagram",
    "message",
    "monitor",
    "monitoring",
    "monitors",
    "product",
    "research",
    "shopping",
    "tiktok",
    "travel",
    "trip",
    "web",
    "watching",
}
_CURRENT_WEB_PHRASES = (
    "back in stock",
    "current price",
    "current weather",
    "exchange rate",
    "flight availability",
    "happening today",
    "how much is",
    "latest",
    "live price",
    "look online",
    "look up",
    "news today",
    "price of",
    "price drop",
    "price drops",
    "research",
    "right now",
    "search the web",
    "search online",
    "weather today",
    "website changes",
)
_EXTERNAL_REQUEST_PHRASES = (
    "am i free",
    "every morning",
    "find somewhere",
    "in my diary",
    "my schedule",
    "keep an eye",
    "let me know when",
    "sort me a weekend away",
    "tell me when",
    "what is on tomorrow",
    "what's on tomorrow",
)

MonitorCreator = Callable[
    [str, Mapping[str, Any], int, str | None],
    Awaitable[Mapping[str, Any]],
]
MonitorLookup = Callable[
    [str, str],
    Awaitable[Mapping[str, Any] | None],
]
MonitorLister = Callable[
    [str, str | None, int],
    Awaitable[Sequence[Mapping[str, Any]]],
]
MonitorCanceller = Callable[
    [str, str],
    Awaitable[Mapping[str, Any] | None],
]


class ConnectorPlannerExecutor:
    """Translate registry evidence into the planner's narrow executor contract."""

    def __init__(self, registry: ConnectorRegistry) -> None:
        self.registry = registry
        self._principal: ContextVar[str | None] = ContextVar(
            "jarvis_planner_principal",
            default=None,
        )

    def set_principal(self, principal_id: str | None):
        return self._principal.set(str(principal_id or "").strip() or None)

    def reset_principal(self, token: Any) -> None:
        self._principal.reset(token)

    @staticmethod
    def _principal_from_conversation(conversation_id: str) -> str | None:
        """Recover only Core's server-created ``usr:<owner>:`` namespace."""

        value = str(conversation_id or "")
        if not value.startswith("usr:"):
            return None
        _, separator, remainder = value.partition(":")
        principal, separator, _ = remainder.partition(":")
        if not separator or not principal or len(principal) > 64:
            return None
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
        return principal if all(character in allowed for character in principal) else None

    @classmethod
    def scope_conversation(cls, conversation_id: str, principal_id: str | None) -> str:
        conversation = str(conversation_id or "").strip()
        if not conversation:
            raise ValueError("A conversation is required")
        principal = str(principal_id or "").strip()
        if not principal:
            return conversation
        allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
        if len(principal) > 64 or any(character not in allowed for character in principal):
            raise ValueError("The integration account principal is malformed")
        existing = cls._principal_from_conversation(conversation)
        if conversation.startswith("usr:"):
            if existing != principal:
                raise ValueError("Conversation and integration account owners do not match")
            return conversation
        return f"usr:{principal}:{conversation}"

    async def snapshot(self) -> Mapping[str, CapabilityState]:
        live_rows = await self.registry.capability_snapshot(
            principal_id=self._principal.get(),
        )
        output: dict[str, CapabilityState] = {}
        for row in live_rows:
            access = str(row.get("access") or "read")
            capability_id = str(row["capability_id"])
            output[capability_id] = CapabilityState(
                capability_id=capability_id,
                available=bool(row.get("available")),
                healthy=bool(row.get("available")),
                readable=access == CapabilityAccess.READ.value,
                writable=access == CapabilityAccess.WRITE.value,
                requires_confirmation=(
                    str(row.get("confirmation") or "none") != ConfirmationMode.NONE.value
                ),
                supports_verification=(
                    access == CapabilityAccess.READ.value
                    or str(row.get("verification") or "none") != VerificationMode.NONE.value
                ),
                reason=(
                    str(row.get("unavailable_reason")) if row.get("unavailable_reason") else None
                ),
            )

        # Setup catalog entries are visible to planning so a requested service is
        # blocked with its actual setup reason.  They are never registered and
        # cannot execute.
        for entry in UNAVAILABLE_CONNECTOR_CATALOG.values():
            for potential in entry.setup.capabilities_after_setup:
                output.setdefault(
                    potential.capability_id,
                    CapabilityState(
                        capability_id=potential.capability_id,
                        available=False,
                        healthy=False,
                        readable=potential.access is CapabilityAccess.READ,
                        writable=potential.access is CapabilityAccess.WRITE,
                        requires_confirmation=potential.requires_confirmation,
                        supports_verification=potential.verification_supported,
                        reason=entry.reason,
                    ),
                )
        return output

    async def execute(self, request: PlanExecutionRequest) -> PlanExecutionResult:
        arguments = dict(request.arguments)
        connector_operation = str(arguments.pop("connector_operation", "")).strip()
        target = next(
            (
                arguments[key]
                for key in (
                    "target",
                    "entity_id",
                    "event_id",
                    "thread_id",
                    "draft_id",
                    "option_id",
                    "offer_id",
                    "url",
                )
                if key in arguments and arguments[key] not in (None, "")
            ),
            None,
        )
        execution = await self.registry.execute(
            CapabilityRequest(
                capability_id=request.capability_id,
                payload=arguments,
                request_id=request.action_id,
                conversation_id=request.conversation_id,
                principal_id=(
                    self._principal.get()
                    or self._principal_from_conversation(request.conversation_id)
                ),
                operation=connector_operation or None,
                target=target,
                confirmed=True,
                idempotency_key=request.idempotency_key,
            )
        )
        receipt = execution.receipt.as_dict() if execution.receipt is not None else None
        if execution.status is ExecutionStatus.OUTCOME_UNKNOWN:
            status = PlanExecutionStatus.OUTCOME_UNKNOWN
        elif execution.status is ExecutionStatus.ACCEPTED_UNVERIFIED:
            status = (
                PlanExecutionStatus.OUTCOME_UNKNOWN
                if request.access is PlanAccess.WRITE
                else PlanExecutionStatus.SUCCEEDED
            )
        elif execution.status in {
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.VERIFIED,
        }:
            status = PlanExecutionStatus.SUCCEEDED
        else:
            status = PlanExecutionStatus.FAILED
        return PlanExecutionResult(
            status=status,
            result=dict(execution.data),
            accepted=execution.accepted,
            verified=(execution.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.VERIFIED}),
            action_receipt=receipt,
            error_code=execution.status.value if not execution.accepted else None,
            error=execution.error,
            retryable=(
                request.access is PlanAccess.READ
                and execution.status in {ExecutionStatus.FAILED, ExecutionStatus.UNAVAILABLE}
            ),
        )

    async def reconcile(self, request: PlanExecutionRequest) -> PlanExecutionResult | None:
        """Recover a crash-window write only from an already durable receipt.

        Calling ``execute`` is safe after the lookup because the registry claims
        the same idempotency key first and returns the existing receipt without
        invoking the provider again.  An absent receipt is never treated as proof
        that an interrupted write did not begin.
        """

        if request.access is not PlanAccess.WRITE:
            return None
        store = self.registry.receipt_store
        if store is None:
            return None
        receipt = await store.get_by_idempotency_key(request.idempotency_key)
        if receipt is None:
            return None
        return await self.execute(request)


class ExternalAgentRuntime:
    """Live connector, research, receipt, and planning runtime for Core."""

    def __init__(
        self,
        *,
        api_key: str,
        web_model: str,
        web_enabled: bool,
        home_assistant: Any,
        tools: Any,
        admin: Any,
        external_enabled: bool = True,
        data_directory: str | Path = "/app/data",
        health_ttl_seconds: float = 60.0,
        connector_timeout_seconds: float = 45.0,
        credential_encryption_key: str = "",
        google_oauth_client_id: str = "",
        google_oauth_client_secret: str = "",
        google_oauth_redirect_uri: str = "",
        google_android_return_uri: str = "jarvis://integrations/google",
        web_search_client: OpenAIWebSearchClient | None = None,
        web_fetcher: SafeWebFetcher | None = None,
        monitor_creator: MonitorCreator | None = None,
        monitor_lookup: MonitorLookup | None = None,
        monitor_lister: MonitorLister | None = None,
        monitor_canceller: MonitorCanceller | None = None,
    ) -> None:
        data_path = Path(data_directory)
        self.enabled = bool(external_enabled)
        self._monitor_creator = monitor_creator
        self._monitor_lookup = monitor_lookup
        self._monitor_lister = monitor_lister
        self._monitor_canceller = monitor_canceller
        self._email_policies: Any | None = None
        self._monitor_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        self._monitor_locks_guard = asyncio.Lock()
        self.receipts = ActionReceiptStore(data_path / "jarvis_action_receipts.db")
        try:
            self.credential_cipher = CredentialCipher(credential_encryption_key)
        except ValueError:
            self.credential_cipher = CredentialCipher("")
        self.integration_accounts = IntegrationAccountStore(
            data_path / "jarvis_integration_accounts.db",
            self.credential_cipher,
        )
        self.registry = ConnectorRegistry(
            receipt_store=self.receipts,
            health_ttl_seconds=health_ttl_seconds,
            health_timeout_seconds=min(30.0, connector_timeout_seconds),
        )
        self.home_connector = self.registry.register(
            HomeAssistantConnector(client=home_assistant, tools=tools, admin=admin)
        )
        self.web_search_client = web_search_client or OpenAIWebSearchClient(
            api_key=api_key,
            model=web_model,
            enabled=(self.enabled and web_enabled),
            timeout_seconds=connector_timeout_seconds,
        )
        self.web_fetcher = web_fetcher or SafeWebFetcher(
            timeout_seconds=min(30.0, connector_timeout_seconds)
        )
        self.web_search_connector = OpenAIWebSearchConnector(search=self.web_search_client)
        self.registry.register(self.web_search_connector)
        self.web_fetch_connector = PublicWebFetchConnector(
            fetcher=self.web_fetcher,
            enabled=self.enabled,
        )
        self.registry.register(self.web_fetch_connector)
        self.google_oauth = GoogleOAuthService(
            config=GoogleOAuthConfig(
                client_id=google_oauth_client_id,
                client_secret=google_oauth_client_secret,
                redirect_uri=google_oauth_redirect_uri,
                android_return_uri=google_android_return_uri,
            ),
            accounts=self.integration_accounts,
            cipher=self.credential_cipher,
            timeout_seconds=connector_timeout_seconds,
        )
        self.google_connector = GoogleConnector(
            oauth=self.google_oauth,
            accounts=self.integration_accounts,
            timeout_seconds=connector_timeout_seconds,
        )
        self.registry.register(self.google_connector)
        self.planner_executor = ConnectorPlannerExecutor(self.registry)
        self.plans = SQLitePlanStore(data_path / "jarvis_agent_plans.db")
        self.planner = PersonalAgentPlanner(self.plans, self.planner_executor)
        self.research = ResearchEngine(
            self._research_search,
            fetch=self._research_fetch,
            conflict_analyzer=self._research_conflicts,
            provider_id="openai_web_search",
            timeout_seconds=connector_timeout_seconds,
            max_concurrency=4,
        )

    async def initialize(self) -> dict[str, Any]:
        await self.integration_accounts.initialize()
        await self.receipts.initialize()
        recovered = await self.receipts.recover_stale(older_than_seconds=300)
        health = await self.registry.health_snapshot(refresh=True)
        database = await self.database_health_snapshot()
        return {
            "recovered_stale_actions": recovered,
            "connectors": health,
            "database": database,
        }

    async def aclose(self) -> None:
        await asyncio.gather(
            self.web_search_connector.aclose(),
            self.web_fetch_connector.aclose(),
            self.google_connector.aclose(),
            self.google_oauth.aclose(),
        )

    def set_monitor_creator(
        self,
        creator: MonitorCreator,
        *,
        lookup: MonitorLookup | None = None,
        lister: MonitorLister | None = None,
        canceller: MonitorCanceller | None = None,
    ) -> None:
        self._monitor_creator = creator
        self._monitor_lookup = lookup
        self._monitor_lister = lister
        self._monitor_canceller = canceller

    def set_email_policy_engine(self, engine: Any) -> None:
        """Attach the durable email-policy service after composition."""

        self._email_policies = engine

    async def providers_snapshot(
        self,
        *,
        refresh: bool = False,
        principal_id: str | None = None,
    ) -> list[dict[str, Any]]:
        providers = await self.registry.status_snapshot(
            refresh=refresh,
            principal_id=principal_id,
        )
        registered = {str(item["provider_id"]) for item in providers}
        registered_capabilities = {
            item.capability_id for item in self.registry.potential_capabilities()
        }
        for entry in UNAVAILABLE_CONNECTOR_CATALOG.values():
            if entry.provider_id in registered:
                continue
            catalog_capabilities = {
                item.capability_id for item in entry.setup.capabilities_after_setup
            }
            if catalog_capabilities & registered_capabilities:
                continue
            providers.append(
                {
                    "provider_id": entry.provider_id,
                    "name": entry.name,
                    "configured": False,
                    "authenticated": False,
                    "healthy": False,
                    "available": False,
                    "health_reason": entry.reason,
                    "setup_requirements": [entry.setup.summary],
                    "setup": entry.setup.to_redacted_dict(),
                    "scopes": list(entry.setup.scopes),
                    "potential_capabilities": [
                        item.capability_id for item in entry.setup.capabilities_after_setup
                    ],
                    "executable_capabilities": [],
                    "checked_at": None,
                }
            )
        return sorted(providers, key=lambda item: str(item["provider_id"]))

    async def capability_snapshot(
        self,
        *,
        refresh: bool = False,
        principal_id: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = await self.registry.capability_snapshot(
            refresh=refresh,
            principal_id=principal_id,
        )
        known = {str(row["capability_id"]) for row in rows}
        for entry in UNAVAILABLE_CONNECTOR_CATALOG.values():
            for potential in entry.setup.capabilities_after_setup:
                if potential.capability_id in known:
                    continue
                rows.append(
                    {
                        **potential.to_dict(),
                        "provider_id": entry.provider_id,
                        "name": potential.capability_id,
                        "available": False,
                        "unavailable_reason": entry.reason,
                        "setup_only": True,
                    }
                )
                known.add(potential.capability_id)
        return sorted(rows, key=lambda row: str(row["capability_id"]))

    async def mobile_integrations_snapshot(
        self,
        *,
        principal_id: str,
        refresh: bool = True,
    ) -> list[dict[str, Any]]:
        """Return the Android account catalogue with redacted, live-derived states."""

        principal = str(principal_id or "").strip()
        if not principal:
            raise ValueError("An integration account owner is required")
        statuses = {
            str(item["provider_id"]): item
            for item in await self.providers_snapshot(
                refresh=refresh,
                principal_id=principal,
            )
        }
        google = statuses.get("google") or {}
        google_capabilities = set(google.get("executable_capabilities") or ())
        account = await self.google_connector.account_status(principal)
        google_service_health = self.google_connector.service_health(principal)
        credential_status = await self.google_connector.credential_status(principal)

        def account_state(
            provider_id: str,
            name: str,
            capability_prefix: str,
        ) -> dict[str, Any]:
            potential = {
                item.capability_id
                for item in self.google_connector.capabilities
                if item.capability_id.startswith(capability_prefix + ".")
            }
            available = potential & google_capabilities
            google_healthy = bool(google.get("available"))
            available_health = google_healthy and bool(available)
            service = google_service_health.get(capability_prefix) or {}
            if google_healthy and available == potential:
                state = "Connected"
            elif google_healthy and available:
                state = "Partial permissions"
            elif account is not None and account.reauthorization_required:
                state = "Reconnect required"
            elif service.get("granted") and not service.get("healthy"):
                state = "Provider unavailable"
            elif google.get("configured") and google.get("authenticated"):
                state = "Permission required"
            elif google.get("configured"):
                state = "Not connected"
            else:
                state = "Setup required"
            return {
                "provider_id": provider_id,
                "name": name,
                "state": state,
                "connected": available_health,
                "healthy": available_health,
                "granted_capabilities": sorted(available),
                "missing_capabilities": sorted(potential - available),
                "setup_requirements": list(google.get("setup_requirements") or ()),
                "health_reason": service.get("reason") or google.get("health_reason"),
            }

        if google.get("available"):
            all_google = {item.capability_id for item in self.google_connector.capabilities}
            google_state = (
                "Connected" if google_capabilities == all_google else "Partial permissions"
            )
        elif account is not None and account.reauthorization_required:
            google_state = "Reconnect required"
        elif google.get("authenticated") and not google.get("healthy"):
            google_state = "Provider unavailable"
        elif google.get("configured"):
            google_state = "Not connected"
        else:
            google_state = "Setup required"
        rows: list[dict[str, Any]] = [
            {
                "provider_id": "google",
                "name": "Google",
                "state": google_state,
                "connected": bool(google.get("available")),
                "healthy": bool(google.get("available")),
                "account": account.as_dict() if account is not None else None,
                "credential_status": credential_status,
                "granted_scopes": list(google.get("scopes") or ()),
                "granted_capabilities": sorted(google_capabilities),
                "setup_requirements": list(google.get("setup_requirements") or ()),
                "health_reason": google.get("health_reason"),
                "can_connect": self.google_oauth.configured,
                "can_reconnect": bool(account is not None),
                "can_disconnect": bool(account is not None),
            },
            account_state("gmail", "Gmail", "gmail"),
            account_state("calendar", "Calendar", "calendar"),
            account_state("contacts", "Contacts", "contacts"),
        ]

        def provider_row(provider_id: str, name: str) -> dict[str, Any]:
            status = statuses.get(provider_id) or {}
            connected = bool(status.get("available"))
            if connected:
                state = "Connected"
            elif status.get("configured") and status.get("authenticated"):
                state = "Provider unavailable"
            elif status.get("configured"):
                state = "Reconnect required"
            else:
                state = "Setup required"
            return {
                "provider_id": provider_id,
                "name": name,
                "state": state,
                "connected": connected,
                "healthy": connected,
                "setup_requirements": list(status.get("setup_requirements") or ()),
                "health_reason": status.get("health_reason"),
            }

        web_statuses = [
            statuses.get("openai_web_search") or {},
            statuses.get("public_web_fetch") or {},
        ]
        web_connected = any(bool(item.get("available")) for item in web_statuses)
        rows.extend(
            [
                {
                    "provider_id": "microsoft",
                    "name": "Microsoft",
                    "state": "Setup required",
                    "connected": False,
                    "healthy": False,
                    "setup_requirements": [
                        "A supported Microsoft OAuth connector is not configured"
                    ],
                },
                {
                    "provider_id": "web",
                    "name": "Web",
                    "state": "Connected" if web_connected else "Setup required",
                    "connected": web_connected,
                    "healthy": web_connected,
                    "setup_requirements": []
                    if web_connected
                    else ["Configure an available web search or fetch provider"],
                },
                provider_row("homeassistant", "Home Assistant"),
            ]
        )
        for provider_id, name in (
            ("instagram", "Instagram"),
            ("facebook", "Facebook"),
            ("tiktok", "TikTok"),
            ("x_social", "X"),
        ):
            rows.append(provider_row(provider_id, name))
        return rows

    async def health_snapshot(
        self,
        *,
        refresh: bool = False,
        principal_id: str | None = None,
    ) -> dict[str, Any]:
        core = await self.registry.health_snapshot(
            refresh=refresh,
            principal_id=principal_id,
        )
        database = await self.database_health_snapshot()
        return {
            **core,
            "healthy": bool(core.get("healthy")) and database["healthy"],
            "database": database,
            "providers": await self.providers_snapshot(
                refresh=False,
                principal_id=principal_id,
            ),
        }

    @staticmethod
    def _probe_database_sync(path: Path, table: str) -> bool:
        with sqlite3.connect(path, timeout=5) as connection:
            quick_check = connection.execute("PRAGMA quick_check(1)").fetchone()
            if quick_check is None or str(quick_check[0]).casefold() != "ok":
                return False
            schema = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
        return schema is not None

    async def database_health_snapshot(self) -> dict[str, Any]:
        """Probe only the two durable stores owned by this runtime."""

        await self.receipts.initialize()
        stores = {
            "action_receipts": (self.receipts.path, "connector_action_receipts"),
            "agent_plans": (self.plans.database_path, "agent_plans"),
            "integration_accounts": (
                self.integration_accounts.path,
                "integration_accounts",
            ),
        }

        async def probe(path: Path, table: str) -> dict[str, Any]:
            try:
                healthy = await asyncio.to_thread(self._probe_database_sync, path, table)
            except Exception:
                healthy = False
            return {
                "healthy": healthy,
                "reason": None if healthy else "Durable database probe failed",
            }

        results = await asyncio.gather(*(probe(path, table) for path, table in stores.values()))
        snapshot = dict(zip(stores, results, strict=True))
        return {
            "healthy": all(item["healthy"] for item in snapshot.values()),
            "stores": snapshot,
        }

    async def execute(
        self,
        capability_id: str,
        payload: Mapping[str, Any],
        *,
        operation: str | None = None,
        conversation_id: str | None = None,
        principal_id: str | None = None,
        request_id: str | None = None,
        confirmed: bool = False,
        standing_permission: bool = False,
        idempotency_key: str | None = None,
        target: Any = None,
    ) -> dict[str, Any]:
        execution = await self.registry.execute(
            capability_id,
            dict(payload),
            operation=operation,
            conversation_id=conversation_id,
            principal_id=principal_id,
            request_id=request_id or str(uuid.uuid4()),
            confirmed=confirmed,
            standing_permission=standing_permission,
            idempotency_key=idempotency_key,
            target=target,
        )
        return execution.as_dict()

    @staticmethod
    def _recent_gmail_context(
        history: Sequence[Mapping[str, str]],
    ) -> bool:
        """Return whether the immediately preceding exchange is about email.

        Older Gmail mentions cannot safely bind a shorthand referent after the
        conversation has moved on to another subject.  Limit context to the last
        user/assistant exchange so commands such as ``Delete it`` cannot target
        Gmail merely because email appeared somewhere in the history window.
        """

        conversational_items = [
            item
            for item in history
            if str(item.get("role") or "").casefold() in {"user", "assistant"}
        ]
        for item in conversational_items[-2:]:
            content = f" {str(item.get('content') or '').casefold()} "
            if (
                " gmail " in content
                or " email " in content
                or " inbox " in content
                or "@" in content
            ):
                return True

        return False

    @staticmethod
    def _current_gmail_context(text: str) -> bool:
        """Return whether the current request explicitly identifies Gmail/email."""

        lowered = " ".join(str(text or "").casefold().split())
        words = {word.strip(".,!?()[]{}:;\\\"'") for word in lowered.split()}
        return bool(
            words
            & {
                "email",
                "emails",
                "gmail",
                "inbox",
                "mail",
            }
        )

    @staticmethod
    def _gmail_priority_intent(text: str) -> bool:
        value = " ".join(str(text or "").casefold().split())
        return bool(
            re.search(
                r"\b(?:prioriti[sz]e|priority|urgent|needs? my attention|"
                r"need(?:s)? attention|important emails?|anything important|ignore for now)\b",
                value,
            )
        )

    @classmethod
    def _current_gmail_service_request(cls, text: str) -> bool:
        """Distinguish mailbox requests from generic uses of the word email."""

        if not cls._current_gmail_context(text):
            return False
        value = " ".join(str(text or "").casefold().split())
        words = {word.strip(".,!?()[]{}:;\\\"'") for word in value.split()}
        if words & {"gmail", "inbox", "emails", "mail"}:
            return True
        if value.startswith("email "):
            return True
        generic_web_subject = bool(
            re.search(
                r"\b(?:research|standards?|technology|marketing|security|protocols?)\b",
                value,
            )
        )
        mailbox_language = bool(
            re.search(
                r"\b(?:my|check|read|show|find|latest|unread|received|sent|archive|"
                r"trash|delete|move|star|reply|forward|draft|send)\b",
                value,
            )
        )
        return mailbox_language or not generic_web_subject

    @staticmethod
    def _gmail_message_operation(text: str) -> str | None:
        """Return an explicit CURRENT-turn new-message operation, if any."""

        value = " ".join(
            "".join(
                character.casefold() if character.isalnum() else " "
                for character in str(text or "")[:5_000]
            ).split()
        )
        words = value.split()[:32]
        if not words:
            return None
        if words[0] == "please":
            words = words[1:]
        if len(words) >= 3 and words[0] in {"can", "could", "would", "will"}:
            if words[1] != "you":
                return None
            words = words[2:]
            if words and words[0] == "please":
                words = words[1:]
        elif len(words) >= 5 and words[:4] in (
            ["i", "want", "you", "to"],
            ["i", "need", "you", "to"],
        ):
            words = words[4:]
        if not words or words[0] in {"show", "tell", "what", "whether", "if"}:
            return None
        if words[0] in {"send", "email"}:
            return "send"
        if words[0] in {"draft", "compose", "write"} and "email" in words:
            return "draft"
        return None

    @classmethod
    def _new_gmail_message_operation(
        cls,
        text: str,
        history: Sequence[Mapping[str, str]] = (),
    ) -> str | None:
        """Recognise a new outgoing email without treating quoted intent as authority."""

        operation = cls._gmail_message_operation(text)
        natural_repeat = bool(
            operation is not None
            and re.search(
                r"\b(?:send|email)\s+(?:her|him|them)\b.*\b(?:another|again)\b",
                " ".join(str(text or "")[:5_000].casefold().split()),
            )
        )
        if operation is None or not (cls._current_gmail_context(text) or natural_repeat):
            return None
        value = " ".join(str(text or "").casefold().split())
        if re.search(r"\b(?:reply|forward)\b", value):
            return None
        return operation

    @staticmethod
    def _gmail_message_content_requested(text: str) -> bool:
        """Return whether the current command already supplies message content."""

        words = " ".join(
            "".join(
                character.casefold() if character.isalnum() or character in "'’" else " "
                for character in str(text or "")[:5_000]
            ).split()
        ).split()
        markers = {"about", "ask", "asking", "make", "say", "saying", "tell", "telling"}
        return any(index + 1 < len(words) for index, word in enumerate(words) if word in markers)

    @staticmethod
    def _gmail_message_body_slot(text: str) -> str | None:
        """Extract literal short message content for a deferred recipient slot."""

        value = str(text or "")[:5_000].strip().strip('“”"')
        lowered = value.casefold()
        candidates: list[tuple[int, str]] = []
        for marker in (" saying ", " say ", " telling ", " tell ", " asking ", " ask "):
            position = lowered.find(marker)
            if position >= 0:
                candidates.append((position, marker))
        if not candidates:
            return None
        position, marker = min(candidates)
        body = value[position + len(marker) :].strip().strip('“”"')
        if marker.strip() in {"tell", "telling"}:
            first, separator, remainder = body.partition(" ")
            if separator and first.casefold() in {"her", "him", "them"}:
                body = remainder.strip()
        return body or None

    @staticmethod
    def _gmail_recipient_reference(text: str) -> str | None:
        """Extract only the named/pronoun target phrase from a new-email command."""

        rendered = " ".join(
            "".join(
                character if character.isalnum() or character in "'’-" else " "
                for character in str(text or "")[:2_000]
            ).split()
        )
        words = rendered.split()
        lowered = [word.casefold() for word in words]
        if not words:
            return None

        content_markers = {
            "about",
            "ask",
            "asking",
            "say",
            "saying",
            "tell",
            "telling",
            "with",
        }
        boundary = next(
            (index for index, word in enumerate(lowered) if word in content_markers),
            len(words),
        )
        words = words[:boundary]
        lowered = lowered[:boundary]
        if not words:
            return None

        start: int | None = None
        end: int | None = None
        if "email" in lowered:
            email_index = lowered.index("email")
            for marker in ("to", "for"):
                try:
                    marker_index = lowered.index(marker, email_index + 1)
                except ValueError:
                    continue
                start = marker_index + 1
                break
            if start is None and lowered[0] == "email":
                start = 1
            if start is None and email_index > 1 and lowered[0] == "send":
                if "message" in lowered[1:email_index]:
                    end = lowered.index("message", 1, email_index)
                    while end > 1 and lowered[end - 1] in {"a", "an", "the", "another"}:
                        end -= 1
                else:
                    end = email_index
                    while end > 1 and lowered[end - 1] in {"a", "an", "the", "another"}:
                        end -= 1
                start = 1
        if start is None and "message" in lowered and lowered[0] == "send":
            message_index = lowered.index("message")
            if message_index > 1:
                start, end = 1, message_index
        if start is None and lowered[0] == "send" and "another" in lowered:
            another_index = lowered.index("another")
            if another_index > 1:
                start, end = 1, another_index
        if start is None:
            return None

        stop_words = {
            "again",
            "and",
            "about",
            "make",
            "say",
            "saying",
            "subject",
            "tell",
            "telling",
            "that",
            "with",
        }
        selected: list[str] = []
        for word in words[start:end]:
            if word.casefold() in stop_words:
                break
            selected.append(word)
            if len(selected) >= 6:
                break
        reference = " ".join(selected).strip(" -'’")
        return reference or None

    @classmethod
    def _current_gmail_recipient_emails(cls, text: str) -> frozenset[str]:
        """Extract literal addresses only from the command's recipient clause."""

        value = str(text or "")[:5_000]
        lowered = value.casefold()
        cut = len(value)
        for marker in (
            " saying ",
            " say ",
            " asking ",
            " ask ",
            " telling ",
            " tell ",
            " about ",
            " with the body ",
            " with body ",
        ):
            position = lowered.find(marker)
            if position >= 0:
                cut = min(cut, position)
        return cls._literal_user_emails(value[:cut])

    @staticmethod
    def _gmail_briefing_intent(text: str) -> bool:
        value = " ".join(str(text or "").casefold().split())
        return bool(re.search(r"\b(?:inbox|email|gmail) briefing\b", value))

    @staticmethod
    def _email_retention_intent(text: str) -> bool:
        value = " ".join(str(text or "").casefold().split())
        return bool(
            re.search(
                r"\b(?:email|emails|gmail|inbox)\b.*\b(?:older than|after)\b.*\bdays?\b|"
                r"\b(?:pause|resume|change|disable|list|show)\b.*"
                r"\b(?:email|gmail|inbox) retention\b",
                value,
            )
        )

    @staticmethod
    def _reply_monitor_intent(text: str) -> bool:
        value = " ".join(str(text or "").casefold().split())
        return bool(
            re.search(
                r"\b(?:monitor|watch|notify me|let me know|tell me when|keep an eye)\b"
                r".{0,80}\b(?:reply|replies|response|responds|heard back)\b",
                value,
            )
        )

    @staticmethod
    def _important_email_monitor_intent(text: str) -> bool:
        value = " ".join(str(text or "").casefold().split())
        return bool(
            re.search(
                r"\b(?:monitor|watch|notify me|let me know|tell me|alert me|keep an eye)\b"
                r".{0,80}\b(?:important|urgent|needs? (?:my )?attention)\b"
                r".{0,40}\b(?:email|emails|gmail|inbox)\b|"
                r"\b(?:important|urgent)\b.{0,40}\b(?:email|emails)\b"
                r".{0,80}\b(?:notify|alert|monitor|watch)\b",
                value,
            )
        )

    @classmethod
    def _gmail_reply_read_intent(cls, text: str) -> bool:
        """Recognise a current reply-status question without granting reply authority."""

        if cls._write_authorized("gmail.reply", text):
            return False
        value = " ".join(str(text or "").casefold().split())
        return bool(
            re.search(
                r"\b(?:have i (?:got|received)|have we (?:got|received)|do i have|"
                r"did [a-z0-9@._+' -]{1,60}|has [a-z0-9@._+' -]{1,60})\b"
                r".{0,60}\b(?:reply|replies|replied|response|responded|answered)\b|"
                r"\b(?:received|got) (?:any |a )?(?:reply|response)\b|"
                r"\b(?:any|a) (?:reply|replies|response)\b|"
                r"\bdid i get (?:a|any|the)?\s*(?:reply|response)\b|"
                r"\bwhat did (?:she|he|they|[a-z][a-z'’-]{1,60}) say\b|"
                r"\bheard back\b",
                value,
            )
        )

    @classmethod
    def _contextual_gmail_read(
        cls,
        text: str,
        history: Sequence[Mapping[str, str]],
    ) -> bool:
        if not cls._recent_gmail_context(history):
            return False
        value = " ".join(str(text or "").casefold().split())
        return cls._contextual_gmail_follow_up(text, history) or bool(
            re.search(
                r"\b(?:read (?:it|that|this)|summari[sz]e (?:it|that|this)|"
                r"what did (?:she|he|they) say|anything urgent|"
                r"what (?:needs?|need) (?:my )?attention|"
                r"what can i ignore|prioriti[sz]e (?:it|them|those))\b",
                value,
            )
        )

    @classmethod
    def _gmail_write_capabilities(cls, text: str) -> tuple[str, ...]:
        """Return every Gmail write authorized by current immutable text."""

        capabilities = (
            "gmail.draft",
            "gmail.reply",
            "gmail.send",
            "gmail.forward",
            "gmail.archive",
            "gmail.mark_read",
            "gmail.mark_unread",
            "gmail.star",
            "gmail.unstar",
            "gmail.mark_important",
            "gmail.mark_not_important",
            "gmail.move",
            "gmail.trash",
            "gmail.restore",
        )
        return tuple(
            capability_id
            for capability_id in capabilities
            if cls._write_authorized(capability_id, text)
        )

    @classmethod
    def _gmail_management_write_capabilities(
        cls,
        text: str,
    ) -> tuple[str, ...]:
        """Return mailbox writes explicitly authorized by CURRENT text only."""

        management = {
            "gmail.reply",
            "gmail.archive",
            "gmail.mark_read",
            "gmail.mark_unread",
            "gmail.star",
            "gmail.unstar",
            "gmail.mark_important",
            "gmail.mark_not_important",
            "gmail.move",
            "gmail.trash",
            "gmail.restore",
        }
        return tuple(
            capability_id
            for capability_id in cls._gmail_write_capabilities(text)
            if capability_id in management
        )

    @classmethod
    def _contextual_gmail_management_capabilities(
        cls,
        text: str,
        history: Sequence[Mapping[str, str]],
    ) -> tuple[str, ...]:
        """Use history only to establish Gmail context, never write authority."""

        if not cls._recent_gmail_context(history):
            return ()
        return cls._gmail_management_write_capabilities(text)

    @classmethod
    def _gmail_write_context_authorized(
        cls,
        capability_id: str,
        user_text: str,
        history: Sequence[Mapping[str, str]] = (),
    ) -> bool:
        """Require current Gmail context or a recent Gmail referent for shorthand."""

        mailbox_writes = {
            "gmail.reply",
            "gmail.archive",
            "gmail.mark_read",
            "gmail.mark_unread",
            "gmail.star",
            "gmail.unstar",
            "gmail.mark_important",
            "gmail.mark_not_important",
            "gmail.move",
            "gmail.trash",
            "gmail.restore",
        }

        if capability_id not in mailbox_writes:
            return True

        if cls._current_gmail_context(user_text):
            return True

        if not cls._recent_gmail_context(history):
            return False

        return capability_id in cls._gmail_management_write_capabilities(user_text)

    @classmethod
    def _contextual_gmail_follow_up(
        cls,
        text: str,
        history: Sequence[Mapping[str, str]],
    ) -> bool:
        """Recognise a read-only Gmail follow-up from recent conversation."""

        if not cls._recent_gmail_context(history):
            return False

        # A current reply command is a write request, not a reply-status query.
        if cls._write_authorized("gmail.reply", text):
            return False

        value = " ".join(str(text or "").casefold().split())

        return bool(
            re.search(
                r"\b(?:reply|replies|replied|response|responded|answered)\b|"
                r"\bheard\s+back\b|"
                r"\bgot\s+back\s+to\s+(?:me|us)\b|"
                r"\bwhat\s+did\s+(?:she|he|they|amber|aaron)\s+say\b",
                value,
            )
        )

    @classmethod
    def is_external_request(
        cls,
        text: str,
        history: Sequence[Mapping[str, str]] = (),
    ) -> bool:
        lowered = str(text or "").casefold()
        words = {word.strip(".,!?()[]{}:;\\\"'") for word in lowered.split()}

        return (
            bool(words & _EXTERNAL_SERVICE_WORDS)
            or cls._current_gmail_context(text)
            or cls._gmail_reply_read_intent(text)
            or cls._gmail_priority_intent(text)
            or cls._new_gmail_message_operation(text, history) is not None
            or any(
                phrase in lowered
                for phrase in (
                    *_CURRENT_WEB_PHRASES,
                    *_EXTERNAL_REQUEST_PHRASES,
                )
            )
            or cls._contextual_gmail_read(text, history)
            or bool(
                cls._contextual_gmail_management_capabilities(
                    text,
                    history,
                )
            )
        )

    @staticmethod
    def requires_live_web(text: str) -> bool:
        lowered = str(text or "").casefold()
        return any(phrase in lowered for phrase in _CURRENT_WEB_PHRASES) or (
            "happening" in lowered and "today" in lowered
        )

    async def model_context(
        self,
        text: str,
        *,
        principal_id: str | None = None,
        history: Sequence[Mapping[str, str]] = (),
    ) -> str | None:
        if not self.enabled or not self.is_external_request(text, history):
            return None
        providers = await self.providers_snapshot(principal_id=principal_id)
        lowered = str(text or "").casefold()
        relevance_aliases = {
            "gmail": ("email", "gmail", "inbox"),
            "calendar": ("calendar", "diary", "schedule", "free"),
            "contacts": ("contact", "email dave", "phone number"),
            "communication": ("message", "sms", "notification"),
            "instagram": ("instagram",),
            "facebook": ("facebook",),
            "tiktok": ("tiktok",),
            "x_social": ("twitter", " x "),
            "travel": ("travel", "trip", "weekend away"),
            "shopping": ("shopping", "product", "price"),
            "dating": ("dating", "profile", "match"),
            "browser": ("browser", "website", "page"),
        }
        lines = []
        for item in providers:
            state = "healthy" if item.get("available") else "unavailable"
            reason = str(item.get("health_reason") or "").strip()
            executable = item.get("executable_capabilities") or ()
            provider_id = str(item["provider_id"])
            relevant = item.get("available") or any(
                alias in f" {lowered} " for alias in relevance_aliases.get(provider_id, ())
            )
            capability_note = ""
            if relevant and executable:
                capability_note = " | executable: " + ", ".join(str(value) for value in executable)
            elif relevant and item.get("potential_capabilities"):
                capability_note = " | setup-only, not executable: " + ", ".join(
                    str(value) for value in item["potential_capabilities"]
                )
            lines.append(
                f"- {provider_id}: {state}" + (f" — {reason}" if reason else "") + capability_note
            )
        requirement = (
            " This request asks for current-world information: you must use "
            "web_search or deep_research and may not answer current claims from "
            "model memory."
            if self.requires_live_web(text)
            else ""
        )
        gmail_management = self._contextual_gmail_management_capabilities(
            text,
            history,
        )
        if gmail_management:
            gmail_requirement = (
                " This is a contextual Gmail mailbox action. Conversation "
                "history may identify the email referent but does not authorize "
                "the write. Resolve the exact live message or thread using Gmail "
                "read/search evidence before mutation; never invent message IDs "
                "or label IDs. If the referent is ambiguous, ask instead of "
                "guessing. The only mailbox writes authorized by the current "
                "request are: " + ", ".join(gmail_management) + "."
            )
        elif (
            self._contextual_gmail_read(text, history)
            or self._gmail_reply_read_intent(text)
            or self._gmail_priority_intent(text)
        ):
            gmail_requirement = (
                " This is a contextual Gmail follow-up/read. Use live Gmail search, thread "
                "or message evidence before answering. Never report mailbox state "
                "or a received reply from conversation memory alone."
            )
        else:
            gmail_requirement = ""
        return (
            "Live external-provider status for this turn follows. Setup-only "
            "providers are not capabilities and no action may be claimed without "
            "structured execution evidence. When live web/research evidence is "
            "used, cite the returned source titles and URLs and report conflicts "
            "or uncertainty rather than smoothing them over. Distinct URLs or "
            "hostnames do not by themselves prove independent publishers; do not "
            "call sources independent unless the evidence establishes that.\n"
            + "\n".join(lines)
            + requirement
            + gmail_requirement
        )

    async def unavailable_service_reply(
        self,
        text: str,
        *,
        principal_id: str | None = None,
        history: Sequence[Mapping[str, str]] = (),
    ) -> str | None:
        """Return a deterministic read/account limitation for explicit services."""

        lowered = f" {str(text or '').casefold()} "
        checks: tuple[tuple[str, str, tuple[str, ...]], ...] = (
            (
                "gmail",
                "Gmail",
                (
                    " my email ",
                    " check email ",
                    " find the email ",
                    " find an email ",
                    " that email ",
                    " send an email ",
                    " reply to the email ",
                    " reply to that email ",
                    " gmail ",
                    " inbox ",
                ),
            ),
            (
                "calendar",
                "Calendar",
                (
                    " calendar ",
                    " diary ",
                    " i'm free ",
                    " i am free ",
                    " my schedule ",
                    " am i free ",
                    " what's on tomorrow ",
                    " what is on tomorrow ",
                ),
            ),
            (
                "contacts",
                "Contacts",
                (" contact details ", " email address ", " phone number "),
            ),
            (
                "browser",
                "Browser automation",
                (
                    " browser click ",
                    " click the ",
                    " fill in ",
                    " log in ",
                    " login to ",
                    " upload ",
                    " download ",
                ),
            ),
            ("instagram", "Instagram", (" my instagram ", " instagram metrics ")),
            ("facebook", "Facebook", (" my facebook ", " facebook metrics ")),
            ("tiktok", "TikTok", (" my tiktok ", " tiktok metrics ")),
            ("x_social", "X", (" my twitter ", " my x account ")),
        )
        requested = [
            (provider_id, label)
            for provider_id, label, phrases in checks
            if any(phrase in lowered for phrase in phrases)
        ]
        if lowered.strip().startswith("email ") and ("gmail", "Gmail") not in requested:
            requested.insert(0, ("gmail", "Gmail"))

        if not requested and (
            self._contextual_gmail_read(text, history)
            or self._gmail_reply_read_intent(text)
            or self._gmail_priority_intent(text)
            or self._current_gmail_service_request(text)
            or self._contextual_gmail_management_capabilities(text, history)
        ):
            requested.append(("gmail", "Gmail"))

        if not requested:
            return None
        statuses = {
            str(item["provider_id"]): item
            for item in await self.providers_snapshot(principal_id=principal_id)
        }
        google = statuses.get("google")
        if google is not None:
            for provider_id in ("gmail", "calendar", "contacts"):
                statuses.setdefault(provider_id, google)
        for provider_id, label in requested:
            status = statuses.get(provider_id)
            if status is None or not status.get("available"):
                reason = str((status or {}).get("health_reason") or "No provider is configured")
                if (
                    provider_id == "gmail"
                    and self._new_gmail_message_operation(text, history) == "send"
                ):
                    if any(
                        marker in reason.casefold()
                        for marker in ("oauth", "token", "authentication", "reconnect")
                    ):
                        return "I can’t send that right now because Gmail needs reconnecting."
                    return "I can’t send that right now because Gmail is unavailable."
                return f"{label} is unavailable — {reason}."
        return None

    async def openai_tools(
        self,
        text: str,
        *,
        principal_id: str | None = None,
        history: Sequence[Mapping[str, str]] = (),
    ) -> list[dict[str, Any]]:
        if not self.enabled or not self.is_external_request(text, history):
            return []
        executable = {
            item.capability_id
            for item in await self.registry.executable_capabilities(
                principal_id=principal_id,
            )
        }
        lowered = str(text or "").casefold()
        direct_literal_email_send = "@" in lowered and "send" in lowered and "email" in lowered
        cancel_monitor_intent = any(
            phrase in lowered
            for phrase in (
                "cancel monitor",
                "cancel monitoring",
                "stop monitor",
                "stop monitoring",
                "stop watching",
            )
        )
        list_monitor_intent = any(
            phrase in lowered
            for phrase in (
                "active monitors",
                "list monitors",
                "monitor status",
                "monitoring status",
                "what are you monitoring",
                "what am i monitoring",
            )
        )
        monitor_management_intent = cancel_monitor_intent or list_monitor_intent
        retention_intent = self._email_retention_intent(text)
        briefing_intent = self._gmail_briefing_intent(text)
        reply_monitor_intent = self._reply_monitor_intent(text) and (
            self._current_gmail_context(text) or self._recent_gmail_context(history)
        )
        important_monitor_intent = self._important_email_monitor_intent(text)
        definitions: list[dict[str, Any]] = []

        google_executable = sorted(executable)

        contextual_read = self._contextual_gmail_read(
            text,
            history,
        )
        reply_read_intent = self._gmail_reply_read_intent(text)
        current_gmail_request = self._current_gmail_service_request(text)
        gmail_management = self._gmail_management_write_capabilities(text)
        gmail_writes = self._gmail_write_capabilities(text)
        gmail_message_operation = self._new_gmail_message_operation(text, history)
        gmail_context = self._current_gmail_context(text) or self._recent_gmail_context(history)
        domain_text = lowered
        for literal_email in self._literal_user_emails(text):
            domain_text = domain_text.replace(literal_email, " ")
        current_google_domains: set[str] = set()
        if any(term in domain_text for term in ("calendar", "diary")) or (
            not self._current_gmail_context(text)
            and any(term in domain_text for term in ("appointment", "schedule"))
        ):
            current_google_domains.add("calendar")
        if re.search(r"\bcontacts?\b|\baddress book\b", domain_text):
            current_google_domains.add("contacts")
        priority_read_intent = self._gmail_priority_intent(text) and not current_google_domains

        def include_explicit_google_domains(allowed: set[str]) -> set[str]:
            """Add only currently requested non-Gmail Google capabilities."""

            scoped = set(allowed)
            for capability_id in executable:
                metadata = self.registry.capability_definition(capability_id)
                if metadata is None or metadata.provider_id != "google":
                    continue
                domain = capability_id.partition(".")[0]
                if domain not in current_google_domains:
                    continue
                if metadata.access is CapabilityAccess.READ or self._write_authorized(
                    capability_id,
                    text,
                ):
                    scoped.add(capability_id)
            return scoped

        explicit_web_intent = bool(
            re.search(
                r"\b(?:web|website|internet|online|research|investigate)\b|https?://",
                lowered,
            )
        )
        narrow_gmail_intent = not explicit_web_intent and bool(
            contextual_read
            or reply_read_intent
            or priority_read_intent
            or current_gmail_request
            or gmail_management
            or retention_intent
            or reply_monitor_intent
            or important_monitor_intent
        )

        if gmail_message_operation is not None:
            # New-message writes use one deterministic target-resolution and
            # execution boundary. The model supplies content, never an address.
            google_executable = []
        elif (
            contextual_read
            or reply_read_intent
            or priority_read_intent
            or (current_gmail_request and not gmail_writes)
        ):
            if self._gmail_priority_intent(text):
                allowed_reads = {
                    "gmail.prioritize",
                    "gmail.read",
                    "gmail.thread",
                }
            elif self._gmail_briefing_intent(text):
                allowed_reads = {
                    "gmail.briefing",
                    "gmail.read",
                    "gmail.thread",
                }
            else:
                allowed_reads = {
                    "gmail.search",
                    "gmail.read",
                    "gmail.thread",
                }
                if "label" in lowered:
                    allowed_reads.add("gmail.labels")
            allowed_reads = include_explicit_google_domains(allowed_reads)
            google_executable = [
                capability_id
                for capability_id in google_executable
                if capability_id in allowed_reads
            ]
        elif gmail_management and gmail_context:
            allowed_gmail = {
                "gmail.search",
                "gmail.read",
                "gmail.thread",
                *gmail_management,
            }
            if "gmail.move" in gmail_management:
                allowed_gmail.add("gmail.labels")
            allowed_gmail = include_explicit_google_domains(allowed_gmail)

            google_executable = [
                capability_id
                for capability_id in google_executable
                if capability_id in allowed_gmail
            ]
        elif gmail_writes and self._current_gmail_context(text):
            allowed_google = {
                "gmail.search",
                "gmail.read",
                "gmail.thread",
                *gmail_writes,
            }
            if "gmail.move" in gmail_writes:
                allowed_google.add("gmail.labels")
            if "@" not in lowered and ({"gmail.draft", "gmail.forward"} & set(gmail_writes)):
                allowed_google.add("contacts.resolve")
            allowed_google = include_explicit_google_domains(allowed_google)
            google_executable = [
                capability_id
                for capability_id in google_executable
                if capability_id in allowed_google
            ]
        elif current_google_domains:
            allowed_google = include_explicit_google_domains(set())
            google_executable = [
                capability_id
                for capability_id in google_executable
                if capability_id in allowed_google
            ]
        else:
            google_executable = []

        reply_status_intent = reply_read_intent or self._contextual_gmail_follow_up(
            text,
            history,
        )
        google_tool = google_model_tool(google_executable)
        if (
            google_tool is not None
            and not reply_status_intent
            and not monitor_management_intent
            and not retention_intent
            and not reply_monitor_intent
            and not important_monitor_intent
            and not (briefing_intent and self._email_policies is not None)
        ):
            definitions.append(google_tool)
        required_message_capabilities = {"gmail.draft"}
        if gmail_message_operation == "send":
            required_message_capabilities.add("gmail.send")
        if gmail_message_operation is not None and required_message_capabilities <= executable:
            definitions.append(
                {
                    "type": "function",
                    "name": "prepare_gmail_message",
                    "description": (
                        "Create the new Gmail message requested in the CURRENT user turn. "
                        "Jarvis resolves the exact recipient independently from trusted "
                        "Contacts, dialogue or verified send evidence; never put an email "
                        "address in these arguments. The server decides from the immutable "
                        "current request whether to draft only or send, and returns a "
                        "clarification without writing when the recipient is ambiguous."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string", "minLength": 1, "maxLength": 1000},
                            "body": {"type": "string", "minLength": 1, "maxLength": 100000},
                        },
                        "required": ["subject", "body"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if briefing_intent and self._email_policies is not None:
            definitions.append(
                {
                    "type": "function",
                    "name": "get_email_briefing",
                    "description": (
                        "Read a live evidence-backed Gmail inbox briefing and persist its "
                        "verified observation watermark for the next briefing. This is read-only."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": ["string", "null"], "maxLength": 1000},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
                            },
                        },
                        "required": ["query", "limit"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if (
            reply_status_intent
            and "gmail.reply_status" in executable
            and not monitor_management_intent
            and not retention_intent
            and not reply_monitor_intent
        ):
            definitions.append(
                {
                    "type": "function",
                    "name": "check_recent_gmail_reply",
                    "description": (
                        "Check live Gmail for an inbound reply using exact verified gmail.send "
                        "evidence owned by this principal. A recipient may be grounded by current "
                        "conversation history or a unique Google Contacts read. If no receipt "
                        "matches an exact grounded recipient, use a bounded Sent-mail read. Return "
                        "ambiguity as a clarification and never guess. This is read-only."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if (
            reply_monitor_intent
            and "gmail.reply_status" in executable
            and (self._monitor_creator is not None or self._email_policies is not None)
        ):
            definitions.append(
                {
                    "type": "function",
                    "name": "create_recent_gmail_reply_monitor",
                    "description": (
                        "Persist a durable reply monitor anchored to the latest verified "
                        "Gmail send receipt in this same conversation. This never guesses "
                        "a thread or message ID."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "polling_interval_seconds": {
                                "type": "integer",
                                "minimum": 300,
                                "maximum": 2592000,
                            }
                        },
                        "required": ["polling_interval_seconds"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if (
            important_monitor_intent
            and "gmail.important_status" in executable
            and (self._monitor_creator is not None or self._email_policies is not None)
        ):
            definitions.append(
                {
                    "type": "function",
                    "name": "create_important_gmail_monitor",
                    "description": (
                        "Persist a continuous principal-scoped monitor for new Gmail "
                        "that bounded provider evidence says needs attention. It dedupes "
                        "by provider message IDs and does not mutate mail."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "polling_interval_seconds": {
                                "type": "integer",
                                "minimum": 300,
                                "maximum": 2592000,
                            }
                        },
                        "required": ["polling_interval_seconds"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if retention_intent and self._email_policies is not None:
            definitions.append(
                {
                    "type": "function",
                    "name": "manage_email_retention_policy",
                    "description": (
                        "Create, list, pause, resume, change, or disable the durable "
                        "Inbox-to-Gmail-Trash retention policy. Creation persists a "
                        "standing policy; it never permanently deletes mail."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "operation": {
                                "type": "string",
                                "enum": ["create", "list", "pause", "resume", "change", "disable"],
                            },
                            "policy_id": {"type": ["string", "null"]},
                            "retention_days": {"type": ["integer", "null"], "minimum": 1},
                        },
                        "required": ["operation", "policy_id", "retention_days"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if "web.search" in executable and not monitor_management_intent and not narrow_gmail_intent:
            definitions.append(
                {
                    "type": "function",
                    "name": "web_search",
                    "description": (
                        "Search the live web. Required for current/latest facts. "
                        "Returns provider evidence and source URLs."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "minLength": 1},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 12,
                            },
                        },
                        "required": ["query", "limit"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if "web.fetch" in executable and (
            not narrow_gmail_intent
            and (
                "http://" in lowered
                or "https://" in lowered
                or any(word in lowered for word in ("open the page", "fetch", "read this page"))
            )
        ):
            definitions.append(
                {
                    "type": "function",
                    "name": "web_fetch",
                    "description": "Fetch and extract text from one public HTTP(S) page.",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string", "minLength": 8}},
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if (
            "web.search" in executable
            and not narrow_gmail_intent
            and any(word in lowered for word in ("research", "investigate", "compare", "shortlist"))
        ):
            definitions.append(
                {
                    "type": "function",
                    "name": "deep_research",
                    "description": (
                        "Collect and cross-check multiple live web sources for a "
                        "research question. Returns provenance, conflicts and errors."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "minLength": 1},
                            "queries": {
                                "type": "array",
                                "items": {"type": "string", "minLength": 1},
                                "minItems": 1,
                                "maxItems": 5,
                            },
                        },
                        "required": ["question", "queries"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        monitor_capabilities = sorted(
            capability_id
            for capability_id in executable
            if (
                (metadata := self.registry.capability_definition(capability_id)) is not None
                and metadata.repeatable
            )
        )
        if (
            self._monitor_creator is not None
            and monitor_capabilities
            and not reply_monitor_intent
            and not important_monitor_intent
            and not cancel_monitor_intent
            and not list_monitor_intent
            and any(
                phrase in lowered
                for phrase in (
                    "back in stock",
                    "changes",
                    "every morning",
                    "keep an eye",
                    "let me know when",
                    "monitor",
                    "price drop",
                    "tell me when",
                    "watch this",
                )
            )
        ):
            provider_ids: set[str] = set()
            for capability_id in monitor_capabilities:
                metadata = self.registry.capability_definition(capability_id)
                if metadata is not None:
                    provider_ids.add(metadata.provider_id)
            providers = sorted(provider_ids)
            definitions.append(
                {
                    "type": "function",
                    "name": "create_external_monitor",
                    "description": (
                        "Capture a verified live baseline and create a durable "
                        "same-conversation monitor using one available read-only "
                        "repeatable capability. Notifications describe only the "
                        "observed transition. Ordered comparisons require a numeric "
                        "provider value; do not reinterpret page text as a price."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "provider": {"type": "string", "enum": providers},
                            "capability_id": {
                                "type": "string",
                                "enum": monitor_capabilities,
                            },
                            "query": {},
                            "operation": {},
                            "arguments": {"type": "object"},
                            "value_path": {"type": "string"},
                            "comparison": {
                                "type": "object",
                                "properties": {
                                    "operator": {
                                        "type": "string",
                                        "enum": [
                                            "changed",
                                            "decreased",
                                            "increased",
                                        ],
                                    }
                                },
                                "required": ["operator"],
                                "additionalProperties": False,
                            },
                            "polling_interval_seconds": {
                                "type": "integer",
                                "minimum": 300,
                                "maximum": 2_592_000,
                            },
                            "label": {"type": "string", "maxLength": 200},
                            "expires_at": {
                                "type": "string",
                                "description": (
                                    "Timezone-aware ISO-8601 deadline, only when the user "
                                    "explicitly requested a deadline."
                                ),
                            },
                            "notify_if_unchanged": {
                                "type": "boolean",
                                "description": (
                                    "True only for an explicit 'remind me if no change by' request."
                                ),
                            },
                        },
                        "required": [
                            "provider",
                            "capability_id",
                            "arguments",
                            "value_path",
                            "comparison",
                            "polling_interval_seconds",
                        ],
                        "additionalProperties": False,
                    },
                    "strict": False,
                }
            )
        if self._monitor_lister is not None and (cancel_monitor_intent or list_monitor_intent):
            definitions.append(
                {
                    "type": "function",
                    "name": "list_external_monitors",
                    "description": (
                        "List durable external monitors belonging to this "
                        "conversation without exposing stored observations."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "status": {
                                "type": ["string", "null"],
                                "enum": [
                                    "pending",
                                    "executing",
                                    "delivery_pending",
                                    "completed",
                                    "failed",
                                    "expired",
                                    "cancelled",
                                    None,
                                ],
                            }
                        },
                        "required": ["status"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if self._monitor_canceller is not None and cancel_monitor_intent:
            definitions.append(
                {
                    "type": "function",
                    "name": "cancel_external_monitor",
                    "description": (
                        "Cancel one external monitor by an ID returned for this same conversation."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"job_id": {"type": "string", "minLength": 1}},
                        "required": ["job_id"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                }
            )
        if (
            not retention_intent
            and gmail_message_operation is None
            and (
                not narrow_gmail_intent
                or (
                    bool(gmail_writes)
                    and "@" not in lowered
                    and "contacts.resolve" in google_executable
                )
            )
            and not direct_literal_email_send
            and (
                any(
                    phrase in lowered
                    for phrase in (
                        " and ",
                        "sort it",
                        "sort me",
                        "sort this",
                        "plan it",
                        "organise",
                    )
                )
                or (
                    google_tool is not None
                    and any(
                        word in lowered
                        for word in ("email", "gmail", "calendar", "appointment", "contact")
                    )
                )
            )
        ):
            definitions.append(self._planner_tool())
        return definitions

    @staticmethod
    def _planner_tool() -> dict[str, Any]:
        return {
            "type": "function",
            "name": "create_personal_plan",
            "description": (
                "Create and start a resumable multi-step personal-agent plan. "
                "Use only for genuine goals with two or more dependent steps."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "minLength": 1},
                    "steps": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 12,
                        "items": {
                            "type": "object",
                            "properties": {
                                "step_id": {"type": "string", "minLength": 1},
                                "title": {"type": "string", "minLength": 1},
                                "capability_id": {"type": "string", "minLength": 3},
                                "access": {"type": "string", "enum": ["read", "write"]},
                                "evidence": {
                                    "type": "string",
                                    "enum": ["accepted", "verified"],
                                },
                                "arguments": {
                                    "type": "object",
                                    "description": (
                                        "Provider arguments. A value may reference "
                                        "persisted evidence from an ancestor step as "
                                        '{"$from_step":"step_id","path":'
                                        '"field.0.child"}. Never guess a value that '
                                        "must come from an earlier provider result."
                                    ),
                                },
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "risk": {
                                    "type": "string",
                                    "enum": ["low", "moderate", "high", "critical"],
                                },
                                "requires_confirmation": {"type": "boolean"},
                                "max_attempts": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 3,
                                },
                            },
                            "required": [
                                "step_id",
                                "title",
                                "capability_id",
                                "access",
                                "evidence",
                                "arguments",
                                "depends_on",
                                "risk",
                                "requires_confirmation",
                                "max_attempts",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["goal", "steps"],
                "additionalProperties": False,
            },
            # Step arguments are provider-specific JSON, so this one schema is
            # deliberately non-strict. Every selected capability and argument
            # set is still validated by planner and registry code before use.
            "strict": False,
        }

    async def execute_model_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        conversation_id: str,
        principal_id: str,
        request_id: str | None = None,
        user_text: str = "",
        history: Sequence[Mapping[str, str]] = (),
        dialogue_focus: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name == GOOGLE_MODEL_TOOL:
            return await self._execute_google_model_tool(
                arguments,
                conversation_id=conversation_id,
                principal_id=principal_id,
                request_id=request_id,
                user_text=user_text,
                history=history,
            )
        if name == "prepare_gmail_message":
            return await self._prepare_gmail_message(
                arguments,
                conversation_id=conversation_id,
                principal_id=principal_id,
                request_id=request_id,
                user_text=user_text,
                history=history,
                dialogue_focus=dialogue_focus,
            )
        if name == "web_search":
            return await self.search(
                str(arguments.get("query") or ""),
                limit=int(arguments.get("limit") or 8),
            )
        if name == "web_fetch":
            return await self.fetch(str(arguments.get("url") or ""))
        if name == "deep_research":
            result = await self.deep_research(
                str(arguments.get("question") or ""),
                queries=tuple(str(item) for item in arguments.get("queries") or ()),
            )
            return {
                "success": bool(result.get("live_evidence_available")),
                "data": result,
                "error": (
                    None
                    if result.get("live_evidence_available")
                    else "No live research evidence was collected"
                ),
            }
        if name == "create_personal_plan":
            steps = arguments.get("steps") or ()
            if not isinstance(steps, Sequence) or isinstance(steps, (str, bytes)):
                raise ValueError("Plan steps must be an array")
            proposed_steps = [dict(item) for item in steps if isinstance(item, Mapping)]
            steps_by_id = {str(step.get("step_id") or ""): step for step in proposed_steps}
            for step in proposed_steps:
                capability_id = str(step.get("capability_id") or "")
                if str(step.get("access") or "read") == "write":
                    if not self._write_authorized(
                        capability_id,
                        user_text,
                    ):
                        raise ValueError(
                            f"The user's request did not explicitly authorize {capability_id}"
                        )
                    if not self._gmail_write_context_authorized(
                        capability_id,
                        user_text,
                        history,
                    ):
                        raise ValueError(
                            "The current request did not establish Gmail context "
                            f"for {capability_id}"
                        )
                step_arguments = step.get("arguments")
                payload = step_arguments if isinstance(step_arguments, Mapping) else {}
                if capability_id in {"gmail.draft", "gmail.forward"} and not (
                    self._plan_recipient_authorized(
                        payload.get("to"),
                        user_text=user_text,
                        steps_by_id=steps_by_id,
                    )
                ):
                    raise ValueError(
                        "A planned email recipient must be stated by the user or "
                        "come from an unambiguous Contacts resolve step"
                    )
                if capability_id in {"calendar.create", "calendar.update"}:
                    event_payload = payload
                    if capability_id == "calendar.update" and isinstance(
                        payload.get("changes"), Mapping
                    ):
                        event_payload = payload["changes"]
                    attendees = event_payload.get("attendees")
                    if attendees is not None:
                        if not isinstance(attendees, Sequence) or isinstance(
                            attendees, (str, bytes)
                        ):
                            raise ValueError("Calendar attendees must be an array")
                        if any(
                            not isinstance(attendee, Mapping)
                            or not self._plan_recipient_authorized(
                                attendee.get("email"),
                                user_text=user_text,
                                steps_by_id=steps_by_id,
                            )
                            for attendee in attendees
                        ):
                            raise ValueError(
                                "A planned attendee must be stated by the user or "
                                "come from an unambiguous Contacts resolve step"
                            )
            plan = await self.create_plan(
                conversation_id=conversation_id,
                principal_id=principal_id,
                goal=str(arguments.get("goal") or ""),
                steps=proposed_steps,
            )
            return {
                "success": True,
                "plan_created": True,
                "goal_completed": plan.get("status") == "completed",
                "data": {"plan": plan},
            }
        if name == "create_external_monitor":
            monitor_request_id = request_id
            if request_id:
                monitor_request_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{request_id}:"
                        + json.dumps(
                            dict(arguments),
                            sort_keys=True,
                            separators=(",", ":"),
                            default=str,
                        ),
                    )
                )
            return await self.create_external_monitor(
                conversation_id=conversation_id,
                principal_id=principal_id,
                provider=str(arguments.get("provider") or ""),
                capability_id=str(arguments.get("capability_id") or ""),
                query=arguments.get("query"),
                operation=arguments.get("operation"),
                arguments=(
                    dict(arguments["arguments"])
                    if isinstance(arguments.get("arguments"), Mapping)
                    else {}
                ),
                value_path=str(arguments.get("value_path") or "") or None,
                comparison=arguments.get("comparison", "changed"),
                polling_interval_seconds=int(arguments.get("polling_interval_seconds") or 3600),
                label=str(arguments.get("label") or "") or None,
                expires_at=str(arguments.get("expires_at") or "") or None,
                notify_if_unchanged=bool(arguments.get("notify_if_unchanged", False)),
                request_id=monitor_request_id,
            )
        if name == "list_external_monitors":
            return await self.list_external_monitors(
                conversation_id=conversation_id,
                status=(str(arguments.get("status") or "").strip() or None),
            )
        if name == "cancel_external_monitor":
            return await self.cancel_external_monitor(
                conversation_id=conversation_id,
                job_id=str(arguments.get("job_id") or ""),
            )
        if name == "manage_email_retention_policy":
            return await self._manage_email_retention_policy(
                arguments,
                conversation_id=conversation_id,
                principal_id=principal_id,
                request_id=request_id,
                user_text=user_text,
            )
        if name == "get_email_briefing":
            return await self._get_email_briefing(
                arguments,
                conversation_id=conversation_id,
                principal_id=principal_id,
            )
        if name == "check_recent_gmail_reply":
            return await self._check_recent_gmail_reply(
                conversation_id=conversation_id,
                principal_id=principal_id,
                user_text=user_text,
                history=history,
                dialogue_focus=dialogue_focus,
            )
        if name == "create_recent_gmail_reply_monitor":
            return await self._create_recent_gmail_reply_monitor(
                conversation_id=conversation_id,
                principal_id=principal_id,
                request_id=request_id,
                user_text=user_text,
                polling_interval_seconds=int(arguments.get("polling_interval_seconds") or 900),
            )
        if name == "create_important_gmail_monitor":
            return await self._create_important_gmail_monitor(
                conversation_id=conversation_id,
                principal_id=principal_id,
                request_id=request_id,
                user_text=user_text,
                polling_interval_seconds=int(arguments.get("polling_interval_seconds") or 900),
            )
        raise ValueError(f"Unsupported external agent tool: {name}")

    @staticmethod
    def _receipt_name_matches_address(name: str, address: str) -> bool:
        """Match a stated person to exact local-part tokens, never an address suffix."""

        name_terms = re.findall(r"[a-z]+", str(name or "").casefold())
        local = str(address or "").partition("@")[0]
        local_terms = re.findall(r"[a-z]+", local.casefold())
        return bool(name_terms and all(term in local_terms for term in name_terms))

    async def _resolve_gmail_message_recipient(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        user_text: str,
        history: Sequence[Mapping[str, str]],
        dialogue_focus: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Resolve one exact write target from trusted read evidence."""

        literal_recipients = self._current_gmail_recipient_emails(user_text)
        if len(literal_recipients) > 1:
            return {
                "resolved": False,
                "clarification_required": True,
                "clarification": "Which email address should I use?",
            }
        current_reference = self._gmail_recipient_reference(user_text)
        display_name = str(current_reference or "").strip()
        if len(literal_recipients) == 1:
            return {
                "resolved": True,
                "recipient": next(iter(literal_recipients)),
                "recipient_name": None,
                "source": "current_literal_address",
            }

        scoped_conversation = self.planner_executor.scope_conversation(
            conversation_id,
            principal_id,
        )
        owner_prefix = f"usr:{principal_id}:"
        same_conversation = await self.receipts.list_recent(
            limit=100,
            conversation_id=scoped_conversation,
        )
        recent = await self.receipts.list_recent(limit=500)

        def verified_receipt_addresses(receipts: Sequence[ActionReceipt]) -> frozenset[str]:
            addresses: set[str] = set()
            for receipt in receipts:
                if (
                    receipt.status is not ReceiptStatus.VERIFIED
                    or receipt.capability_id != "gmail.send"
                    or not str(receipt.conversation_id or "").startswith(owner_prefix)
                    or str(receipt.result.get("status") or "") != "sent"
                ):
                    continue
                try:
                    addresses.add(
                        GoogleConnector._recipient(
                            str(receipt.result.get("recipient") or "")
                        ).casefold()
                    )
                except ValueError:
                    continue
            return frozenset(addresses)

        owned_addresses = verified_receipt_addresses(recent)
        same_addresses = verified_receipt_addresses(same_conversation)

        pronouns = {"her", "him", "them", "it"}
        reference = display_name
        if reference.casefold() in pronouns:
            reference = ""

        if not reference:
            for focus_key in ("gmail_recipient", "gmail_reply"):
                raw_focus = (dialogue_focus or {}).get(focus_key)
                if not isinstance(raw_focus, Mapping):
                    continue
                try:
                    focused = GoogleConnector._recipient(
                        str(raw_focus.get("recipient") or "")
                    ).casefold()
                except ValueError:
                    continue
                trusted_focus = (
                    focus_key == "gmail_recipient"
                    and str(raw_focus.get("source") or "")
                    in {
                        "current_literal_address",
                        "google_contacts",
                        "principal_verified_send",
                        "grounded_dialogue_address",
                        "same_conversation_verified_send",
                        "conversation_focus",
                    }
                    and str(raw_focus.get("operation") or "") in {"draft", "send"}
                ) or (
                    focus_key == "gmail_reply"
                    and bool(raw_focus.get("sent_message_id"))
                    and bool(raw_focus.get("thread_id"))
                )
                if focused in owned_addresses or trusted_focus:
                    return {
                        "resolved": True,
                        "recipient": focused,
                        "recipient_name": str(raw_focus.get("recipient_name") or "").strip()
                        or None,
                        "source": "conversation_focus",
                    }

            for item in reversed(history[-12:]):
                content = str(item.get("content") or "")
                history_emails = self._literal_user_emails(content) & owned_addresses
                if len(history_emails) == 1:
                    return {
                        "resolved": True,
                        "recipient": next(iter(history_emails)),
                        "recipient_name": None,
                        "source": "grounded_dialogue_address",
                    }
                history_reference = self._gmail_recipient_reference(content)
                if not history_reference:
                    history_reference = self._reply_person_reference(content)
                if history_reference and history_reference.casefold() not in pronouns:
                    reference = history_reference
                    break

            if not reference and len(same_addresses) == 1:
                return {
                    "resolved": True,
                    "recipient": next(iter(same_addresses)),
                    "recipient_name": None,
                    "source": "same_conversation_verified_send",
                }

        if not reference:
            return {
                "resolved": False,
                "clarification_required": True,
                "clarification": "Who do you mean?",
            }

        contact_error: str | None = None
        contact_resolved_without_address = False
        contact_execution = await self.registry.execute(
            CapabilityRequest(
                capability_id="contacts.resolve",
                payload={"query": reference},
                request_id=str(uuid.uuid4()),
                conversation_id=scoped_conversation,
                principal_id=principal_id,
                operation="resolve_gmail_message_recipient",
                target=reference,
            ),
            refresh_health=True,
        )
        if contact_execution.success:
            contact_data = contact_execution.data
            if contact_data.get("ambiguous") is True:
                return {
                    "resolved": False,
                    "clarification_required": True,
                    "clarification": f"Which {reference} do you mean?",
                }
            contact = contact_data.get("contact")
            if contact_data.get("resolved") is True and isinstance(contact, Mapping):
                contact_addresses: set[str] = set()
                for item in contact.get("email_addresses") or ():
                    try:
                        contact_addresses.add(GoogleConnector._recipient(str(item)).casefold())
                    except ValueError:
                        continue
                if len(contact_addresses) == 1:
                    return {
                        "resolved": True,
                        "recipient": next(iter(contact_addresses)),
                        "recipient_name": reference,
                        "source": "google_contacts",
                    }
                if len(contact_addresses) > 1:
                    return {
                        "resolved": False,
                        "clarification_required": True,
                        "clarification": (
                            f"I’ve got more than one email address for {reference}. "
                            "Which one should I use?"
                        ),
                    }
                contact_resolved_without_address = True
        else:
            contact_error = str(contact_execution.error or "Google Contacts is unavailable")

        receipt_matches = {
            address
            for address in owned_addresses
            if self._receipt_name_matches_address(reference, address)
        }
        if len(receipt_matches) == 1:
            return {
                "resolved": True,
                "recipient": next(iter(receipt_matches)),
                "recipient_name": reference,
                "source": "principal_verified_send",
            }
        if len(receipt_matches) > 1:
            return {
                "resolved": False,
                "clarification_required": True,
                "clarification": f"Which {reference} do you mean?",
            }
        if contact_error:
            return {
                "resolved": False,
                "error": contact_error,
                "user_error": f"I couldn’t look up {reference}’s email address just now.",
            }
        if contact_resolved_without_address:
            return {
                "resolved": False,
                "clarification_required": True,
                "clarification": (
                    f"I know who you mean, but I don’t have an email address for {reference} yet."
                ),
            }
        return {
            "resolved": False,
            "clarification_required": True,
            "clarification": f"Which email address should I use for {reference}?",
        }

    async def _prepare_gmail_message(
        self,
        arguments: Mapping[str, Any],
        *,
        conversation_id: str,
        principal_id: str,
        request_id: str | None,
        user_text: str,
        history: Sequence[Mapping[str, str]],
        dialogue_focus: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Resolve a target, create a verified draft and send only when authorised now."""

        operation = self._new_gmail_message_operation(user_text, history)
        if operation not in {"draft", "send"}:
            raise ValueError("The current request did not authorize a new Gmail message")
        if not self._write_authorized("gmail.draft", user_text) or (
            operation == "send" and not self._write_authorized("gmail.send", user_text)
        ):
            raise ValueError("The current request did not authorize this Gmail write")

        subject = str(arguments.get("subject") or "").strip()
        body = str(arguments.get("body") or "").strip()
        if not subject or not body:
            raise ValueError("A subject and body are required")
        if len(subject) > 1_000 or len(body) > 100_000:
            raise ValueError("The email content is too long")

        resolution = await self._resolve_gmail_message_recipient(
            conversation_id=conversation_id,
            principal_id=principal_id,
            user_text=user_text,
            history=history,
            dialogue_focus=dialogue_focus,
        )
        if resolution.get("resolved") is not True:
            return {
                "handled": True,
                "success": resolution.get("clarification_required") is True,
                "write_executed": False,
                "operation": operation,
                "subject": subject,
                "body": body,
                **resolution,
            }

        return await self.execute_resolved_gmail_message(
            operation=operation,
            authorization_text=user_text,
            recipient=str(resolution["recipient"]),
            recipient_name=str(resolution.get("recipient_name") or "").strip() or None,
            recipient_source=str(resolution.get("source") or "pending_exact_target"),
            subject=subject,
            body=body,
            conversation_id=conversation_id,
            principal_id=principal_id,
            request_id=request_id,
        )

    async def execute_resolved_gmail_message(
        self,
        *,
        operation: str,
        authorization_text: str,
        recipient: str,
        recipient_name: str | None,
        recipient_source: str,
        subject: str,
        body: str,
        conversation_id: str,
        principal_id: str,
        request_id: str | None,
    ) -> dict[str, Any]:
        """Execute a server-resolved target under preserved current-turn authority."""

        authorised_operation = self._gmail_message_operation(authorization_text)
        if operation not in {"draft", "send"} or authorised_operation != operation:
            raise ValueError("The original request did not authorize this Gmail message")
        if not self._write_authorized("gmail.draft", authorization_text) or (
            operation == "send" and not self._write_authorized("gmail.send", authorization_text)
        ):
            raise ValueError("The original request did not authorize this Gmail write")
        canonical_recipient = GoogleConnector._recipient(recipient).casefold()
        subject = str(subject or "").strip()
        body = str(body or "").strip()
        if not subject or not body:
            raise ValueError("A subject and body are required")
        if len(subject) > 1_000 or len(body) > 100_000:
            raise ValueError("The email content is too long")

        scoped_conversation = self.planner_executor.scope_conversation(
            conversation_id,
            principal_id,
        )
        base_request_id = str(request_id or uuid.uuid4())

        def child_id(stage: str) -> str:
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{base_request_id}:gmail:{stage}"))

        draft_request_id = child_id("draft")
        draft_execution = await self.registry.execute(
            CapabilityRequest(
                capability_id="gmail.draft",
                payload={"to": canonical_recipient, "subject": subject, "body": body},
                request_id=draft_request_id,
                conversation_id=scoped_conversation,
                principal_id=principal_id,
                operation="compose_new_email",
                target=canonical_recipient,
                confirmed=True,
                idempotency_key=draft_request_id,
            ),
            refresh_health=True,
        )
        draft_receipt = (
            draft_execution.receipt.as_dict() if draft_execution.receipt is not None else None
        )
        draft_verified = (
            draft_execution.status is ExecutionStatus.VERIFIED
            and isinstance(draft_receipt, Mapping)
            and draft_receipt.get("status") == ReceiptStatus.VERIFIED.value
        )
        base_result = {
            "handled": True,
            "operation": operation,
            "recipient": canonical_recipient,
            "recipient_name": recipient_name,
            "recipient_source": recipient_source,
            "subject": subject,
        }
        if not draft_verified:
            return {
                **base_result,
                "success": False,
                "status": draft_execution.status.value,
                "error": draft_execution.error or "Gmail did not verify the draft",
                "receipt": draft_receipt,
            }
        draft_id = str(draft_execution.data.get("draft_id") or "").strip()
        if not draft_id:
            return {
                **base_result,
                "success": False,
                "status": "accepted_unverified",
                "error": "Gmail did not return a verified draft identifier",
                "receipt": draft_receipt,
            }
        if operation == "draft":
            return {
                **base_result,
                "success": True,
                "status": "verified",
                "drafted": True,
                "receipt": draft_receipt,
            }

        send_request_id = child_id("send")
        send_execution = await self.registry.execute(
            CapabilityRequest(
                capability_id="gmail.send",
                payload={"draft_id": draft_id},
                request_id=send_request_id,
                conversation_id=scoped_conversation,
                principal_id=principal_id,
                operation="send_new_email",
                target=draft_id,
                confirmed=True,
                idempotency_key=send_request_id,
            ),
            refresh_health=True,
        )
        send_receipt = (
            send_execution.receipt.as_dict() if send_execution.receipt is not None else None
        )
        send_verified = (
            send_execution.status is ExecutionStatus.VERIFIED
            and isinstance(send_receipt, Mapping)
            and send_receipt.get("status") == ReceiptStatus.VERIFIED.value
        )
        if not send_verified:
            return {
                **base_result,
                "success": False,
                "status": send_execution.status.value,
                "drafted": True,
                "sent": False,
                "error": send_execution.error or "Gmail did not verify the send",
                "draft_receipt": draft_receipt,
                "receipt": send_receipt,
            }
        return {
            **base_result,
            "success": True,
            "status": "verified",
            "drafted": True,
            "sent": True,
            "draft_receipt": draft_receipt,
            "receipt": send_receipt,
            "provider_reference": send_execution.provider_reference,
        }

    async def _get_email_briefing(
        self,
        arguments: Mapping[str, Any],
        *,
        conversation_id: str,
        principal_id: str,
    ) -> dict[str, Any]:
        engine = self._email_policies
        if engine is None:
            raise RuntimeError("Durable email briefing is unavailable")
        return await engine.briefing(
            principal_id=principal_id,
            conversation_id=self.planner_executor.scope_conversation(
                conversation_id,
                principal_id,
            ),
            query=str(arguments.get("query") or "in:inbox"),
            limit=int(arguments.get("limit") or 50),
        )

    async def _verified_gmail_send_anchor(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        user_text: str,
    ) -> tuple[ActionReceipt, str, str] | None:
        scoped_conversation = self.planner_executor.scope_conversation(
            conversation_id,
            principal_id,
        )
        literal_recipients = self._literal_user_emails(user_text)
        same_conversation = await self.receipts.list_recent(
            limit=100,
            conversation_id=scoped_conversation,
        )
        recent = await self.receipts.list_recent(limit=500)
        owner_prefix = f"usr:{principal_id}:"
        other_owned = [
            receipt
            for receipt in recent
            if receipt.conversation_id != scoped_conversation
            and str(receipt.conversation_id or "").startswith(owner_prefix)
        ]

        def eligible(receipt: ActionReceipt) -> tuple[ActionReceipt, str] | None:
            result = receipt.result
            if (
                receipt.status is not ReceiptStatus.VERIFIED
                or receipt.capability_id != "gmail.send"
                or str(result.get("status") or "") != "sent"
                or not result.get("message_id")
                or not result.get("thread_id")
            ):
                return None
            try:
                recipient = GoogleConnector._recipient(
                    str(result.get("recipient") or "")
                ).casefold()
            except ValueError:
                return None
            if literal_recipients and recipient not in literal_recipients:
                return None
            return receipt, recipient

        for receipt in same_conversation:
            match = eligible(receipt)
            if match is not None:
                return match[0], match[1], "same_conversation_receipt"

        owned_matches = [match for receipt in other_owned if (match := eligible(receipt))]
        if literal_recipients and owned_matches:
            return owned_matches[0][0], owned_matches[0][1], "principal_durable_receipt"
        # Without an explicit recipient, a cross-conversation receipt is safe
        # only when it is the principal's sole possible sent referent.
        if len(owned_matches) == 1:
            return owned_matches[0][0], owned_matches[0][1], "principal_durable_receipt"
        return None

    @staticmethod
    def _reply_person_reference(text: str) -> str | None:
        """Extract an explicitly named reply referent without resolving identity."""

        value = " ".join(str(text or "").split())
        patterns = (
            r"\b(?:has|did)\s+([a-z][a-z'’-]{1,60})\s+(?:replied|reply|responded|respond)",
            r"\b(?:reply|response)\s+from\s+([a-z][a-z'’-]{1,60})\b",
            r"\bwhat\s+did\s+([a-z][a-z'’-]{1,60})\s+say\b",
            r"\bemail\s+(?:i|you|we)\s+(?:just\s+)?sent\s+to\s+"
            r"([a-z][a-z'’-]{1,60})\b",
        )
        excluded = {"any", "he", "her", "him", "i", "me", "she", "that", "they", "we", "you"}
        for pattern in patterns:
            match = re.search(pattern, value, re.I)
            if match and match.group(1).casefold() not in excluded:
                return match.group(1).strip().title()
        return None

    @classmethod
    def _history_reply_referent(
        cls,
        history: Sequence[Mapping[str, str]],
    ) -> tuple[frozenset[str], str | None]:
        """Resolve read-only recipient hints from recent dialogue evidence."""

        for item in reversed(history):
            content = str(item.get("content") or "")
            emails = cls._literal_user_emails(content)
            if emails:
                return emails, cls._reply_person_reference(content)
        for item in reversed(history):
            name = cls._reply_person_reference(str(item.get("content") or ""))
            if name:
                return frozenset(), name
        return frozenset(), None

    async def _resolve_reply_contact(
        self,
        *,
        name: str,
        conversation_id: str,
        principal_id: str,
    ) -> tuple[frozenset[str], str | None, dict[str, Any] | None]:
        """Resolve one named person through provider-backed Contacts evidence."""

        execution = await self.registry.execute(
            CapabilityRequest(
                capability_id="contacts.resolve",
                payload={"query": name},
                request_id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                principal_id=principal_id,
                operation="resolve_reply_status_recipient",
                target=name,
            ),
            refresh_health=True,
        )
        if not execution.success:
            return (
                frozenset(),
                None,
                {
                    "success": False,
                    "live_evidence_available": False,
                    "reply_received": None,
                    "error": execution.error or "Google Contacts is unavailable",
                },
            )
        if execution.data.get("ambiguous") is True:
            return (
                frozenset(),
                None,
                {
                    "success": True,
                    "live_evidence_available": False,
                    "reply_received": None,
                    "clarification_required": True,
                    "clarification": (
                        f"I found more than one {name} in your contacts — which one do you mean?"
                    ),
                },
            )
        contact = execution.data.get("contact")
        if not execution.data.get("resolved") or not isinstance(contact, Mapping):
            return (
                frozenset(),
                None,
                {
                    "success": True,
                    "live_evidence_available": False,
                    "reply_received": None,
                    "clarification_required": True,
                    "clarification": f"Which {name} do you mean?",
                },
            )
        recipients: set[str] = set()
        for address in contact.get("email_addresses") or ():
            try:
                recipients.add(GoogleConnector._recipient(str(address)).casefold())
            except ValueError:
                continue
        if not recipients:
            return (
                frozenset(),
                None,
                {
                    "success": True,
                    "live_evidence_available": False,
                    "reply_received": None,
                    "clarification_required": True,
                    "clarification": f"I don’t have a verified email address for {name}. Which email do you mean?",
                },
            )
        display_name = str(contact.get("display_name") or name).strip() or name
        return frozenset(recipients), display_name, None

    async def _create_recent_gmail_reply_monitor(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        request_id: str | None,
        user_text: str,
        polling_interval_seconds: int,
    ) -> dict[str, Any]:
        if not self._reply_monitor_intent(user_text):
            raise ValueError("The current request did not authorize reply monitoring")
        anchor = await self._verified_gmail_send_anchor(
            conversation_id=conversation_id,
            principal_id=principal_id,
            user_text=user_text,
        )
        if anchor is None:
            raise ValueError(
                "No verified sent Gmail message in this conversation matched the monitor request"
            )
        receipt, recipient, _ = anchor
        sent = receipt.result
        if self._email_policies is not None:
            return await self._email_policies.watch_reply(
                principal_id=principal_id,
                conversation_id=self.planner_executor.scope_conversation(
                    conversation_id,
                    principal_id,
                ),
                thread_id=str(sent["thread_id"]),
                sent_message_id=str(sent["message_id"]),
                recipient=recipient,
                source="explicit_verified_send_monitor",
                poll_interval_seconds=polling_interval_seconds,
            )
        return await self.create_external_monitor(
            conversation_id=conversation_id,
            principal_id=principal_id,
            provider="google",
            capability_id="gmail.reply_status",
            arguments={
                "thread_id": str(sent["thread_id"]),
                "sent_message_id": str(sent["message_id"]),
            },
            value_path="reply_count",
            comparison={"operator": "increased"},
            polling_interval_seconds=polling_interval_seconds,
            label=f"Reply from {recipient}",
            notify=True,
            request_id=request_id,
        )

    async def _create_important_gmail_monitor(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        request_id: str | None,
        user_text: str,
        polling_interval_seconds: int,
    ) -> dict[str, Any]:
        if not self._important_email_monitor_intent(user_text):
            raise ValueError("The current request did not authorize important-email monitoring")
        if self._email_policies is not None:
            configured = await self._email_policies.configure_assistant(
                principal_id=principal_id,
                conversation_id=self.planner_executor.scope_conversation(
                    conversation_id,
                    principal_id,
                ),
                important_email_alerts=True,
                poll_interval_seconds=polling_interval_seconds,
            )
            return {
                "success": True,
                "status": configured["status"],
                "important_email_alerts": configured["important_email_alerts"],
                "durable": True,
            }
        return await self.create_external_monitor(
            conversation_id=conversation_id,
            principal_id=principal_id,
            provider="google",
            capability_id="gmail.important_status",
            arguments={
                "query": "in:inbox {is:important is:starred is:unread}",
                "limit": 50,
            },
            value_path="attention_message_ids",
            comparison={"operator": "new_items"},
            polling_interval_seconds=polling_interval_seconds,
            label="Important Gmail",
            continuous=True,
            notify=True,
            request_id=request_id,
        )

    async def _check_recent_gmail_reply(
        self,
        *,
        conversation_id: str,
        principal_id: str,
        user_text: str,
        history: Sequence[Mapping[str, str]] = (),
        dialogue_focus: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve an exact sent anchor, then inspect its Gmail thread live."""

        scoped_conversation = self.planner_executor.scope_conversation(
            conversation_id,
            principal_id,
        )
        same_conversation = await self.receipts.list_recent(
            limit=100,
            conversation_id=scoped_conversation,
        )
        recent = await self.receipts.list_recent(limit=500)
        owner_prefix = f"usr:{principal_id}:"
        current_recipients = self._literal_user_emails(user_text)
        history_recipients, history_name = self._history_reply_referent(history)
        current_name = self._reply_person_reference(user_text)
        recipient_name: str | None = current_name or history_name
        recipient_hints = current_recipients or history_recipients

        focused_anchor: tuple[str, str, str, str | None] | None = None
        if not current_recipients and current_name is None:
            raw_focus = (dialogue_focus or {}).get("gmail_reply")
            if isinstance(raw_focus, Mapping):
                try:
                    focused_recipient = GoogleConnector._recipient(
                        str(raw_focus.get("recipient") or "")
                    ).casefold()
                except ValueError:
                    focused_recipient = ""
                focused_message_id = str(raw_focus.get("sent_message_id") or "").strip()
                focused_thread_id = str(raw_focus.get("thread_id") or "").strip()
                if focused_recipient and focused_message_id and focused_thread_id:
                    focused_anchor = (
                        focused_recipient,
                        focused_message_id,
                        focused_thread_id,
                        str(raw_focus.get("send_receipt_action_id") or "").strip() or None,
                    )
                    recipient_name = (
                        str(raw_focus.get("recipient_name") or "").strip() or recipient_name
                    )

        def receipt_recipients_for_name(name: str) -> frozenset[str]:
            normalised_name = re.sub(r"[^a-z0-9]+", "", name.casefold())
            receipt_addresses: set[str] = set()
            for receipt in (*same_conversation, *recent):
                if (
                    receipt.status is not ReceiptStatus.VERIFIED
                    or receipt.capability_id != "gmail.send"
                    or not str(receipt.conversation_id or "").startswith(owner_prefix)
                    or str(receipt.result.get("status") or "") != "sent"
                ):
                    continue
                try:
                    address = GoogleConnector._recipient(
                        str(receipt.result.get("recipient") or "")
                    ).casefold()
                except ValueError:
                    continue
                local_tokens = {
                    re.sub(r"[^a-z0-9]+", "", token)
                    for token in re.split(r"[._+\-]+", address.split("@", 1)[0])
                }
                if normalised_name and normalised_name in local_tokens:
                    receipt_addresses.add(address)
            return frozenset(receipt_addresses)

        if focused_anchor is None and not current_recipients and current_name:
            receipt_addresses = receipt_recipients_for_name(current_name)
            if len(receipt_addresses) == 1:
                recipient_hints = receipt_addresses
            else:
                recipient_hints, recipient_name, resolution = await self._resolve_reply_contact(
                    name=current_name,
                    conversation_id=scoped_conversation,
                    principal_id=principal_id,
                )
                if resolution is not None:
                    return resolution
        elif focused_anchor is None and not recipient_hints and history_name:
            receipt_addresses = receipt_recipients_for_name(history_name)
            if len(receipt_addresses) == 1:
                recipient_hints = receipt_addresses
            else:
                recipient_hints, recipient_name, resolution = await self._resolve_reply_contact(
                    name=history_name,
                    conversation_id=scoped_conversation,
                    principal_id=principal_id,
                )
                if resolution is not None:
                    return resolution

        def eligible(receipt: ActionReceipt) -> tuple[ActionReceipt, str] | None:
            result = receipt.result
            if (
                receipt.status is not ReceiptStatus.VERIFIED
                or receipt.capability_id != "gmail.send"
                or str(result.get("status") or "") != "sent"
                or not result.get("message_id")
                or not result.get("thread_id")
            ):
                return None
            try:
                candidate = GoogleConnector._recipient(
                    str(result.get("recipient") or "")
                ).casefold()
            except ValueError:
                return None
            if recipient_hints and candidate not in recipient_hints:
                return None
            return receipt, candidate

        same_matches = [
            match for receipt in same_conversation if (match := eligible(receipt)) is not None
        ]
        other_matches = [
            match
            for receipt in recent
            if receipt.conversation_id != scoped_conversation
            and str(receipt.conversation_id or "").startswith(owner_prefix)
            and (match := eligible(receipt)) is not None
        ]

        anchor: tuple[ActionReceipt, str, str] | None = None
        if focused_anchor is not None:
            recipient, sent_message_id, thread_id, receipt_action_id = focused_anchor
            anchor_source = "conversation_reply_focus"
        elif same_matches and (current_recipients or len(same_matches) == 1):
            anchor = same_matches[0][0], same_matches[0][1], "same_conversation_receipt"
        elif len(same_matches) > 1:
            whom = recipient_name or "that recipient"
            return {
                "success": True,
                "live_evidence_available": False,
                "reply_received": None,
                "clarification_required": True,
                "clarification": (
                    f"I found more than one recent email to {whom}. Which one do you mean?"
                ),
            }
        elif current_recipients and other_matches:
            # Preserve the exact-address contract: an address in the immutable
            # current request means the latest exact principal-owned send.
            anchor = other_matches[0][0], other_matches[0][1], "principal_durable_receipt"
        elif len(other_matches) == 1:
            anchor = other_matches[0][0], other_matches[0][1], "principal_durable_receipt"
        elif len(other_matches) > 1:
            if not recipient_hints and not recipient_name:
                return {
                    "success": True,
                    "live_evidence_available": False,
                    "reply_received": None,
                    "clarification_required": True,
                    "clarification": "Which email do you mean?",
                }
            whom = recipient_name or "that recipient"
            return {
                "success": True,
                "live_evidence_available": False,
                "reply_received": None,
                "clarification_required": True,
                "clarification": (
                    f"I found more than one recent email to {whom}. Which one do you mean?"
                ),
            }

        if focused_anchor is None:
            receipt_action_id = None
        if focused_anchor is None and anchor is not None:
            anchor_receipt, recipient, anchor_source = anchor
            result = anchor_receipt.result
            sent_message_id = str(result["message_id"])
            thread_id = str(result["thread_id"])
            receipt_action_id = str(anchor_receipt.action_id)
        elif focused_anchor is None:
            if len(recipient_hints) != 1:
                return {
                    "success": True,
                    "live_evidence_available": False,
                    "reply_received": None,
                    "clarification_required": True,
                    "clarification": "Which email do you mean?",
                }
            recipient = next(iter(recipient_hints))
            search = await self.registry.execute(
                CapabilityRequest(
                    capability_id="gmail.search",
                    payload={"query": f"in:sent to:{recipient}", "limit": 10},
                    request_id=str(uuid.uuid4()),
                    conversation_id=scoped_conversation,
                    principal_id=principal_id,
                    operation="recover_exact_sent_message_for_reply_status",
                    target=recipient,
                ),
                refresh_health=True,
            )
            if not search.success:
                return {
                    "success": False,
                    "live_evidence_available": False,
                    "reply_received": None,
                    "error": search.error or "Gmail Sent-mail recovery failed",
                }
            candidates: list[tuple[int | None, str, str]] = []
            for item in search.data.get("messages") or ():
                if not isinstance(item, Mapping):
                    continue
                labels = {str(label) for label in item.get("label_ids") or ()}
                message_id = str(item.get("message_id") or "").strip()
                candidate_thread = str(item.get("thread_id") or "").strip()
                addresses: set[str] = set()
                for _, address in getaddresses([str(item.get("to") or "")]):
                    try:
                        addresses.add(GoogleConnector._recipient(address).casefold())
                    except ValueError:
                        continue
                raw_timestamp = item.get("internal_date_ms")
                timestamp = int(raw_timestamp) if isinstance(raw_timestamp, int) else None
                if "SENT" in labels and message_id and candidate_thread and recipient in addresses:
                    candidates.append((timestamp, message_id, candidate_thread))
            candidates.sort(
                key=lambda item: item[0] if item[0] is not None else -1,
                reverse=True,
            )
            if not candidates:
                return {
                    "success": True,
                    "live_evidence_available": True,
                    "reply_received": None,
                    "clarification_required": True,
                    "clarification": (
                        f"I couldn’t find a sent email to {recipient_name or recipient} to check."
                    ),
                }
            ambiguous_named_recovery = not current_recipients and len(candidates) > 1
            if (
                ambiguous_named_recovery
                or candidates[0][0] is None
                or (len(candidates) > 1 and candidates[1][0] == candidates[0][0])
            ):
                return {
                    "success": True,
                    "live_evidence_available": True,
                    "reply_received": None,
                    "clarification_required": True,
                    "clarification": (
                        f"I found more than one recent email to {recipient_name or recipient}. "
                        "Which one do you mean?"
                    ),
                }
            _, sent_message_id, thread_id = candidates[0]
            anchor_source = "bounded_exact_recipient_sent_search"

        execution = await self.registry.execute(
            CapabilityRequest(
                capability_id="gmail.reply_status",
                payload={
                    "thread_id": thread_id,
                    "sent_message_id": sent_message_id,
                },
                request_id=str(uuid.uuid4()),
                conversation_id=scoped_conversation,
                principal_id=principal_id,
                operation="check_reply_after_verified_send",
                target=thread_id,
            ),
            refresh_health=True,
        )
        if not execution.success:
            return {
                "success": False,
                "live_evidence_available": False,
                "reply_received": None,
                "error": execution.error or "Gmail reply status could not be verified",
            }
        evidence = dict(execution.data)
        return {
            "success": True,
            "live_evidence_available": True,
            "reply_received": evidence.get("reply_received") is True,
            "recipient": recipient,
            "recipient_name": recipient_name,
            "sent_message_id": sent_message_id,
            "thread_id": thread_id,
            "anchor_source": anchor_source,
            "send_receipt_action_id": receipt_action_id,
            "reply_count": int(evidence.get("reply_count") or 0),
            "replies": list(evidence.get("replies") or ()),
            "provider_reference": execution.provider_reference,
            "evidence": evidence.get("evidence"),
        }

    async def _manage_email_retention_policy(
        self,
        arguments: Mapping[str, Any],
        *,
        conversation_id: str,
        principal_id: str,
        request_id: str | None,
        user_text: str,
    ) -> dict[str, Any]:
        engine = self._email_policies
        if engine is None:
            raise RuntimeError("Durable email retention is unavailable")
        operation = str(arguments.get("operation") or "").strip().casefold()
        if operation == "list":
            policies = await engine.list(principal_id=principal_id)
            return {"success": True, "count": len(policies), "policies": policies}

        policy_id = str(arguments.get("policy_id") or "").strip()
        authority = " ".join(str(user_text or "").casefold().split())
        if operation == "create":
            days = int(arguments.get("retention_days") or 0)
            stated_days = {int(value) for value in re.findall(r"\b(\d{1,4})\s+days?\b", authority)}
            if (
                days not in stated_days
                or not self._email_retention_intent(user_text)
                or not self._write_authorized("gmail.trash", user_text)
            ):
                raise ValueError(
                    "The current request must explicitly authorize the retention days and Trash policy"
                )
            policy = await engine.create_retention_policy(
                principal_id=principal_id,
                conversation_id=self.planner_executor.scope_conversation(
                    conversation_id,
                    principal_id,
                ),
                retention_days=days,
                request_id=request_id,
            )
        else:
            if not policy_id:
                raise ValueError("Email retention policy_id is required")
            verbs = {
                "pause": ("pause", "stop"),
                "resume": ("resume", "restart", "enable"),
                "change": ("change", "update", "set"),
                "disable": ("disable", "cancel", "turn off"),
            }
            operation_verbs = verbs.get(operation, ())
            negated = any(
                re.search(
                    rf"\b(?:do not|don t|dont|never)\s+(?:please\s+)?(?:\w+\s+){{0,3}}"
                    rf"{re.escape(verb)}\b",
                    authority,
                )
                for verb in operation_verbs
            )
            if (
                not operation_verbs
                or negated
                or not any(verb in authority for verb in operation_verbs)
            ):
                raise ValueError("The current request did not authorize that policy change")
            if operation == "change":
                days = int(arguments.get("retention_days") or 0)
                stated_days = {
                    int(value) for value in re.findall(r"\b(\d{1,4})\s+days?\b", authority)
                }
                if days not in stated_days:
                    raise ValueError("The new retention period was not stated by the user")
                policy = await engine.change(
                    policy_id,
                    principal_id=principal_id,
                    retention_days=days,
                )
            else:
                policy = await getattr(engine, operation)(
                    policy_id,
                    principal_id=principal_id,
                )
            if policy is None:
                raise ValueError("Email retention policy was not found for this principal")
        persisted = await engine.get(policy["policy_id"], principal_id=principal_id)
        if persisted is None or persisted["status"] != policy["status"]:
            raise RuntimeError("Email retention policy persistence could not be verified")
        return {
            "success": True,
            "persisted": True,
            "policy": persisted,
            "operation": operation,
            "permanent_delete": False,
        }

    @staticmethod
    def _write_authorized(capability_id: str, user_text: str) -> bool:
        """Recognize explicit CURRENT user authority for an external write."""

        request_prefix = str(user_text or "")[:5_000]

        # Do not let quoted/body content grant authority for another action.
        for separator in ("\n", "\r", ":", "?", "!", '"'):
            request_prefix = request_prefix.partition(separator)[0]

        normalised = "".join(
            character.casefold() if character.isalnum() else " " for character in request_prefix
        )

        words = normalised.split()[:32]

        content_markers = {
            "body",
            "contains",
            "reads",
            "said",
            "says",
            "saying",
            "tells",
        }

        for index, word in enumerate(words):
            if word in content_markers:
                words = words[:index]
                break

        word_set = set(words)
        authority_text = " ".join(words)

        negated_actions: Mapping[str, tuple[str, ...]] = {
            "gmail.reply": (r"reply", r"respond"),
            "gmail.draft": (r"draft", r"compose", r"write", r"email"),
            "gmail.send": (r"send", r"email"),
            "gmail.forward": (r"forward",),
            "gmail.archive": (r"archive",),
            "gmail.mark_read": (r"mark(?:\s+\w+){0,3}\s+read",),
            "gmail.mark_unread": (r"mark(?:\s+\w+){0,3}\s+unread",),
            "gmail.star": (r"star", r"add(?:\s+a)?\s+star"),
            "gmail.unstar": (r"unstar", r"remove(?:\s+the|\s+a)?\s+star"),
            "gmail.mark_important": (r"mark(?:\s+\w+){0,3}\s+important",),
            "gmail.mark_not_important": (
                r"mark(?:\s+\w+){0,3}\s+not\s+important",
                r"remove(?:\s+the)?\s+important",
            ),
            "gmail.move": (r"move",),
            "gmail.trash": (r"trash", r"delete", r"remove"),
            "gmail.restore": (r"restore", r"undelete", r"untrash"),
            "calendar.create": (r"add", r"book", r"create", r"put", r"schedule"),
            "calendar.update": (r"change", r"move", r"reschedule", r"update"),
            "calendar.cancel": (r"cancel", r"delete", r"remove"),
        }
        negative_prefix = r"\b(?:do not|don t|dont|never)\s+(?:please\s+)?(?:\w+\s+){0,3}"
        if any(
            re.search(negative_prefix + marker + r"\b", authority_text)
            for marker in negated_actions.get(capability_id, ())
        ):
            return False

        # ----------------------------------------------------
        # Gmail actions whose intent needs phrase-level checks.
        # ----------------------------------------------------

        if capability_id == "gmail.reply":
            # A noun/query such as "Have I got any reply?" is NOT
            # permission to create a reply draft.
            return bool(
                re.search(
                    r"^(?:please\s+)?(?:reply|respond)\b|"
                    r"^(?:can|could|would|will)\s+you\s+"
                    r"(?:please\s+)?(?:reply|respond)\b|"
                    r"\bdraft\s+(?:a\s+)?reply\b",
                    authority_text,
                )
            )

        if capability_id == "gmail.send":
            return ExternalAgentRuntime._gmail_message_operation(authority_text) == "send"

        if capability_id == "gmail.draft":
            return ExternalAgentRuntime._gmail_message_operation(authority_text) in {
                "draft",
                "send",
            }

        if capability_id == "gmail.mark_read":
            return bool(
                re.search(
                    r"\bmark(?:\s+(?:this|that|the|it|email|message)){0,3}"
                    r"\s+(?:as\s+)?read\b",
                    authority_text,
                )
            )

        if capability_id == "gmail.mark_unread":
            return bool(
                re.search(
                    r"\bmark(?:\s+(?:this|that|the|it|email|message)){0,3}"
                    r"\s+(?:as\s+)?unread\b",
                    authority_text,
                )
            )

        if capability_id == "gmail.star":
            return bool(
                re.search(
                    r"\bstar(?:\s+(?:this|that|the|it|email|message)){0,3}\b",
                    authority_text,
                )
            ) or bool(re.search(r"\badd\s+(?:a\s+)?star\b", authority_text))

        if capability_id == "gmail.unstar":
            return "unstar" in word_set or bool(
                re.search(
                    r"\bremove\s+(?:the\s+|a\s+)?star\b",
                    authority_text,
                )
            )

        if capability_id == "gmail.mark_important":
            return bool(
                re.search(
                    r"\bmark(?:\s+(?:this|that|the|it|email|message)){0,3}"
                    r"\s+(?:as\s+)?important\b",
                    authority_text,
                )
            )

        if capability_id == "gmail.mark_not_important":
            return bool(
                re.search(
                    r"\bmark(?:\s+(?:this|that|the|it|email|message)){0,3}"
                    r"\s+(?:as\s+)?not\s+important\b",
                    authority_text,
                )
            ) or bool(
                re.search(
                    r"\bremove\s+(?:the\s+)?important(?:\s+marker)?\b",
                    authority_text,
                )
            )

        if capability_id == "gmail.archive":
            return (
                "archive" in word_set
                and bool(
                    word_set
                    & {
                        "email",
                        "emails",
                        "message",
                        "messages",
                        "gmail",
                        "inbox",
                    }
                )
            ) or bool(
                re.search(
                    r"\barchive\s+(?:it|that|this|one)\b",
                    authority_text,
                )
            )

        if capability_id == "gmail.move":
            return (
                "move" in word_set
                and bool(
                    word_set
                    & {
                        "email",
                        "emails",
                        "message",
                        "messages",
                        "gmail",
                        "inbox",
                    }
                )
            ) or bool(
                re.search(
                    r"\bmove\s+(?:it|that|this|one)\s+to\b",
                    authority_text,
                )
            )

        if capability_id == "gmail.trash":
            return (
                bool(word_set & {"trash", "bin"})
                or (
                    bool(word_set & {"delete", "remove"})
                    and bool(
                        word_set
                        & {
                            "email",
                            "emails",
                            "message",
                            "messages",
                            "gmail",
                            "inbox",
                        }
                    )
                )
                or bool(
                    re.search(
                        r"\b(?:delete|remove)\s+(?:it|that|this|one)\b",
                        authority_text,
                    )
                )
            )

        if capability_id == "gmail.restore":
            return (
                "untrash" in word_set
                or (
                    bool(word_set & {"restore", "undelete"})
                    and bool(
                        word_set
                        & {
                            "email",
                            "emails",
                            "message",
                            "messages",
                            "gmail",
                            "trash",
                        }
                    )
                )
                or bool(
                    re.search(
                        r"\b(?:restore|undelete)\s+"
                        r"(?:it|that|this|one)\b",
                        authority_text,
                    )
                )
            )

        # ----------------------------------------------------
        # Existing explicit command verbs.
        # ----------------------------------------------------

        required: Mapping[str, frozenset[str]] = {
            "gmail.draft": frozenset(
                {
                    "draft",
                    "compose",
                    "write",
                    "send",
                }
            ),
            "gmail.send": frozenset({"send"}),
            "gmail.forward": frozenset({"forward"}),
            "calendar.create": frozenset(
                {
                    "add",
                    "book",
                    "create",
                    "put",
                    "schedule",
                }
            ),
            "calendar.update": frozenset(
                {
                    "change",
                    "move",
                    "reschedule",
                    "update",
                }
            ),
            "calendar.cancel": frozenset(
                {
                    "cancel",
                    "delete",
                    "remove",
                }
            ),
        }

        verbs = required.get(capability_id)
        return verbs is not None and bool(word_set & verbs)

    @staticmethod
    def _literal_user_emails(user_text: str) -> frozenset[str]:
        """Return complete literal email addresses stated in the user's request."""

        text = str(user_text or "")
        local_characters = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.!#$%&'*+/=?^_`{|}~-"
        )
        domain_characters = frozenset(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-"
        )
        addresses: set[str] = set()
        for marker, character in enumerate(text):
            if character != "@":
                continue
            start = marker
            while start > 0 and text[start - 1] in local_characters:
                start -= 1
            end = marker + 1
            while end < len(text) and text[end] in domain_characters:
                end += 1
            while end > marker + 1 and text[end - 1] in ".-":
                end -= 1
            candidate = text[start:end]
            try:
                addresses.add(GoogleConnector._recipient(candidate).casefold())
            except ValueError:
                continue
        return frozenset(addresses)

    @staticmethod
    def _plan_recipient_authorized(
        value: Any,
        *,
        user_text: str,
        steps_by_id: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        """Accept a literal user address or dataflow from Contacts resolution."""

        if isinstance(value, Mapping):
            if set(value) != {"$from_step", "path"}:
                return False
            source = steps_by_id.get(str(value.get("$from_step") or ""))
            path = str(value.get("path") or "")
            return bool(
                source
                and source.get("capability_id") == "contacts.resolve"
                and path.startswith("contact.email_addresses.")
            )
        try:
            recipient = GoogleConnector._recipient(str(value or "")).casefold()
        except ValueError:
            return False
        return recipient in ExternalAgentRuntime._literal_user_emails(user_text)

    async def _execute_google_model_tool(
        self,
        arguments: Mapping[str, Any],
        *,
        conversation_id: str,
        principal_id: str,
        request_id: str | None,
        user_text: str,
        history: Sequence[Mapping[str, str]] = (),
    ) -> dict[str, Any]:
        capability_id = str(arguments.get("capability_id") or "").strip()
        payload_value = arguments.get("arguments")
        if not isinstance(payload_value, Mapping):
            raise ValueError("Google capability arguments must be an object")
        payload = dict(payload_value)
        metadata = self.registry.capability_definition(capability_id)
        if metadata is None or metadata.provider_id != "google":
            raise ValueError("The requested Google capability is not registered")
        confirmed = False
        if metadata.access is CapabilityAccess.WRITE:
            if not self._write_authorized(capability_id, user_text):
                raise ValueError(
                    "The user's current request did not explicitly authorize this write"
                )
            if not self._gmail_write_context_authorized(
                capability_id,
                user_text,
                history,
            ):
                raise ValueError(
                    "The current request did not establish Gmail context for this write"
                )
            confirmed = True
        if capability_id in {"gmail.draft", "gmail.forward"}:
            recipient = payload.get("to")
            if recipient and not self._plan_recipient_authorized(
                recipient,
                user_text=user_text,
                steps_by_id={},
            ):
                raise ValueError(
                    "A recipient not stated by the user must come from a verified plan step"
                )
        if capability_id in {"calendar.create", "calendar.update"}:
            event_payload: Mapping[str, Any] = payload
            if capability_id == "calendar.update" and isinstance(payload.get("changes"), Mapping):
                event_payload = payload["changes"]
            attendees = event_payload.get("attendees")
            if attendees is not None:
                if not isinstance(attendees, Sequence) or isinstance(attendees, (str, bytes)):
                    raise ValueError("Calendar attendees must be an array")
                literal_emails = self._literal_user_emails(user_text)
                for attendee in attendees:
                    if not isinstance(attendee, Mapping):
                        raise ValueError("Each calendar attendee must be an object")
                    try:
                        email = GoogleConnector._recipient(
                            str(attendee.get("email") or "")
                        ).casefold()
                    except ValueError as exc:
                        raise ValueError(
                            "Each calendar attendee must contain one valid email address"
                        ) from exc
                    if email not in literal_emails:
                        raise ValueError(
                            "An attendee not stated by the user must come from a verified plan step"
                        )
        resolved_request_id = str(request_id or uuid.uuid4())
        idempotency_material = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        scoped_key = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"google:{principal_id}:{conversation_id}:{resolved_request_id}:"
                f"{capability_id}:{idempotency_material}",
            )
        )
        execution = await self.registry.execute(
            CapabilityRequest(
                capability_id=capability_id,
                payload=payload,
                request_id=resolved_request_id,
                conversation_id=conversation_id,
                principal_id=principal_id,
                target=next(
                    (
                        payload[key]
                        for key in (
                            "draft_id",
                            "message_id",
                            "thread_id",
                            "event_id",
                            "to",
                        )
                        if payload.get(key) not in (None, "")
                    ),
                    None,
                ),
                operation=capability_id,
                confirmed=confirmed,
                idempotency_key=scoped_key,
            ),
            refresh_health=True,
        )
        return execution.as_dict()

    async def search(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        return await self.execute("web.search", {"query": query, "limit": limit})

    async def fetch(self, url: str) -> dict[str, Any]:
        return await self.execute("web.fetch", {"url": url})

    async def list_external_monitors(
        self,
        *,
        conversation_id: str,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if self._monitor_lister is None:
            raise RuntimeError("External monitor listing is unavailable")
        jobs = await self._monitor_lister(
            str(conversation_id).strip(), status, max(1, min(int(limit), 100))
        )
        monitors: list[dict[str, Any]] = []
        for job in jobs:
            payload = job.get("payload")
            safe_payload = payload if isinstance(payload, Mapping) else {}
            monitors.append(
                {
                    "job_id": job.get("job_id"),
                    "status": job.get("status"),
                    "provider": safe_payload.get("provider"),
                    "capability_id": safe_payload.get("capability_id"),
                    "label": redact_secrets(safe_payload.get("label")),
                    "next_run_at": job.get("next_run_at"),
                    "poll_count": job.get("poll_count"),
                    "max_polls": job.get("max_polls"),
                    "expires_at": job.get("expires_at"),
                }
            )
        return {
            "success": True,
            "count": len(monitors),
            "monitors": monitors,
        }

    async def cancel_external_monitor(
        self,
        *,
        conversation_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        if self._monitor_canceller is None:
            raise RuntimeError("External monitor cancellation is unavailable")
        resolved_job_id = str(job_id).strip()
        if not resolved_job_id:
            raise ValueError("External monitor job_id is required")
        job = await self._monitor_canceller(str(conversation_id).strip(), resolved_job_id)
        if job is None:
            return {
                "success": False,
                "job_id": resolved_job_id,
                "error": "External monitor was not found in this conversation",
            }
        return {
            "success": job.get("status") == "cancelled",
            "job_id": job.get("job_id"),
            "status": job.get("status"),
        }

    async def create_external_monitor(
        self,
        *,
        conversation_id: str,
        principal_id: str | None = None,
        provider: str,
        capability_id: str,
        query: Any = None,
        operation: Any = None,
        arguments: Mapping[str, Any] | None = None,
        value_path: str | None = None,
        comparison: Any = "changed",
        polling_interval_seconds: int = 900,
        label: str | None = None,
        max_attempts: int = 3,
        expires_at: str | None = None,
        notify_if_unchanged: bool = False,
        continuous: bool = False,
        notify: bool = False,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        creator = self._monitor_creator
        if creator is None:
            raise RuntimeError("Durable external monitor creation is unavailable")
        resolved_principal = str(principal_id or "").strip() or None
        scoped_conversation = self.planner_executor.scope_conversation(
            conversation_id,
            resolved_principal,
        )
        metadata = await self.registry.get_capability(
            str(capability_id).strip(),
            refresh=True,
            principal_id=resolved_principal,
        )
        if (
            metadata is None
            or metadata.access is not CapabilityAccess.READ
            or not metadata.repeatable
        ):
            raise RuntimeError(
                "Monitor capability is unavailable, not read-only, or not repeatable"
            )
        if metadata.provider_id != str(provider).strip():
            raise RuntimeError("Monitor provider does not own the requested capability")
        provider_account_id: str | None = None
        if metadata.provider_id == "google":
            if not resolved_principal:
                raise RuntimeError("A Google monitor requires an authenticated principal")
            account = await self.google_connector.account_status(resolved_principal)
            if account is None or not account.authenticated:
                raise RuntimeError("A connected Google account is required for this monitor")
            provider_account_id = account.account_id
        interval = int(polling_interval_seconds)
        minimum_interval = int(metadata.minimum_poll_interval_seconds or 0)
        if interval < minimum_interval or interval > 2_592_000:
            if interval < minimum_interval:
                raise ValueError(
                    "External monitor polling interval must be at least "
                    f"{minimum_interval} seconds for {metadata.capability_id}"
                )
            raise ValueError("External monitor polling interval is out of range")
        selected_path = str(value_path or "").strip()
        if selected_path not in metadata.monitor_value_paths:
            choices = ", ".join(metadata.monitor_value_paths)
            raise ValueError(f"External monitor value_path must be one of: {choices}")
        normalised_comparison = self._normalise_monitor_comparison(comparison)
        now = datetime.now(timezone.utc)
        configured_expiry = now + timedelta(seconds=int(metadata.monitor_ttl_seconds or interval))
        if expires_at:
            rendered_expiry = str(expires_at).strip()
            if rendered_expiry.endswith("Z"):
                rendered_expiry = rendered_expiry[:-1] + "+00:00"
            try:
                requested_expiry = datetime.fromisoformat(rendered_expiry)
            except ValueError as exc:
                raise ValueError("External monitor deadline must be an ISO-8601 timestamp") from exc
            if requested_expiry.tzinfo is None:
                raise ValueError("External monitor deadline must include a timezone")
            requested_expiry = requested_expiry.astimezone(timezone.utc)
            if requested_expiry <= now or requested_expiry > configured_expiry:
                raise ValueError("External monitor deadline is outside the capability policy")
            configured_expiry = requested_expiry
        payload: dict[str, Any] = {
            "provider": metadata.provider_id,
            "capability_id": metadata.capability_id,
            "arguments": dict(arguments or {}),
            "comparison": normalised_comparison,
            "polling_interval_seconds": interval,
            "max_attempts": max(1, min(int(max_attempts), 10)),
            "conversation_id": scoped_conversation,
            "principal_id": resolved_principal,
            "provider_account_id": provider_account_id,
            "value_path": selected_path,
            "poll_count": 0,
            "max_polls": int(metadata.maximum_monitor_polls or 1),
            "expires_at": configured_expiry.isoformat(),
            "deadline_requested": bool(expires_at),
            "notify_if_unchanged": bool(notify_if_unchanged),
            "continuous": bool(continuous),
            "notify": bool(notify),
        }
        for key, value in (
            ("query", query),
            ("operation", operation),
            ("label", str(label or "").strip()[:200] or None),
        ):
            if value is not None:
                payload[key] = value
        if redact_secrets(payload) != payload:
            raise ValueError("External monitor definitions may not contain credentials or secrets")
        resolved_request_id = self._scoped_monitor_key(
            scoped_conversation,
            str(principal_id or "").strip(),
            request_id,
        )
        if resolved_request_id:
            lock = await self._monitor_lock(resolved_request_id)
            async with lock:
                return await self._capture_and_persist_monitor(
                    creator=creator,
                    scoped_conversation=scoped_conversation,
                    payload=payload,
                    interval=interval,
                    request_id=resolved_request_id,
                    comparison=normalised_comparison,
                )
        return await self._capture_and_persist_monitor(
            creator=creator,
            scoped_conversation=scoped_conversation,
            payload=payload,
            interval=interval,
            request_id=None,
            comparison=normalised_comparison,
        )

    async def _monitor_lock(self, request_id: str) -> asyncio.Lock:
        async with self._monitor_locks_guard:
            lock = self._monitor_locks.get(request_id)
            if lock is None:
                lock = asyncio.Lock()
                self._monitor_locks[request_id] = lock
            return lock

    async def _capture_and_persist_monitor(
        self,
        *,
        creator: MonitorCreator,
        scoped_conversation: str,
        payload: dict[str, Any],
        interval: int,
        request_id: str | None,
        comparison: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Serialize an idempotent baseline capture through durable creation."""

        resolved_request_id = request_id
        if resolved_request_id and self._monitor_lookup is not None:
            existing = await self._monitor_lookup(scoped_conversation, resolved_request_id)
            if existing is not None:
                if (
                    str(existing.get("kind") or "") != "external_monitor"
                    or str(existing.get("conversation_id") or "") != scoped_conversation
                    or not self._same_monitor_definition(existing, payload)
                ):
                    raise ValueError(
                        "External monitor request ID was already used for a different request"
                    )
                return self._compact_monitor_result(
                    existing,
                    baseline_captured=False,
                    reused=True,
                )
        baseline = await self.evaluate_external_monitor(payload)
        payload["baseline"] = baseline["value"]
        operator = str(comparison["operator"])
        if (
            isinstance(baseline["value"], Mapping)
            and baseline["value"].get("kind") == "content_fingerprint"
            and operator != "changed"
        ):
            raise ValueError("Web text monitors support only content-change comparisons")
        if operator in {"decreased", "increased", "less_than", "greater_than"}:
            value = baseline["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Ordered monitors require a numeric provider observation")
        job = await creator(
            scoped_conversation,
            payload,
            min(
                interval,
                max(
                    1,
                    int(
                        (
                            datetime.fromisoformat(str(payload["expires_at"]))
                            - datetime.now(timezone.utc)
                        ).total_seconds()
                    ),
                ),
            ),
            resolved_request_id,
        )
        return self._compact_monitor_result(
            job,
            baseline_captured=True,
            reused=False,
            baseline=baseline["value"],
        )

    @staticmethod
    def _scoped_monitor_key(
        conversation_id: str,
        principal_id: str,
        request_id: str | None,
    ) -> str | None:
        raw = str(request_id or "").strip()
        if not raw:
            return None
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"jarvis-monitor:{principal_id}:{conversation_id}:{raw}",
            )
        )

    @staticmethod
    def _normalise_monitor_comparison(comparison: Any) -> dict[str, Any]:
        if isinstance(comparison, str):
            operator = comparison.strip().casefold()
            raw: Mapping[str, Any] = {}
        elif isinstance(comparison, Mapping):
            operator = (
                str(comparison.get("operator") or comparison.get("type") or "").strip().casefold()
            )
            raw = comparison
        else:
            raise ValueError("External monitor comparison is malformed")
        allowed = {
            "changed",
            "equals",
            "not_equals",
            "decreased",
            "increased",
            "less_than",
            "greater_than",
            "contains",
            "truthy",
            "new_items",
        }
        if operator not in allowed:
            raise ValueError(f"Unsupported external monitor comparison: {operator or 'missing'}")
        target_required = {
            "equals",
            "not_equals",
            "less_than",
            "greater_than",
            "contains",
        }
        output: dict[str, Any] = {"operator": operator}
        if operator in target_required:
            target_found = False
            for key in ("target", "expected", "value"):
                if key in raw:
                    output["target"] = raw[key]
                    target_found = True
                    break
            if not target_found:
                raise ValueError(f"External monitor comparison '{operator}' requires a target")
        return output

    @staticmethod
    def _compact_monitor_result(
        job: Mapping[str, Any],
        *,
        baseline_captured: bool,
        reused: bool,
        baseline: Any = None,
    ) -> dict[str, Any]:
        job_id = str(job.get("job_id") or "").strip()
        status = str(job.get("status") or "").strip()
        if not job_id or not status:
            raise RuntimeError("The durable monitor store did not return a persisted job identity")
        result: dict[str, Any] = {
            "success": True,
            "baseline_captured": baseline_captured,
            "reused": reused,
            "job_id": job_id,
            "status": status,
            "next_run_at": job.get("next_run_at"),
        }
        if baseline_captured:
            encoded = json.dumps(
                baseline,
                separators=(",", ":"),
                sort_keys=True,
                default=str,
            ).encode("utf-8")
            result["baseline"] = {
                "captured": True,
                "type": type(baseline).__name__,
                "size_bytes": len(encoded),
            }
        return result

    @staticmethod
    def _same_monitor_definition(existing: Mapping[str, Any], requested: Mapping[str, Any]) -> bool:
        stored = existing.get("payload")
        if not isinstance(stored, Mapping):
            return False
        identity_keys = (
            "provider",
            "capability_id",
            "arguments",
            "query",
            "operation",
            "value_path",
            "comparison",
            "polling_interval_seconds",
            "principal_id",
            "provider_account_id",
            "label",
            "max_attempts",
            "max_polls",
            "deadline_requested",
            "notify_if_unchanged",
            "continuous",
            "notify",
        )
        if not all(stored.get(key) == requested.get(key) for key in identity_keys):
            return False
        if requested.get("deadline_requested") is True:
            return stored.get("expires_at") == requested.get("expires_at")
        return True

    async def evaluate_external_monitor(self, monitor: Mapping[str, Any]) -> dict[str, Any]:
        """Re-run one explicitly repeatable read for FollowUpEngine.

        The worker owns comparison and delivery.  This callback returns only a
        verified provider observation and never declares the condition changed.
        """

        capability_id = str(monitor.get("capability_id") or "").strip()
        provider_id = str(monitor.get("provider") or "").strip()
        principal_id = str(monitor.get("principal_id") or "").strip() or None
        metadata = await self.registry.get_capability(
            capability_id,
            refresh=True,
            principal_id=principal_id,
        )
        if (
            metadata is None
            or metadata.access is not CapabilityAccess.READ
            or not metadata.repeatable
        ):
            raise RuntimeError(
                "Monitor capability is unavailable, not read-only, or not repeatable"
            )
        if metadata.provider_id != provider_id:
            raise RuntimeError("Monitor provider does not own the requested capability")
        if provider_id == "google":
            expected_account = str(monitor.get("provider_account_id") or "").strip()
            if not principal_id or not expected_account:
                raise RuntimeError("Google monitor account binding is missing")
            current_account = await self.google_connector.account_status(principal_id)
            if current_account is None or current_account.account_id != expected_account:
                raise RuntimeError("Google monitor account binding no longer matches")

        raw_arguments = monitor.get("arguments")
        payload = dict(raw_arguments) if isinstance(raw_arguments, Mapping) else {}
        operation: str | None = None
        raw_operation = monitor.get("operation")
        if isinstance(raw_operation, Mapping):
            payload.update(raw_operation)
        elif raw_operation is not None:
            operation = str(raw_operation).strip() or None
        if "query" in monitor and "query" not in payload:
            query = monitor.get("query")
            if isinstance(query, Mapping):
                payload.update(query)
            else:
                payload["query"] = query

        execution = await self.registry.execute(
            CapabilityRequest(
                capability_id=capability_id,
                payload=payload,
                request_id=str(uuid.uuid4()),
                conversation_id=str(monitor.get("conversation_id") or "") or None,
                principal_id=principal_id,
                operation=operation,
            ),
            refresh_health=True,
        )
        if not execution.success:
            raise RuntimeError(execution.error or "External monitor observation failed")

        value: Any = dict(execution.data)
        value_path = str(monitor.get("value_path") or "").strip()
        if value_path not in metadata.monitor_value_paths:
            raise RuntimeError("External monitor value_path is not an approved selector")
        for part in (segment for segment in value_path.split(".") if segment):
            if isinstance(value, Mapping) and part in value:
                value = value[part]
            else:
                raise RuntimeError("External monitor value_path was not present")
        if capability_id in {"web.fetch", "web.search"} and isinstance(value, str):
            content = value.encode("utf-8")
            value = {
                "kind": "content_fingerprint",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        return {
            "verified": True,
            "value": value,
            "provider_reference": execution.provider_reference,
        }

    async def deep_research(
        self,
        question: str,
        *,
        queries: Sequence[str] | None = None,
        resume: Mapping[str, Any] | None = None,
        max_queries_this_run: int | None = None,
    ) -> dict[str, Any]:
        result = await self.research.research(
            question,
            queries=queries,
            resume=resume,
            max_queries_this_run=max_queries_this_run,
        )
        return result.to_dict()

    async def _research_search(self, query: str) -> Sequence[Mapping[str, Any]]:
        execution = await self.search(query)
        if not execution.get("success"):
            raise RuntimeError(str(execution.get("error") or "Live search unavailable"))
        data = execution.get("data") or {}
        if not isinstance(data, Mapping):
            raise RuntimeError("Live search returned malformed evidence")
        sources = data.get("sources") or ()
        if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)):
            raise RuntimeError("Live search returned malformed sources")
        return [dict(item) for item in sources if isinstance(item, Mapping)]

    async def _research_fetch(self, url: str) -> Mapping[str, Any]:
        execution = await self.fetch(url)
        if not execution.get("success"):
            raise RuntimeError(str(execution.get("error") or "Page fetch unavailable"))
        data = execution.get("data") or {}
        if not isinstance(data, Mapping):
            raise RuntimeError("Page fetch returned malformed evidence")
        return dict(data)

    async def _research_conflicts(
        self,
        question: str,
        sources: Sequence[Any],
    ) -> Sequence[Mapping[str, Any]]:
        analyzer = getattr(self.web_search_client, "analyze_conflicts", None)
        if not callable(analyzer):
            return ()
        payload = [
            source.to_dict() for source in sources if callable(getattr(source, "to_dict", None))
        ]
        return await analyzer(question, payload)

    @staticmethod
    def _proposed_step(value: Mapping[str, Any]) -> ProposedStep:
        access = PlanAccess(str(value.get("access") or "read"))
        evidence = (
            EvidenceRequirement.VERIFIED
            if access is PlanAccess.WRITE
            else EvidenceRequirement(str(value.get("evidence") or "accepted"))
        )
        return ProposedStep(
            step_id=str(value.get("step_id") or ""),
            title=str(value.get("title") or ""),
            capability=CapabilityRequirement(
                capability_id=str(value.get("capability_id") or ""),
                access=access,
                evidence=evidence,
            ),
            arguments=dict(value.get("arguments") or {}),
            depends_on=tuple(str(item) for item in value.get("depends_on") or ()),
            risk=PlanRisk(str(value.get("risk") or "low")),
            requires_confirmation=bool(value.get("requires_confirmation")),
            max_attempts=max(1, min(int(value.get("max_attempts") or 1), 10)),
            continuation=(
                dict(value["continuation"])
                if isinstance(value.get("continuation"), Mapping)
                else None
            ),
        )

    async def create_plan(
        self,
        *,
        conversation_id: str,
        principal_id: str | None = None,
        goal: str,
        steps: Sequence[Mapping[str, Any]],
        continuation: Mapping[str, Any] | None = None,
        start: bool = True,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("External agent mode is disabled")
        proposed = [self._proposed_step(value) for value in steps]
        resolved_principal = str(
            principal_id or ""
        ).strip() or self.planner_executor._principal_from_conversation(conversation_id)
        scoped_conversation = self.planner_executor.scope_conversation(
            conversation_id,
            resolved_principal,
        )
        token = self.planner_executor.set_principal(resolved_principal)
        try:
            plan = await self.planner.create(
                route=RequestRoute.MULTI_STEP,
                conversation_id=scoped_conversation,
                goal=goal,
                proposed_steps=proposed,
                continuation=continuation,
            )
            if start:
                plan = await self.planner.resume(plan.plan_id)
            return plan.as_dict()
        finally:
            self.planner_executor.reset_principal(token)

    async def replan(
        self,
        plan_id: str,
        *,
        steps: Sequence[Mapping[str, Any]],
        goal: str | None = None,
        continuation: Mapping[str, Any] | None = None,
        start: bool = True,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("External agent mode is disabled")
        proposed = [self._proposed_step(value) for value in steps]
        existing = await self.planner.get(plan_id)
        if existing is None:
            raise KeyError(plan_id)
        principal = self.planner_executor._principal_from_conversation(existing.conversation_id)
        token = self.planner_executor.set_principal(principal)
        try:
            plan = await self.planner.replan(
                plan_id,
                proposed_steps=proposed,
                goal=goal,
                continuation=continuation,
            )
            if start:
                plan = await self.planner.resume(plan.plan_id)
            return plan.as_dict()
        finally:
            self.planner_executor.reset_principal(token)

    async def resume_plan(self, plan_id: str) -> dict[str, Any]:
        plan = await self.planner.get(plan_id)
        if plan is None:
            raise KeyError(plan_id)
        principal = self.planner_executor._principal_from_conversation(plan.conversation_id)
        token = self.planner_executor.set_principal(principal)
        try:
            return (await self.planner.resume(plan_id)).as_dict()
        finally:
            self.planner_executor.reset_principal(token)


__all__ = ["ConnectorPlannerExecutor", "ExternalAgentRuntime"]
