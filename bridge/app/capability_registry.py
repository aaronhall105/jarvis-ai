"""Runtime capability truth and persistent action receipts for Jarvis Core."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HealthCheck = Callable[[], Awaitable[Mapping[str, Any] | bool]]

_SECRET_KEY = re.compile(r"(?:password|passcode|token|secret|api[_-]?key|authorization)", re.I)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Keep receipts useful without retaining credentials or unbounded payloads."""
    if depth >= 4:
        return "[truncated]"
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: "[redacted]" if _SECRET_KEY.search(str(key)) else _safe_value(item, depth=depth + 1)
            for key, item in list(value.items())[:50]
        }
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth=depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(value)[:1000]


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    provider_id: str
    description: str
    configured: bool
    health_check: HealthCheck | None = field(default=None, compare=False, repr=False)
    setup_hint: str | None = None


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    capability_id: str
    description: str
    provider: str
    mode: str
    risk: str = "low"
    confirmation: str = "never"
    asynchronous: bool = False
    verification: str = "provider_result"
    scopes: tuple[str, ...] = ()
    tool_names: tuple[str, ...] = ()
    enabled: bool = True

    def public(self) -> dict[str, Any]:
        result = asdict(self)
        result["scopes"] = list(self.scopes)
        result["tool_names"] = list(self.tool_names)
        return result


