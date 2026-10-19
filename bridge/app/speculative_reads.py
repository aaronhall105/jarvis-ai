"""Bounded, generation-scoped speculative execution for side-effect-free reads.

The executor in this module deliberately accepts only a read projection.  It has
no reference to Jarvis's general tool registry, connector runtime, or Home
Assistant service-call surface.  Effect metadata is admission policy; the
absence of write callbacks is the structural safety boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ToolEffect(StrEnum):
    PURE_LOCAL = "PURE_LOCAL"
    READ_ONLY_EXTERNAL = "READ_ONLY_EXTERNAL"
    WRITE_REVERSIBLE = "WRITE_REVERSIBLE"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    UNKNOWN = "UNKNOWN"

    @property
    def speculative_read_safe(self) -> bool:
        return self in {self.PURE_LOCAL, self.READ_ONLY_EXTERNAL}


class SpeculationDisposition(StrEnum):
    REUSED = "REUSED"
    FALLBACK = "FALLBACK"
    MISMATCH = "MISMATCH"
    EXPIRED = "EXPIRED"
    GENERATION_MISMATCH = "GENERATION_MISMATCH"
    NOT_READY = "NOT_READY"


ReadCallback = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolEffectDefinition:
    name: str
    effect: ToolEffect = ToolEffect.UNKNOWN
    speculation_enabled: bool = False
    provider: str = ""

    @property
    def eligible(self) -> bool:
        return self.speculation_enabled and self.effect.speculative_read_safe


class ToolEffectRegistry:
    """Complete effect metadata plus an independently constructed read surface."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolEffectDefinition] = {}
        self._read_callbacks: dict[str, ReadCallback] = {}

    def classify(
        self,
        name: str,
        effect: ToolEffect,
        *,
        speculation_enabled: bool = False,
        provider: str = "",
    ) -> None:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("Tool names cannot be empty.")
        self._definitions[clean_name] = ToolEffectDefinition(
            name=clean_name,
            effect=effect,
            speculation_enabled=bool(speculation_enabled),
            provider=str(provider or "").strip(),
        )
        if not effect.speculative_read_safe:
            self._read_callbacks.pop(clean_name, None)

    def register_read(
        self,
        name: str,
        callback: ReadCallback,
        *,
        effect: ToolEffect = ToolEffect.READ_ONLY_EXTERNAL,
        enabled: bool = True,
        provider: str = "",
    ) -> None:
        if not effect.speculative_read_safe:
            raise ValueError("A speculative read callback must have a read-only effect.")
        self.classify(
            name,
            effect,
            speculation_enabled=enabled,
            provider=provider,
        )
        self._read_callbacks[name] = callback

    def definition(self, name: str) -> ToolEffectDefinition:
        return self._definitions.get(
            str(name or "").strip(),
            ToolEffectDefinition(name=str(name or "").strip()),
        )

    def read_projection(self) -> "ReadOnlyToolRegistry":
        callbacks = {
            name: callback
            for name, callback in self._read_callbacks.items()
            if self.definition(name).eligible
        }
        definitions = {name: self.definition(name) for name in callbacks}
        return ReadOnlyToolRegistry(definitions, callbacks)

    def counts(self) -> dict[str, int]:
        counts = Counter(item.effect.value for item in self._definitions.values())
        counts[ToolEffect.UNKNOWN.value] += 0
        return dict(sorted(counts.items()))

    def definitions(self) -> tuple[ToolEffectDefinition, ...]:
        return tuple(self._definitions[name] for name in sorted(self._definitions))


