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
import sqlite3
import uuid
import weakref
from collections.abc import Awaitable, Callable, Mapping, Sequence
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

    async def snapshot(self) -> Mapping[str, CapabilityState]:
        live_rows = await self.registry.capability_snapshot()
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

    async def providers_snapshot(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        providers = await self.registry.status_snapshot(refresh=refresh)
        registered = {str(item["provider_id"]) for item in providers}
        for entry in UNAVAILABLE_CONNECTOR_CATALOG.values():
            if entry.provider_id in registered:
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

    async def capability_snapshot(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        rows = await self.registry.capability_snapshot(refresh=refresh)
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

    async def health_snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        core = await self.registry.health_snapshot(refresh=refresh)
        database = await self.database_health_snapshot()
        return {
            **core,
            "healthy": bool(core.get("healthy")) and database["healthy"],
            "database": database,
            "providers": await self.providers_snapshot(refresh=False),
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

    async def model_context(self, text: str) -> str | None:
        if not self.enabled or not self.is_external_request(text):
            return None
        providers = await self.providers_snapshot()
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

    async def unavailable_service_reply(self, text: str) -> str | None:
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
        statuses = {str(item["provider_id"]): item for item in await self.providers_snapshot()}
        for provider_id, label in requested:
            status = statuses.get(provider_id)
            if status is None or not status.get("available"):
                reason = str((status or {}).get("health_reason") or "No provider is configured")
                return f"{label} is unavailable — {reason}."
        return None

    async def openai_tools(self, text: str) -> list[dict[str, Any]]:
        if not self.enabled or not self.is_external_request(text):
            return []
        executable = {item.capability_id for item in await self.registry.executable_capabilities()}
        lowered = str(text or "").casefold()
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
        if any(
            phrase in lowered
            for phrase in (
                " and ",
                "sort it",
                "sort me",
                "sort this",
                "plan it",
                "organise",
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
    ) -> dict[str, Any]:
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
            plan = await self.create_plan(
                conversation_id=conversation_id,
                goal=str(arguments.get("goal") or ""),
                steps=[dict(item) for item in steps if isinstance(item, Mapping)],
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
        request_id: str | None = None,
    ) -> dict[str, Any]:
        creator = self._monitor_creator
        if creator is None:
            raise RuntimeError("Durable external monitor creation is unavailable")
        scoped_conversation = str(conversation_id).strip()
        if not scoped_conversation:
            raise ValueError("External monitors require a conversation")
        metadata = await self.registry.get_capability(str(capability_id).strip(), refresh=True)
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
        payload: dict[str, Any] = {
            "provider": metadata.provider_id,
            "capability_id": metadata.capability_id,
            "arguments": dict(arguments or {}),
            "comparison": normalised_comparison,
            "polling_interval_seconds": interval,
            "max_attempts": max(1, min(int(max_attempts), 10)),
            "conversation_id": scoped_conversation,
            "principal_id": str(principal_id or "").strip() or None,
            "value_path": selected_path,
            "poll_count": 0,
            "max_polls": int(metadata.maximum_monitor_polls or 1),
            "expires_at": (
                now + timedelta(seconds=int(metadata.monitor_ttl_seconds or interval))
            ).isoformat(),
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
            interval,
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
            "label",
            "max_attempts",
            "max_polls",
        )
        return all(stored.get(key) == requested.get(key) for key in identity_keys)

    async def evaluate_external_monitor(self, monitor: Mapping[str, Any]) -> dict[str, Any]:
        """Re-run one explicitly repeatable read for FollowUpEngine.

        The worker owns comparison and delivery.  This callback returns only a
        verified provider observation and never declares the condition changed.
        """

        capability_id = str(monitor.get("capability_id") or "").strip()
        provider_id = str(monitor.get("provider") or "").strip()
        metadata = await self.registry.get_capability(capability_id, refresh=True)
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
        goal: str,
        steps: Sequence[Mapping[str, Any]],
        continuation: Mapping[str, Any] | None = None,
        start: bool = True,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("External agent mode is disabled")
        proposed = [self._proposed_step(value) for value in steps]
        plan = await self.planner.create(
            route=RequestRoute.MULTI_STEP,
            conversation_id=conversation_id,
            goal=goal,
            proposed_steps=proposed,
            continuation=continuation,
        )
        if start:
            plan = await self.planner.resume(plan.plan_id)
        return plan.as_dict()

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
        plan = await self.planner.replan(
            plan_id,
            proposed_steps=proposed,
            goal=goal,
            continuation=continuation,
        )
        if start:
            plan = await self.planner.resume(plan.plan_id)
        return plan.as_dict()


__all__ = ["ConnectorPlannerExecutor", "ExternalAgentRuntime"]