class ActionReceiptStore:
    """SQLite audit trail for execution claims, safe across Core restarts."""

    VALID_INITIAL = {"started", "scheduled", "rejected"}

    def __init__(self, database_path: str) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialise(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS action_receipts (
                    action_id TEXT PRIMARY KEY,
                    capability_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    conversation_id TEXT,
                    actor_key TEXT,
                    target_json TEXT NOT NULL,
                    requested_action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    external_reference TEXT,
                    verified INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_action_receipts_conversation
                    ON action_receipts(conversation_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_action_receipts_status
                    ON action_receipts(status, started_at DESC);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["target"] = json.loads(result.pop("target_json") or "{}")
        result["result"] = json.loads(result.pop("result_json") or "{}")
        result["verified"] = bool(result["verified"])
        return result

    async def begin(
        self,
        *,
        capability_id: str,
        provider: str,
        tool_name: str,
        requested_action: str,
        target: Mapping[str, Any] | None = None,
        conversation_id: str | None = None,
        actor_key: str | None = None,
        status: str = "started",
        action_id: str | None = None,
    ) -> dict[str, Any]:
        if status not in self.VALID_INITIAL:
            raise ValueError(f"Unsupported initial receipt status: {status}")
        resolved_id = action_id or str(uuid.uuid4())
        safe_target = _safe_value(dict(target or {}))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO action_receipts (
                        action_id, capability_id, provider, tool_name,
                        conversation_id, actor_key, target_json, requested_action,
                        status, started_at, verified
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        resolved_id,
                        capability_id,
                        provider,
                        tool_name,
                        conversation_id,
                        actor_key,
                        json.dumps(safe_target, ensure_ascii=False, separators=(",", ":")),
                        str(requested_action or tool_name)[:200],
                        status,
                        _utc_now(),
                    ),
                )
        except sqlite3.IntegrityError:
            # A caller may use a deterministic action ID to close a crash window.
            # Replaying that exact begin operation returns the durable original.
            if action_id is not None:
                existing = await self.get(resolved_id)
                if existing is not None and all(
                    (
                        existing.get("capability_id") == capability_id,
                        existing.get("provider") == provider,
                        existing.get("tool_name") == tool_name,
                        existing.get("conversation_id") == conversation_id,
                        existing.get("actor_key") == actor_key,
                        existing.get("target") == safe_target,
                        existing.get("requested_action")
                        == str(requested_action or tool_name)[:200],
                    )
                ):
                    existing["idempotent_replay"] = True
                    return existing
            raise
        return await self.get(resolved_id) or {}

    @staticmethod
    def _final_status(result: Mapping[str, Any]) -> tuple[str, bool]:
        success = result.get("success") is True
        verified = result.get("verified") is True
        if result.get("outcome_unknown") is True:
            return "outcome_unknown", False
        if success and verified:
            return "verified", True
        if success and result.get("verified") is False:
            return "accepted_unverified", False
        if success:
            return "completed", False
        if (
            result.get("command_sent")
            or result.get("command_accepted")
            or result.get("changed")
        ):
            return "verification_failed", False
        return "failed", False

    async def complete(
        self,
        action_id: str,
        result: Mapping[str, Any],
        *,
        status: str | None = None,
        verified: bool | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        resolved_status, inferred_verified = self._final_status(result)
        resolved_status = status or resolved_status
        resolved_verified = inferred_verified if verified is None else bool(verified)
        external_reference = next(
            (
                str(result[key])
                for key in (
                    "external_reference",
                    "job_id",
                    "event_id",
                    "candidate_id",
                    "proposal_id",
                    "memory_id",
                )
                if result.get(key) not in {None, ""}
            ),
            None,
        )
        safe_result = _safe_value(dict(result))
        safe_error = str(error or result.get("error") or "").strip()[:1000] or None
        with self._connect() as connection:
            changed = connection.execute(
                """
                UPDATE action_receipts SET
                    status=?, completed_at=?, external_reference=?, verified=?,
                    result_json=?, error=?
                WHERE action_id=? AND completed_at IS NULL
                """,
                (
                    resolved_status,
                    _utc_now(),
                    external_reference,
                    int(resolved_verified),
                    json.dumps(safe_result, ensure_ascii=False, separators=(",", ":")),
                    safe_error,
                    action_id,
                ),
            ).rowcount
        if not changed:
            existing = await self.get(action_id)
            if existing is None:
                raise KeyError(f"Unknown action receipt: {action_id}")
            return existing
        return await self.get(action_id) or {}

    async def get(self, action_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM action_receipts WHERE action_id=?", (action_id,)
            ).fetchone()
        return self._row(row) if row else None

    async def recent(
        self,
        *,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        with self._connect() as connection:
            if conversation_id:
                rows = connection.execute(
                    "SELECT * FROM action_receipts WHERE conversation_id=? ORDER BY started_at DESC LIMIT ?",
                    (conversation_id, safe_limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM action_receipts ORDER BY started_at DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
        return [self._row(row) for row in rows]

    async def summary(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM action_receipts GROUP BY status"
            ).fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        return {"total": sum(counts.values()), "statuses": counts}


class CapabilityRegistry:
    """Single source of truth for configured providers and executable tools."""

    def __init__(self, receipts: ActionReceiptStore) -> None:
        self.receipts = receipts
        self._providers: dict[str, ProviderDefinition] = {}
        self._capabilities: dict[str, CapabilityDefinition] = {}
        self._tools: dict[str, str] = {}
        self._health_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def register_provider(self, provider: ProviderDefinition) -> None:
        if provider.provider_id in self._providers:
            raise ValueError(f"Duplicate provider: {provider.provider_id}")
        self._providers[provider.provider_id] = provider

    def register(self, capability: CapabilityDefinition) -> None:
        if capability.capability_id in self._capabilities:
            raise ValueError(f"Duplicate capability: {capability.capability_id}")
        if capability.provider not in self._providers:
            raise ValueError(f"Unknown capability provider: {capability.provider}")
        for tool_name in capability.tool_names:
            if tool_name in self._tools:
                raise ValueError(f"Tool already registered: {tool_name}")
        self._capabilities[capability.capability_id] = capability
        for tool_name in capability.tool_names:
            self._tools[tool_name] = capability.capability_id

    def capability_for_tool(self, tool_name: str) -> CapabilityDefinition | None:
        capability_id = self._tools.get(tool_name)
        return self._capabilities.get(capability_id) if capability_id else None

    def tool_available(self, tool_name: str) -> tuple[bool, str | None]:
        capability = self.capability_for_tool(tool_name)
        if capability is None:
            return False, "The tool is not registered as a real capability."
        provider = self._providers[capability.provider]
        if not capability.enabled:
            return False, "The capability is disabled."
        if not provider.configured:
            return False, provider.setup_hint or "The provider is not configured."
        return True, None

    async def _provider_status(self, provider: ProviderDefinition) -> dict[str, Any]:
        cached = self._health_cache.get(provider.provider_id)
        if cached is not None and time.monotonic() - cached[0] <= 5.0:
            return dict(cached[1])
        status: dict[str, Any] = {
            "provider_id": provider.provider_id,
            "description": provider.description,
            "configured": provider.configured,
            "healthy": None,
            "available": False,
            "reason": None,
            "setup_hint": provider.setup_hint,
        }
        if not provider.configured:
            status["reason"] = provider.setup_hint or "Provider is not configured."
            self._health_cache[provider.provider_id] = (time.monotonic(), dict(status))
            return status
        if provider.health_check is None:
            status.update({"healthy": None, "available": True, "reason": "Health is checked on use."})
            self._health_cache[provider.provider_id] = (time.monotonic(), dict(status))
            return status
        try:
            result = await asyncio.wait_for(provider.health_check(), timeout=8.0)
            if isinstance(result, Mapping):
                healthy = bool(result.get("healthy", result.get("connected", False)))
                reason = result.get("reason") or result.get("message")
            else:
                healthy, reason = bool(result), None
            status.update({"healthy": healthy, "available": healthy, "reason": reason})
        except Exception as exc:
            status.update({"healthy": False, "available": False, "reason": str(exc)[:300]})
        self._health_cache[provider.provider_id] = (time.monotonic(), dict(status))
        return status

    async def tool_available_now(self, tool_name: str) -> tuple[bool, str | None]:
        available, reason = self.tool_available(tool_name)
        if not available:
            return available, reason
        capability = self.capability_for_tool(tool_name)
        if capability is None:
            return False, "The tool is not registered as a real capability."
        provider_status = await self._provider_status(
            self._providers[capability.provider]
        )
        if not provider_status["available"]:
            return False, str(
                provider_status.get("reason")
                or "The capability provider is unhealthy."
            )
        return True, None

    async def snapshot(self) -> dict[str, Any]:
        providers = await asyncio.gather(
            *(self._provider_status(item) for item in self._providers.values())
        )
        provider_lookup = {item["provider_id"]: item for item in providers}
        capabilities: list[dict[str, Any]] = []
        for capability in self._capabilities.values():
            provider = provider_lookup[capability.provider]
            public = capability.public()
            public.update(
                {
                    "configured": provider["configured"],
                    "available": bool(capability.enabled and provider["available"]),
                    "health": provider["healthy"],
                    "unavailable_reason": None if capability.enabled and provider["available"] else provider["reason"],
                }
            )
            capabilities.append(public)
        return {
            "providers": providers,
            "capabilities": capabilities,
            "available": sum(1 for item in capabilities if item["available"]),
            "unavailable": sum(1 for item in capabilities if not item["available"]),
            "checked_at": _utc_now(),
        }

    async def begin_tool_action(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        conversation_id: str,
        actor_key: str,
        action_id: str | None = None,
    ) -> dict[str, Any] | None:
        capability = self.capability_for_tool(tool_name)
        if capability is None or capability.mode == "read":
            return None
        target_keys = (
            "entity_id",
            "area_id",
            "recipient",
            "target",
            "shortcut",
            "domain",
            "item_key",
            "config_key",
            "proposal_id",
            "volume_percent",
        )
        target = {key: arguments[key] for key in target_keys if arguments.get(key) not in {None, ""}}
        canonical_arguments = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        target["argument_fingerprint"] = hashlib.sha256(
            canonical_arguments.encode()
        ).hexdigest()
        return await self.receipts.begin(
            capability_id=capability.capability_id,
            provider=capability.provider,
            tool_name=tool_name,
            requested_action=str(arguments.get("action") or tool_name),
            target=target,
            conversation_id=conversation_id,
            actor_key=actor_key,
            action_id=action_id,
        )

    async def finish_tool_action(
        self,
        receipt: Mapping[str, Any] | None,
        result: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if not receipt:
            return None
        checked_result = dict(result)
        capability = self._capabilities.get(str(receipt.get("capability_id") or ""))
        if checked_result.get("verified") is True and capability is not None:
            verification_evidence = True
            if capability.verification == "configuration_readback":
                verification_evidence = checked_result.get("stored_verified") is True
            elif capability.verification == "post_state":
                verification_evidence = bool(
                    checked_result.get("already_in_target_state") is True
                    or checked_result.get("current_state") not in {None, "", "unknown"}
                    or checked_result.get("current_volume_percent") is not None
                    or checked_result.get("verified_entity_ids")
                    or checked_result.get("entities")
                )
            elif capability.verification == "provider_acceptance":
                verification_evidence = (
                    checked_result.get("delivery_confirmed") is True
                )
            elif capability.verification == "persisted_job":
                verification_evidence = bool(checked_result.get("job_id"))
            if not verification_evidence:
                checked_result["verified"] = False
                checked_result["verification_policy_failed"] = True
        return await self.receipts.complete(
            str(receipt["action_id"]),
            checked_result,
        )


def register_standard_capabilities(
    registry: CapabilityRegistry,
    *,
    home_assistant_configured: bool,
    model_configured: bool,
    code_awareness_configured: bool,
    home_assistant_health: HealthCheck | None = None,
    followup_health: HealthCheck | None = None,
) -> None:
    """Register current production functions and honest future connector boundaries."""
    providers = (
        ProviderDefinition("core", "Jarvis Core local services", True),
        ProviderDefinition(
            "followup",
            "Jarvis durable follow-up worker",
            True,
            health_check=followup_health,
        ),
        ProviderDefinition("openai", "Configured model provider", model_configured),
        ProviderDefinition(
            "homeassistant",
            "Home Assistant",
            home_assistant_configured,
            health_check=home_assistant_health,
            setup_hint="Configure HOME_ASSISTANT_URL and HOME_ASSISTANT_TOKEN.",
        ),
        ProviderDefinition("code", "Read-only mounted source inspection", code_awareness_configured),
        ProviderDefinition("web", "Live web research provider", False, setup_hint="No live web research provider is configured."),
        ProviderDefinition("browser", "Browser automation provider", False, setup_hint="No browser automation provider is configured."),
        ProviderDefinition("email", "Email provider", False, setup_hint="No email account connector is configured."),
        ProviderDefinition("calendar", "Calendar provider", False, setup_hint="No calendar account connector is configured."),
        ProviderDefinition("social", "Social media provider", False, setup_hint="No authorised social media connector is configured."),
        ProviderDefinition("messaging", "Messaging provider", False, setup_hint="No messaging account connector is configured."),
    )
    for provider in providers:
        registry.register_provider(provider)

    capabilities = (
        CapabilityDefinition("core.conversation", "Persistent conversation", "core", "read"),
        CapabilityDefinition("core.memory.read", "Read scoped personal memory", "core", "read", scopes=("memory:read",), tool_names=("search_memory",)),
        CapabilityDefinition("core.memory.write", "Save scoped personal memory", "core", "write", confirmation="explicit_request", scopes=("memory:write",), tool_names=("save_memory",)),
        CapabilityDefinition("core.memory.delete", "Delete scoped personal memory", "core", "write", confirmation="explicit_request", scopes=("memory:delete",), tool_names=("forget_memory",)),
        CapabilityDefinition("core.followup.schedule", "Persistent same-chat follow-up jobs", "followup", "write", asynchronous=True, verification="persisted_job"),
        CapabilityDefinition("homeassistant.read", "Fresh entity, area and presence evidence", "homeassistant", "read", tool_names=("search_entity_states", "get_entity_state", "list_area_states", "inspect_presence")),
        CapabilityDefinition("homeassistant.control", "Verified light and switch control", "homeassistant", "write", risk="user_configurable", verification="post_state", tool_names=("control_device", "control_area_lights")),
        CapabilityDefinition("homeassistant.routine", "Run an existing safe routine", "homeassistant", "write", risk="user_configurable", verification="provider_result", tool_names=("run_home_routine", "run_media_shortcut")),
        CapabilityDefinition("homeassistant.media", "Control configured media players", "homeassistant", "write", risk="user_configurable", verification="post_state", tool_names=("control_media_player", "set_media_volume")),
        CapabilityDefinition("homeassistant.notify", "Send configured household notifications", "homeassistant", "write", risk="moderate", verification="provider_acceptance", tool_names=("send_mobile_notification", "announce_message")),
        CapabilityDefinition("homeassistant.admin", "Staged and confirmed persistent HA configuration changes", "homeassistant", "write", risk="high", confirmation="always", verification="configuration_readback", tool_names=("propose_admin_change", "apply_admin_change", "cancel_admin_change")),
        CapabilityDefinition("code.inspect", "Read-only production source inspection", "code", "read"),
        CapabilityDefinition("web.search", "Live multi-source web search", "web", "read"),
        CapabilityDefinition("browser.navigate", "State-aware browser automation", "browser", "write", risk="moderate", confirmation="policy"),
        CapabilityDefinition("email.read", "Read connected email", "email", "read", scopes=("email:read",)),
        CapabilityDefinition("email.send", "Send email", "email", "write", risk="high", confirmation="always", scopes=("email:send",)),
        CapabilityDefinition("calendar.read", "Read connected calendars", "calendar", "read", scopes=("calendar:read",)),
        CapabilityDefinition("calendar.create", "Create calendar events", "calendar", "write", risk="moderate", confirmation="policy", scopes=("calendar:write",)),
        CapabilityDefinition("social.read_metrics", "Read authorised social metrics", "social", "read", scopes=("social:read",)),
        CapabilityDefinition("social.post", "Publish to an authorised social account", "social", "write", risk="high", confirmation="always", scopes=("social:write",)),
        CapabilityDefinition("messaging.send", "Send an authorised message", "messaging", "write", risk="high", confirmation="always", scopes=("messaging:send",)),
    )
    for capability in capabilities:
        registry.register(capability)