class ReadOnlyToolRegistry:
    """Capability-limited registry; construction excludes all write callbacks."""

    def __init__(
        self,
        definitions: Mapping[str, ToolEffectDefinition],
        callbacks: Mapping[str, ReadCallback],
    ) -> None:
        self._definitions = dict(definitions)
        self._callbacks = dict(callbacks)
        if set(self._definitions) != set(self._callbacks):
            raise ValueError("Read definitions and callbacks must have identical keys.")
        if any(not item.eligible for item in self._definitions.values()):
            raise ValueError("The read projection contains an ineligible tool.")

    def supports(self, name: str) -> bool:
        return str(name or "") in self._callbacks

    def definition(self, name: str) -> ToolEffectDefinition | None:
        return self._definitions.get(str(name or ""))

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._callbacks)

    async def execute(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        callback = self._callbacks.get(str(name or ""))
        definition = self._definitions.get(str(name or ""))
        if callback is None or definition is None or not definition.eligible:
            raise PermissionError("The requested operation is not an admitted speculative read.")
        return await callback(dict(arguments))


def _normalise_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalise_json(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalise_json(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass(frozen=True)
class SemanticReadIdentity:
    session_id: str
    conversation_id: str
    generation: int
    task_family: str
    semantic_operation: str
    tool_name: str
    arguments: Mapping[str, Any]
    reference_scope: str = ""
    provider_identity: str = ""

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "generation": int(self.generation),
            "task_family": self.task_family.strip().upper(),
            "semantic_operation": self.semantic_operation.strip().upper(),
            "tool_name": self.tool_name.strip(),
            "arguments": _normalise_json(self.arguments),
            "reference_scope": self.reference_scope.strip(),
            "provider_identity": self.provider_identity.strip(),
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProvisionalReadPlan:
    identity: SemanticReadIdentity
    input_version: int
    effect: ToolEffect
    confidence: float | None = None
    stability_evidence: str = "semantic_plan_complete"
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class SpeculativeEntry:
    speculation_id: str
    plan: ProvisionalReadPlan
    started_at: float
    expires_at: float
    task: asyncio.Task[dict[str, Any]]
    completed_at: float | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    invalidated: bool = False


@dataclass(frozen=True)
class SpeculationResolution:
    disposition: SpeculationDisposition
    result: dict[str, Any] | None = None
    speculation_id: str | None = None
    latency_saved_ms: float | None = None


class SpeculativeReadCoordinator:
    """One-candidate-per-generation orchestration and exact final-plan validation."""

    def __init__(
        self,
        tools: ReadOnlyToolRegistry,
        *,
        enabled: bool = False,
        ttl_seconds: float = 8.0,
        timeout_seconds: float = 4.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tools = tools
        self.enabled = bool(enabled)
        self.ttl_seconds = max(0.1, float(ttl_seconds))
        self.timeout_seconds = max(0.05, float(timeout_seconds))
        self._clock = clock
        self._entries: dict[tuple[str, int], SpeculativeEntry] = {}
        self._planning: set[tuple[str, int]] = set()
        self.metrics: Counter[str] = Counter()

    def reserve_planning(self, session_id: str, generation: int) -> bool:
        self.metrics["provisional_inputs_observed"] += 1
        if not self.enabled:
            self.metrics["fallback"] += 1
            return False
        key = (str(session_id), int(generation))
        if key in self._planning or key in self._entries:
            self.metrics["bounded_duplicate"] += 1
            return False
        self._planning.add(key)
        self.metrics["speculation_candidates"] += 1
        return True

    def release_planning(self, session_id: str, generation: int) -> None:
        self._planning.discard((str(session_id), int(generation)))

    async def start(self, plan: ProvisionalReadPlan) -> SpeculativeEntry | None:
        key = (plan.identity.session_id, int(plan.identity.generation))
        self._planning.discard(key)
        definition = self.tools.definition(plan.identity.tool_name)
        if (
            not self.enabled
            or definition is None
            or not definition.eligible
            or plan.effect != definition.effect
            or not plan.effect.speculative_read_safe
            or plan.identity.semantic_operation.strip().upper() not in {"READ", "QUERY"}
        ):
            self.metrics["rejected_effect_class"] += 1
            return None
        existing = self._entries.get(key)
        if existing is not None:
            if existing.plan.identity.fingerprint == plan.identity.fingerprint:
                self.metrics["bounded_duplicate"] += 1
                return existing
            existing.invalidated = True
            existing.task.cancel()
            self.metrics["cancelled"] += 1

        async def invoke() -> dict[str, Any]:
            async with asyncio.timeout(self.timeout_seconds):
                return await self.tools.execute(plan.identity.tool_name, plan.identity.arguments)

        task = asyncio.create_task(invoke())
        now = self._clock()
        entry = SpeculativeEntry(
            speculation_id=str(uuid.uuid4()),
            plan=plan,
            started_at=now,
            expires_at=now + self.ttl_seconds,
            task=task,
        )
        self._entries[key] = entry
        self.metrics["admitted"] += 1
        self.metrics["started"] += 1

        def finish(completed: asyncio.Task[dict[str, Any]]) -> None:
            entry.completed_at = self._clock()
            if completed.cancelled():
                return
            try:
                entry.result = completed.result()
                self.metrics["completed"] += 1
            except TimeoutError:
                entry.error = "timeout"
                self.metrics["expired"] += 1
            except Exception as exc:  # bounded diagnostics; never exposes provider payloads
                entry.error = type(exc).__name__
                self.metrics["errors"] += 1

        task.add_done_callback(finish)
        return entry

    async def resolve(
        self,
        authoritative: SemanticReadIdentity,
        *,
        wait_for_in_flight: bool = False,
    ) -> SpeculationResolution:
        key = (authoritative.session_id, int(authoritative.generation))
        entry = self._entries.get(key)
        if entry is None:
            if any(
                session_id == authoritative.session_id and not candidate.invalidated
                for (session_id, _), candidate in self._entries.items()
            ):
                self.metrics["generation_mismatched"] += 1
                return SpeculationResolution(SpeculationDisposition.GENERATION_MISMATCH)
            self.metrics["fallback"] += 1
            return SpeculationResolution(SpeculationDisposition.FALLBACK)
        if entry.invalidated:
            self.metrics["fallback"] += 1
            return SpeculationResolution(SpeculationDisposition.FALLBACK)
        if entry.plan.identity.fingerprint != authoritative.fingerprint:
            entry.invalidated = True
            self.metrics["mismatched"] += 1
            return SpeculationResolution(
                SpeculationDisposition.MISMATCH,
                speculation_id=entry.speculation_id,
            )
        now = self._clock()
        if now >= entry.expires_at:
            entry.invalidated = True
            self.metrics["expired"] += 1
            return SpeculationResolution(
                SpeculationDisposition.EXPIRED,
                speculation_id=entry.speculation_id,
            )
        if wait_for_in_flight and not entry.task.done():
            try:
                remaining = min(self.timeout_seconds, max(0.0, entry.expires_at - now))
                await asyncio.wait_for(asyncio.shield(entry.task), timeout=remaining)
            except (TimeoutError, asyncio.CancelledError):
                pass
        if entry.task.done() and entry.result is None and entry.error is None:
            try:
                entry.result = entry.task.result()
                entry.completed_at = entry.completed_at or self._clock()
            except asyncio.CancelledError:
                entry.invalidated = True
            except TimeoutError:
                entry.error = "timeout"
            except Exception as exc:
                entry.error = type(exc).__name__
        if not entry.task.done() or entry.result is None or entry.error:
            self.metrics["fallback"] += 1
            return SpeculationResolution(
                SpeculationDisposition.NOT_READY,
                speculation_id=entry.speculation_id,
            )
        self.metrics["reused"] += 1
        return SpeculationResolution(
            SpeculationDisposition.REUSED,
            result=dict(entry.result),
            speculation_id=entry.speculation_id,
            latency_saved_ms=max(0.0, (now - entry.completed_at) * 1000)
            if entry.completed_at is not None
            else None,
        )

    def invalidate_session(self, session_id: str, generation: int | None = None) -> int:
        invalidated = 0
        for (candidate_session, candidate_generation), entry in list(self._entries.items()):
            if candidate_session != str(session_id):
                continue
            if generation is not None and candidate_generation == int(generation):
                continue
            if not entry.invalidated:
                entry.invalidated = True
                if not entry.task.done():
                    entry.task.cancel()
                invalidated += 1
        if invalidated:
            self.metrics["cancelled"] += invalidated
        return invalidated

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ttl_seconds": self.ttl_seconds,
            "timeout_seconds": self.timeout_seconds,
            "eligible_tools": sorted(self.tools.names),
            "metrics": dict(sorted(self.metrics.items())),
        }


CORE_TOOL_EFFECTS: dict[str, ToolEffect] = {
    # Home and presence reads.
    "search_home": ToolEffect.READ_ONLY_EXTERNAL,
    "inspect_home_item": ToolEffect.READ_ONLY_EXTERNAL,
    "query_home": ToolEffect.READ_ONLY_EXTERNAL,
    "search_entity_states": ToolEffect.READ_ONLY_EXTERNAL,
    "list_area_states": ToolEffect.READ_ONLY_EXTERNAL,
    "get_entity_state": ToolEffect.READ_ONLY_EXTERNAL,
    "inspect_presence": ToolEffect.READ_ONLY_EXTERNAL,
    "explain_home_action_receipt": ToolEffect.PURE_LOCAL,
    # Administrative and local inspection reads. These are classified but not
    # enabled for voice speculation because they may expose privileged data.
    "list_admin_items": ToolEffect.READ_ONLY_EXTERNAL,
    "get_admin_item_config": ToolEffect.READ_ONLY_EXTERNAL,
    "code_list": ToolEffect.PURE_LOCAL,
    "code_read": ToolEffect.PURE_LOCAL,
    "code_search": ToolEffect.PURE_LOCAL,
    "git_status": ToolEffect.PURE_LOCAL,
    "git_diff": ToolEffect.PURE_LOCAL,
    "git_log": ToolEffect.PURE_LOCAL,
    # External reads remain ineligible until each provider's mutation semantics
    # has been audited. Classification alone never grants admission.
    "web_search": ToolEffect.READ_ONLY_EXTERNAL,
    "web_fetch": ToolEffect.READ_ONLY_EXTERNAL,
    "deep_research": ToolEffect.READ_ONLY_EXTERNAL,
    "list_external_monitors": ToolEffect.READ_ONLY_EXTERNAL,
    "check_recent_gmail_reply": ToolEffect.READ_ONLY_EXTERNAL,
    # Reversible or user-visible writes.
    "control_referenced_set": ToolEffect.WRITE_REVERSIBLE,
    "control_area_lights": ToolEffect.WRITE_REVERSIBLE,
    "control_device": ToolEffect.WRITE_REVERSIBLE,
    "run_home_routine": ToolEffect.WRITE,
    "run_media_shortcut": ToolEffect.WRITE_REVERSIBLE,
    "control_media_player": ToolEffect.WRITE_REVERSIBLE,
    "set_media_volume": ToolEffect.WRITE_REVERSIBLE,
    "execute_home_action": ToolEffect.WRITE_REVERSIBLE,
    "execute_home_parameter": ToolEffect.WRITE_REVERSIBLE,
    "send_mobile_notification": ToolEffect.WRITE,
    "announce_message": ToolEffect.WRITE,
    "continue_proactive_speech": ToolEffect.WRITE_REVERSIBLE,
    "propose_admin_change": ToolEffect.WRITE,
    "prepare_gmail_message": ToolEffect.WRITE,
    "create_personal_plan": ToolEffect.WRITE,
    "create_external_monitor": ToolEffect.WRITE,
    "cancel_external_monitor": ToolEffect.WRITE,
    "save_memory": ToolEffect.WRITE,
    "forget_memory": ToolEffect.DESTRUCTIVE,
    "google_integration": ToolEffect.UNKNOWN,
}


def classify_core_tools(registry: ToolEffectRegistry) -> None:
    """Install explicit effects without enabling any operation for speculation."""

    for name, effect in CORE_TOOL_EFFECTS.items():
        registry.classify(name, effect)
