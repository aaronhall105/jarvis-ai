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
    ActionReceiptStore,
    CapabilityAccess,
    CapabilityMetadata,
    CapabilityRequest,
    ConfirmationMode,
    ConnectorRegistry,
    ExecutionStatus,
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
    def is_external_request(text: str) -> bool:
        lowered = str(text or "").casefold()
        words = {word.strip(".,!?()[]{}:;\"'") for word in lowered.split()}
        return bool(words & _EXTERNAL_SERVICE_WORDS) or any(
            phrase in lowered for phrase in (*_CURRENT_WEB_PHRASES, *_EXTERNAL_REQUEST_PHRASES)
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
    ) -> str | None:
        if not self.enabled or not self.is_external_request(text):
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
        )

    async def unavailable_service_reply(
        self,
        text: str,
        *,
        principal_id: str | None = None,
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
                return f"{label} is unavailable — {reason}."
        return None

    async def openai_tools(
        self,
        text: str,
        *,
        principal_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not self.enabled or not self.is_external_request(text):
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
        definitions: list[dict[str, Any]] = []
        google_tool = google_model_tool(sorted(executable))
        if google_tool is not None and not monitor_management_intent:
            definitions.append(google_tool)
        if "web.search" in executable and not monitor_management_intent:
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
            "http://" in lowered
            or "https://" in lowered
            or any(word in lowered for word in ("open the page", "fetch", "read this page"))
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
        if "web.search" in executable and any(
            word in lowered for word in ("research", "investigate", "compare", "shortlist")
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
        if not direct_literal_email_send and (
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
    ) -> dict[str, Any]:
        if name == GOOGLE_MODEL_TOOL:
            return await self._execute_google_model_tool(
                arguments,
                conversation_id=conversation_id,
                principal_id=principal_id,
                request_id=request_id,
                user_text=user_text,
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
                if str(step.get("access") or "read") == "write" and not self._write_authorized(
                    capability_id,
                    user_text,
                ):
                    raise ValueError(
                        f"The user's request did not explicitly authorize {capability_id}"
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
        raise ValueError(f"Unsupported external agent tool: {name}")

    @staticmethod
    def _write_authorized(capability_id: str, user_text: str) -> bool:
        """Recognize explicit user authority; model/provider content cannot grant it."""

        request_prefix = str(user_text or "")[:5_000]
        for separator in ("\n", "\r", ":", "?", "!", '"'):
            request_prefix = request_prefix.partition(separator)[0]
        normalised = "".join(
            character.casefold() if character.isalnum() else " " for character in request_prefix
        )
        words = normalised.split()[:32]
        content_markers = {"body", "contains", "reads", "said", "says", "saying", "tells"}
        for index, word in enumerate(words):
            if word in content_markers:
                words = words[:index]
                break
        word_set = set(words)
        required: Mapping[str, frozenset[str]] = {
            "gmail.draft": frozenset({"draft", "compose", "write", "send"}),
            "gmail.reply": frozenset({"reply", "respond", "draft"}),
            "gmail.send": frozenset({"send"}),
            "gmail.forward": frozenset({"forward"}),
            "gmail.archive": frozenset({"archive"}),
            "calendar.create": frozenset({"add", "book", "create", "put", "schedule"}),
            "calendar.update": frozenset({"change", "move", "reschedule", "update"}),
            "calendar.cancel": frozenset({"cancel", "delete", "remove"}),
        }
        verbs = required.get(capability_id)
        return verbs is not None and bool(word_set & verbs)

    @staticmethod
    def _literal_user_emails(user_text: str) -> frozenset[str]:
        """Return complete literal email addresses stated in the user's request."""

        pattern = re.compile(
            r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
            r"(?![A-Za-z0-9.-])"
        )
        addresses: set[str] = set()
        for match in pattern.finditer(str(user_text or "")):
            try:
                addresses.add(GoogleConnector._recipient(match.group(0)).casefold())
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
