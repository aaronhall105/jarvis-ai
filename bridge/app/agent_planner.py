"""Durable, provider-neutral orchestration for multi-step personal-agent goals.

This module deliberately does not know about connector implementations.  A caller
supplies a small capability executor which can describe the capabilities available
right now and execute one structured request.  The planner owns dependency ordering,
approval gates, durable state, evidence requirements, and conservative recovery.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from app.connectors.credentials import redact_secrets, redact_text


JsonObject = dict[str, Any]


class RequestRoute(str, Enum):
    """Explicit upstream routing decision.

    ``SIMPLE_DIRECT`` is intentionally rejected by :meth:`PersonalAgentPlanner.create`.
    Direct deterministic commands belong on the fast path and must never be inflated
    into durable agent plans.
    """

    SIMPLE_DIRECT = "simple_direct"
    MULTI_STEP = "multi_step"


class CapabilityAccess(str, Enum):
    READ = "read"
    WRITE = "write"


class RiskLevel(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceRequirement(str, Enum):
    """Evidence needed before a step may be recorded as successful."""

    ACCEPTED = "accepted"
    VERIFIED = "verified"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    OUTCOME_UNKNOWN = "outcome_unknown"
    CANCELLED = "cancelled"


class PlanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ConfirmationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class PlanValidationError(ValueError):
    """A proposed plan is malformed or unsafe to persist."""


class DirectExecutionRequired(PlanValidationError):
    """Raised when a simple direct request is incorrectly sent to the planner."""


@dataclass(frozen=True)
class CapabilityState:
    """Small provider-neutral capability view returned by an executor snapshot."""

    capability_id: str
    available: bool
    healthy: bool = True
    readable: bool = True
    writable: bool = False
    requires_confirmation: bool = False
    supports_verification: bool = False
    reason: str | None = None

    def permits(self, access: CapabilityAccess) -> bool:
        if access is CapabilityAccess.READ:
            return self.readable
        return self.writable


@dataclass(frozen=True)
class CapabilityRequirement:
    capability_id: str
    access: CapabilityAccess = CapabilityAccess.READ
    evidence: EvidenceRequirement = EvidenceRequirement.ACCEPTED


@dataclass(frozen=True)
class ProposedStep:
    """Structured step proposed by an intent/model layer and validated by code."""

    step_id: str
    title: str
    capability: CapabilityRequirement
    arguments: Mapping[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    risk: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = False
    max_attempts: int = 1
    continuation: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class StepFailure:
    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapabilityExecutionRequest:
    plan_id: str
    step_id: str
    action_id: str
    conversation_id: str
    capability_id: str
    access: CapabilityAccess
    arguments: Mapping[str, Any]

    @property
    def idempotency_key(self) -> str:
        """Stable across safe retries of the same persisted step."""

        return self.action_id


@dataclass(frozen=True)
class CapabilityExecutionResult:
    """Authoritative executor result; prose is never interpreted as evidence."""

    status: ExecutionStatus
    result: Mapping[str, Any] = field(default_factory=dict)
    accepted: bool = False
    verified: bool = False
    action_receipt: Mapping[str, Any] | None = None
    error_code: str | None = None
    error: str | None = None
    retryable: bool = False
    continuation: Mapping[str, Any] | None = None


class CapabilityExecutor(Protocol):
    """Only the live capability boundary required by the planner."""

    async def snapshot(self) -> Mapping[str, CapabilityState]: ...

    async def execute(self, request: CapabilityExecutionRequest) -> CapabilityExecutionResult: ...

    async def reconcile(
        self, request: CapabilityExecutionRequest
    ) -> CapabilityExecutionResult | None: ...


@dataclass
class PlanStep:
    step_id: str
    title: str
    capability: CapabilityRequirement
    arguments: JsonObject
    depends_on: tuple[str, ...]
    risk: RiskLevel
    required_confirmation: bool
    confirmation_status: ConfirmationStatus
    max_attempts: int
    continuation: JsonObject | None
    action_id: str
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    result: JsonObject | None = None
    failure: StepFailure | None = None
    action_receipt: JsonObject | None = None
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class AgentPlan:
    plan_id: str
    conversation_id: str
    goal: str
    status: PlanStatus
    steps: list[PlanStep]
    continuation: JsonObject | None
    created_at: str
    updated_at: str

    def step(self, step_id: str) -> PlanStep:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        raise KeyError(step_id)

    @property
    def completed(self) -> bool:
        return self.status is PlanStatus.COMPLETED

    def as_dict(self) -> JsonObject:
        """Return the durable, API-safe representation of this plan."""

        return _plan_to_dict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_object(value: Mapping[str, Any] | None, *, name: str) -> JsonObject | None:
    """Validate and detach caller-owned JSON data before durable storage."""

    if value is None:
        return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"{name} must contain only JSON-compatible values") from exc
    if not isinstance(decoded, dict):
        raise PlanValidationError(f"{name} must be an object")
    return decoded


def _reject_secret_material(value: Any, *, name: str) -> None:
    """Refuse caller-controlled credentials before a plan reaches SQLite."""

    if redact_secrets(value) != value:
        raise PlanValidationError(f"{name} may not contain credentials or secrets")


def _argument_references(value: Any) -> tuple[str, ...]:
    """Validate and collect explicit data-only references in step arguments."""

    references: list[str] = []

    def visit(item: Any, depth: int) -> None:
        if depth > 20:
            raise PlanValidationError("Step argument references are nested too deeply")
        if isinstance(item, Mapping):
            if "$from_step" in item:
                if set(item) != {"$from_step", "path"}:
                    raise PlanValidationError(
                        "A step result reference may contain only $from_step and path"
                    )
                step_id = str(item.get("$from_step") or "").strip()
                path = item.get("path")
                if not step_id:
                    raise PlanValidationError("A step result reference requires a step ID")
                if not isinstance(path, str) or not path.strip():
                    raise PlanValidationError("A step result reference requires a data path")
                parts = path.split(".")
                if len(parts) > 20 or any(not part for part in parts):
                    raise PlanValidationError("A step result reference path is invalid")
                references.append(step_id)
                return
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)
    return tuple(references)


def _failure_to_dict(failure: StepFailure | None) -> JsonObject | None:
    if failure is None:
        return None
    return {
        "code": failure.code,
        "message": failure.message,
        "retryable": failure.retryable,
        "details": dict(failure.details),
    }


def _failure_from_dict(value: Mapping[str, Any] | None) -> StepFailure | None:
    if value is None:
        return None
    return StepFailure(
        code=str(value.get("code") or "unknown_failure"),
        message=str(value.get("message") or "The step failed."),
        retryable=bool(value.get("retryable")),
        details=dict(value.get("details") or {}),
    )


def _step_to_dict(step: PlanStep) -> JsonObject:
    return {
        "step_id": step.step_id,
        "title": step.title,
        "capability": {
            "capability_id": step.capability.capability_id,
            "access": step.capability.access.value,
            "evidence": step.capability.evidence.value,
        },
        "arguments": step.arguments,
        "depends_on": list(step.depends_on),
        "risk": step.risk.value,
        "required_confirmation": step.required_confirmation,
        "confirmation_status": step.confirmation_status.value,
        "max_attempts": step.max_attempts,
        "continuation": step.continuation,
        "action_id": step.action_id,
        "status": step.status.value,
        "attempts": step.attempts,
        "result": step.result,
        "failure": _failure_to_dict(step.failure),
        "action_receipt": step.action_receipt,
        "started_at": step.started_at,
        "completed_at": step.completed_at,
    }


def _step_from_dict(value: Mapping[str, Any]) -> PlanStep:
    capability = dict(value.get("capability") or {})
    return PlanStep(
        step_id=str(value["step_id"]),
        title=str(value["title"]),
        capability=CapabilityRequirement(
            capability_id=str(capability["capability_id"]),
            access=CapabilityAccess(str(capability["access"])),
            evidence=EvidenceRequirement(str(capability["evidence"])),
        ),
        arguments=dict(value.get("arguments") or {}),
        depends_on=tuple(str(item) for item in value.get("depends_on") or ()),
        risk=RiskLevel(str(value["risk"])),
        required_confirmation=bool(value.get("required_confirmation")),
        confirmation_status=ConfirmationStatus(str(value["confirmation_status"])),
        max_attempts=max(1, int(value.get("max_attempts") or 1)),
        continuation=(
            dict(value["continuation"]) if value.get("continuation") is not None else None
        ),
        action_id=str(value["action_id"]),
        status=StepStatus(str(value["status"])),
        attempts=max(0, int(value.get("attempts") or 0)),
        result=dict(value["result"]) if value.get("result") is not None else None,
        failure=_failure_from_dict(value.get("failure")),
        action_receipt=(
            dict(value["action_receipt"]) if value.get("action_receipt") is not None else None
        ),
        started_at=str(value["started_at"]) if value.get("started_at") else None,
        completed_at=str(value["completed_at"]) if value.get("completed_at") else None,
    )


def _plan_to_dict(plan: AgentPlan) -> JsonObject:
    raw = {
        "plan_id": plan.plan_id,
        "conversation_id": plan.conversation_id,
        "goal": plan.goal,
        "status": plan.status.value,
        "steps": [_step_to_dict(step) for step in plan.steps],
        "continuation": plan.continuation,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }
    safe = redact_secrets(raw)
    if not isinstance(safe, dict):  # pragma: no cover - defensive type boundary
        raise PlanValidationError("Plan serialization produced an invalid object")
    return safe


def _plan_from_dict(value: Mapping[str, Any]) -> AgentPlan:
    return AgentPlan(
        plan_id=str(value["plan_id"]),
        conversation_id=str(value["conversation_id"]),
        goal=str(value["goal"]),
        status=PlanStatus(str(value["status"])),
        steps=[_step_from_dict(item) for item in value.get("steps") or ()],
        continuation=(
            dict(value["continuation"]) if value.get("continuation") is not None else None
        ),
        created_at=str(value["created_at"]),
        updated_at=str(value["updated_at"]),
    )


class SQLitePlanStore:
    """Whole-plan JSON store with indexed status fields and WAL durability."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _database(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialise(self) -> None:
        with self._database() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_plans (
                    plan_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_plans_conversation
                    ON agent_plans(conversation_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_plans_status
                    ON agent_plans(status, updated_at DESC);
                """
            )

    def _save_sync(self, plan: AgentPlan) -> None:
        safe_plan = _plan_to_dict(plan)
        payload = json.dumps(safe_plan, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        with self._database() as connection:
            connection.execute(
                """
                INSERT INTO agent_plans(
                    plan_id, conversation_id, goal, status, payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(plan_id) DO UPDATE SET
                    conversation_id=excluded.conversation_id,
                    goal=excluded.goal,
                    status=excluded.status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    plan.plan_id,
                    plan.conversation_id,
                    str(safe_plan["goal"]),
                    plan.status.value,
                    payload,
                    plan.created_at,
                    plan.updated_at,
                ),
            )

    async def save(self, plan: AgentPlan) -> None:
        await asyncio.to_thread(self._save_sync, plan)

    def _get_sync(self, plan_id: str) -> AgentPlan | None:
        with self._database() as connection:
            row = connection.execute(
                "SELECT payload_json FROM agent_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            return None
        return _plan_from_dict(json.loads(str(row["payload_json"])))

    async def get(self, plan_id: str) -> AgentPlan | None:
        return await asyncio.to_thread(self._get_sync, plan_id)

    def _list_sync(
        self,
        conversation_id: str | None,
        status: PlanStatus | None,
        limit: int,
    ) -> list[AgentPlan]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            parameters.append(conversation_id)
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(max(1, min(int(limit), 500)))
        with self._database() as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM agent_plans{where} ORDER BY updated_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [_plan_from_dict(json.loads(str(row["payload_json"]))) for row in rows]

    async def list(
        self,
        *,
        conversation_id: str | None = None,
        status: PlanStatus | None = None,
        limit: int = 100,
    ) -> list[AgentPlan]:
        return await asyncio.to_thread(self._list_sync, conversation_id, status, limit)


_DYNAMIC_BLOCK_CODES = {
    "capability_missing",
    "capability_unavailable",
    "capability_unhealthy",
    "capability_snapshot_failed",
    "dependency_blocked",
}


class PersonalAgentPlanner:
    """Resumable DAG executor with fail-closed evidence and approval semantics."""

    def __init__(
        self,
        store: SQLitePlanStore,
        executor: CapabilityExecutor,
        *,
        max_parallel_reads: int = 4,
    ) -> None:
        self.store = store
        self.executor = executor
        self.max_parallel_reads = max(1, min(int(max_parallel_reads), 16))
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _plan_lock(self, plan_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(plan_id, asyncio.Lock())

    @staticmethod
    def _validate_graph(steps: Sequence[ProposedStep]) -> None:
        if not steps:
            raise PlanValidationError("A multi-step plan requires at least one step")
        identifiers: list[str] = []
        for proposed in steps:
            if not isinstance(proposed, ProposedStep):
                raise PlanValidationError("Every proposed item must be a ProposedStep")
            if not isinstance(proposed.capability, CapabilityRequirement):
                raise PlanValidationError("Every step requires a CapabilityRequirement")
            if not isinstance(proposed.capability.access, CapabilityAccess):
                raise PlanValidationError("Capability access must be a CapabilityAccess value")
            if not isinstance(proposed.capability.evidence, EvidenceRequirement):
                raise PlanValidationError(
                    "Capability evidence must be an EvidenceRequirement value"
                )
            if not isinstance(proposed.risk, RiskLevel):
                raise PlanValidationError("Step risk must be a RiskLevel value")
            if not isinstance(proposed.step_id, str):
                raise PlanValidationError("Every step requires a string step_id")
            step_id = proposed.step_id.strip()
            if not step_id:
                raise PlanValidationError("Every step requires a non-empty step_id")
            if step_id != proposed.step_id:
                raise PlanValidationError("Step IDs may not contain surrounding whitespace")
            identifiers.append(step_id)
            if not proposed.title.strip():
                raise PlanValidationError(f"Step {step_id!r} requires a title")
            if not proposed.capability.capability_id.strip():
                raise PlanValidationError(f"Step {step_id!r} requires a capability")
            if proposed.max_attempts < 1 or proposed.max_attempts > 10:
                raise PlanValidationError("max_attempts must be between 1 and 10")
            if proposed.capability.access is CapabilityAccess.READ and proposed.risk in {
                RiskLevel.HIGH,
                RiskLevel.CRITICAL,
            }:
                raise PlanValidationError("Read-only steps cannot declare write-level risk")
            if (
                proposed.capability.access is CapabilityAccess.WRITE
                and proposed.capability.evidence is not EvidenceRequirement.VERIFIED
            ):
                raise PlanValidationError("Write steps must require verified execution evidence")
        if len(set(identifiers)) != len(identifiers):
            raise PlanValidationError("Step IDs must be unique")

        known = set(identifiers)
        dependencies: dict[str, tuple[str, ...]] = {}
        for proposed in steps:
            if len(set(proposed.depends_on)) != len(proposed.depends_on):
                raise PlanValidationError(
                    f"Step {proposed.step_id!r} contains duplicate dependencies"
                )
            for dependency in proposed.depends_on:
                if dependency not in known:
                    raise PlanValidationError(
                        f"Step {proposed.step_id!r} depends on unknown step {dependency!r}"
                    )
                if dependency == proposed.step_id:
                    raise PlanValidationError(f"Step {proposed.step_id!r} cannot depend on itself")
            dependencies[proposed.step_id] = proposed.depends_on

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise PlanValidationError("The proposed step graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in identifiers:
            visit(step_id)

        def ancestors(step_id: str) -> set[str]:
            output: set[str] = set()
            pending = list(dependencies[step_id])
            while pending:
                dependency = pending.pop()
                if dependency in output:
                    continue
                output.add(dependency)
                pending.extend(dependencies[dependency])
            return output

        for proposed in steps:
            allowed = ancestors(proposed.step_id)
            for referenced in _argument_references(proposed.arguments):
                if referenced not in known:
                    raise PlanValidationError(
                        f"Step {proposed.step_id!r} references unknown step {referenced!r}"
                    )
                if referenced not in allowed:
                    raise PlanValidationError(
                        f"Step {proposed.step_id!r} may reference only an ancestor result"
                    )

    async def _snapshot(self) -> tuple[Mapping[str, CapabilityState], StepFailure | None]:
        try:
            snapshot = await self.executor.snapshot()
        except Exception as exc:
            return {}, StepFailure(
                code="capability_snapshot_failed",
                message="Capability availability could not be checked.",
                retryable=True,
                details={"error_type": type(exc).__name__},
            )
        if not isinstance(snapshot, Mapping):
            return {}, StepFailure(
                code="capability_snapshot_failed",
                message="The capability provider returned an invalid snapshot.",
                retryable=True,
            )
        for capability_id, state in snapshot.items():
            valid_booleans = isinstance(state, CapabilityState) and all(
                type(value) is bool
                for value in (
                    state.available,
                    state.healthy,
                    state.readable,
                    state.writable,
                    state.requires_confirmation,
                    state.supports_verification,
                )
            )
            if (
                not isinstance(capability_id, str)
                or not valid_booleans
                or state.capability_id != capability_id
            ):
                return {}, StepFailure(
                    code="capability_snapshot_failed",
                    message="The capability provider returned malformed capability metadata.",
                    retryable=True,
                )
        return snapshot, None

    @staticmethod
    def _capability_failure(step: PlanStep, state: CapabilityState | None) -> StepFailure | None:
        capability_id = step.capability.capability_id
        if state is None or state.capability_id != capability_id:
            return StepFailure(
                code="capability_missing",
                message=f"Capability {capability_id!r} is not configured.",
                retryable=True,
            )
        if not state.available:
            return StepFailure(
                code="capability_unavailable",
                message=state.reason or f"Capability {capability_id!r} is unavailable.",
                retryable=True,
            )
        if not state.healthy:
            return StepFailure(
                code="capability_unhealthy",
                message=state.reason or f"Capability {capability_id!r} is unhealthy.",
                retryable=True,
            )
        if not state.permits(step.capability.access):
            return StepFailure(
                code="capability_access_denied",
                message=(
                    f"Capability {capability_id!r} does not permit "
                    f"{step.capability.access.value} operations."
                ),
                retryable=False,
            )
        if (
            step.capability.evidence is EvidenceRequirement.VERIFIED
            and not state.supports_verification
        ):
            return StepFailure(
                code="verification_unsupported",
                message=f"Capability {capability_id!r} cannot verify this operation.",
                retryable=False,
            )
        return None

    @staticmethod
    def _requires_confirmation(proposed: ProposedStep, state: CapabilityState | None) -> bool:
        if proposed.capability.access is CapabilityAccess.READ:
            return False
        return bool(
            proposed.requires_confirmation
            or proposed.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            or (state is not None and state.requires_confirmation)
        )

    async def create(
        self,
        *,
        route: RequestRoute,
        conversation_id: str,
        goal: str,
        proposed_steps: Sequence[ProposedStep],
        continuation: Mapping[str, Any] | None = None,
        plan_id: str | None = None,
    ) -> AgentPlan:
        if RequestRoute(route) is RequestRoute.SIMPLE_DIRECT:
            raise DirectExecutionRequired(
                "simple_direct requests must use the deterministic direct executor"
            )
        if not conversation_id.strip():
            raise PlanValidationError("conversation_id is required")
        if not goal.strip():
            raise PlanValidationError("goal is required")
        _reject_secret_material(conversation_id, name="conversation_id")
        _reject_secret_material(goal, name="goal")
        self._validate_graph(proposed_steps)
        plan_continuation = _json_object(continuation, name="plan continuation")
        _reject_secret_material(plan_continuation, name="plan continuation")
        snapshot, snapshot_failure = await self._snapshot()
        now = _utc_now()
        steps: list[PlanStep] = []
        for proposed in proposed_steps:
            arguments = _json_object(proposed.arguments, name=f"{proposed.step_id} arguments")
            step_continuation = _json_object(
                proposed.continuation, name=f"{proposed.step_id} continuation"
            )
            _reject_secret_material(proposed.title, name=f"{proposed.step_id} title")
            _reject_secret_material(arguments, name=f"{proposed.step_id} arguments")
            _reject_secret_material(step_continuation, name=f"{proposed.step_id} continuation")
            state = snapshot.get(proposed.capability.capability_id)
            required_confirmation = self._requires_confirmation(proposed, state)
            step = PlanStep(
                step_id=proposed.step_id,
                title=proposed.title.strip(),
                capability=proposed.capability,
                arguments=arguments or {},
                depends_on=tuple(proposed.depends_on),
                risk=proposed.risk,
                required_confirmation=required_confirmation,
                confirmation_status=(
                    ConfirmationStatus.PENDING
                    if required_confirmation
                    else ConfirmationStatus.NOT_REQUIRED
                ),
                max_attempts=proposed.max_attempts,
                continuation=step_continuation,
                action_id=str(uuid.uuid4()),
            )
            failure = snapshot_failure or self._capability_failure(step, state)
            if failure is not None:
                step.status = StepStatus.BLOCKED
                step.failure = failure
            steps.append(step)

        resolved_plan_id = str(plan_id or uuid.uuid4()).strip()
        if not resolved_plan_id:
            raise PlanValidationError("plan_id may not be empty")
        _reject_secret_material(resolved_plan_id, name="plan_id")
        if await self.store.get(resolved_plan_id) is not None:
            raise PlanValidationError(f"Plan {resolved_plan_id!r} already exists")
        plan = AgentPlan(
            plan_id=resolved_plan_id,
            conversation_id=conversation_id.strip(),
            goal=goal.strip(),
            status=PlanStatus.PENDING,
            steps=steps,
            continuation=plan_continuation,
            created_at=now,
            updated_at=now,
        )
        self._propagate_dependency_blocks(plan)
        self._derive_status(plan)
        await self.store.save(plan)
        return plan

    create_plan = create

    @staticmethod
    def _proposal_matches_persisted_step(proposed: ProposedStep, persisted: PlanStep) -> bool:
        try:
            arguments = _json_object(proposed.arguments, name=f"{proposed.step_id} arguments") or {}
            continuation = _json_object(
                proposed.continuation,
                name=f"{proposed.step_id} continuation",
            )
        except PlanValidationError:
            return False
        return bool(
            proposed.step_id == persisted.step_id
            and proposed.title.strip() == persisted.title
            and proposed.capability == persisted.capability
            and arguments == persisted.arguments
            and tuple(proposed.depends_on) == persisted.depends_on
            and proposed.risk is persisted.risk
            and proposed.max_attempts == persisted.max_attempts
            and continuation == persisted.continuation
        )

    async def replan(
        self,
        plan_id: str,
        *,
        proposed_steps: Sequence[ProposedStep],
        goal: str | None = None,
        continuation: Mapping[str, Any] | None = None,
    ) -> AgentPlan:
        """Replace only unstarted work while preserving proven completed steps.

        Failed, cancelled, running, or outcome-unknown steps require explicit
        human resolution and cannot be rewritten out of history. Successful
        steps must be present unchanged in the replacement graph.
        """

        lock = await self._plan_lock(plan_id)
        async with lock:
            plan = await self.store.get(plan_id)
            if plan is None:
                raise KeyError(plan_id)
            if plan.status in {PlanStatus.CANCELLED, PlanStatus.COMPLETED}:
                raise PlanValidationError("A terminal plan cannot be replanned")
            unsafe = [
                step.step_id
                for step in plan.steps
                if step.status
                in {
                    StepStatus.RUNNING,
                    StepStatus.FAILED,
                    StepStatus.OUTCOME_UNKNOWN,
                    StepStatus.CANCELLED,
                }
            ]
            if unsafe:
                raise PlanValidationError(
                    "A plan with failed, cancelled, running, or outcome-unknown "
                    "steps cannot be automatically replanned"
                )
            self._validate_graph(proposed_steps)
            proposed_by_id = {step.step_id: step for step in proposed_steps}
            completed = {
                step.step_id: step for step in plan.steps if step.status is StepStatus.SUCCEEDED
            }
            for step_id, persisted in completed.items():
                proposed = proposed_by_id.get(step_id)
                if proposed is None or not self._proposal_matches_persisted_step(
                    proposed, persisted
                ):
                    raise PlanValidationError(
                        f"Completed step {step_id!r} must remain unchanged during replanning"
                    )
            if proposed_steps and all(step.step_id in completed for step in proposed_steps):
                raise PlanValidationError(
                    "Replanning cannot declare completion by deleting all remaining work"
                )

            resolved_goal = plan.goal if goal is None else str(goal).strip()
            if not resolved_goal:
                raise PlanValidationError("goal is required")
            _reject_secret_material(resolved_goal, name="goal")
            resolved_continuation = (
                plan.continuation
                if continuation is None
                else _json_object(continuation, name="plan continuation")
            )
            _reject_secret_material(resolved_continuation, name="plan continuation")
            snapshot, snapshot_failure = await self._snapshot()
            replacement: list[PlanStep] = []
            for proposed in proposed_steps:
                completed_step = completed.get(proposed.step_id)
                if completed_step is not None:
                    replacement.append(completed_step)
                    continue
                arguments = (
                    _json_object(proposed.arguments, name=f"{proposed.step_id} arguments") or {}
                )
                step_continuation = _json_object(
                    proposed.continuation,
                    name=f"{proposed.step_id} continuation",
                )
                _reject_secret_material(proposed.title, name=f"{proposed.step_id} title")
                _reject_secret_material(arguments, name=f"{proposed.step_id} arguments")
                _reject_secret_material(
                    step_continuation,
                    name=f"{proposed.step_id} continuation",
                )
                state = snapshot.get(proposed.capability.capability_id)
                confirmation = self._requires_confirmation(proposed, state)
                step = PlanStep(
                    step_id=proposed.step_id,
                    title=proposed.title.strip(),
                    capability=proposed.capability,
                    arguments=arguments,
                    depends_on=tuple(proposed.depends_on),
                    risk=proposed.risk,
                    required_confirmation=confirmation,
                    confirmation_status=(
                        ConfirmationStatus.PENDING
                        if confirmation
                        else ConfirmationStatus.NOT_REQUIRED
                    ),
                    max_attempts=proposed.max_attempts,
                    continuation=step_continuation,
                    action_id=str(uuid.uuid4()),
                )
                failure = snapshot_failure or self._capability_failure(step, state)
                if failure is not None:
                    step.status = StepStatus.BLOCKED
                    step.failure = failure
                replacement.append(step)

            plan.goal = resolved_goal
            plan.steps = replacement
            plan.continuation = resolved_continuation
            self._propagate_dependency_blocks(plan)
            plan.updated_at = _utc_now()
            self._derive_status(plan)
            await self.store.save(plan)
            return plan

    async def get(self, plan_id: str) -> AgentPlan | None:
        return await self.store.get(plan_id)

    async def list_plans(
        self,
        *,
        conversation_id: str | None = None,
        status: PlanStatus | None = None,
        limit: int = 100,
    ) -> list[AgentPlan]:
        return await self.store.list(conversation_id=conversation_id, status=status, limit=limit)

    @staticmethod
    def _clear_dynamic_blocks(plan: AgentPlan) -> None:
        for step in plan.steps:
            if (
                step.status is StepStatus.BLOCKED
                and step.failure is not None
                and step.failure.code in _DYNAMIC_BLOCK_CODES
            ):
                step.status = StepStatus.PENDING
                step.failure = None

    async def _recover_interrupted_steps(self, plan: AgentPlan) -> bool:
        changed = False
        for step in plan.steps:
            if step.status is not StepStatus.RUNNING:
                continue
            if step.capability.access is CapabilityAccess.WRITE:
                reconciler = getattr(self.executor, "reconcile", None)
                if callable(reconciler):
                    request = CapabilityExecutionRequest(
                        plan_id=plan.plan_id,
                        step_id=step.step_id,
                        action_id=step.action_id,
                        conversation_id=plan.conversation_id,
                        capability_id=step.capability.capability_id,
                        access=step.capability.access,
                        arguments=step.arguments,
                    )
                    try:
                        recovered = await reconciler(request)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        recovered = None
                    if (
                        isinstance(recovered, CapabilityExecutionResult)
                        and isinstance(recovered.status, ExecutionStatus)
                        and type(recovered.accepted) is bool
                        and type(recovered.verified) is bool
                        and type(recovered.retryable) is bool
                    ):
                        self._apply_execution_result(step, recovered)
                        changed = True
                        continue
                step.status = StepStatus.OUTCOME_UNKNOWN
                step.failure = StepFailure(
                    code="execution_interrupted_outcome_unknown",
                    message=(
                        "Execution was interrupted after this external write started; "
                        "its outcome must be verified before any retry."
                    ),
                    retryable=False,
                )
            else:
                step.status = StepStatus.FAILED
                step.failure = StepFailure(
                    code="execution_interrupted",
                    message="The read was interrupted and can be attempted again safely.",
                    retryable=step.attempts < step.max_attempts,
                )
            step.completed_at = _utc_now()
            changed = True
        return changed

    @staticmethod
    def _reset_retryable_failures(plan: AgentPlan) -> None:
        for step in plan.steps:
            if (
                step.status is StepStatus.FAILED
                and step.failure is not None
                and step.failure.retryable
                and step.attempts < step.max_attempts
            ):
                step.status = StepStatus.PENDING
                step.failure = None

    @staticmethod
    def _propagate_dependency_blocks(plan: AgentPlan) -> None:
        by_id = {step.step_id: step for step in plan.steps}
        changed = True
        while changed:
            changed = False
            for step in plan.steps:
                if step.status not in {StepStatus.PENDING, StepStatus.BLOCKED}:
                    continue
                if (
                    step.status is StepStatus.BLOCKED
                    and step.failure is not None
                    and step.failure.code != "dependency_blocked"
                ):
                    continue
                blocked_dependencies = [
                    dependency
                    for dependency in step.depends_on
                    if by_id[dependency].status
                    in {
                        StepStatus.FAILED,
                        StepStatus.BLOCKED,
                        StepStatus.OUTCOME_UNKNOWN,
                        StepStatus.CANCELLED,
                    }
                ]
                if blocked_dependencies:
                    dependency_failures = [by_id[item].failure for item in blocked_dependencies]
                    retryable = all(
                        failure is not None and failure.retryable for failure in dependency_failures
                    )
                    failure = StepFailure(
                        code="dependency_blocked",
                        message="A required earlier step has not completed successfully.",
                        retryable=retryable,
                        details={"dependencies": blocked_dependencies},
                    )
                    if step.status is not StepStatus.BLOCKED or step.failure != failure:
                        step.status = StepStatus.BLOCKED
                        step.failure = failure
                        changed = True

    @staticmethod
    def _derive_status(plan: AgentPlan) -> None:
        statuses = [step.status for step in plan.steps]
        if plan.status is PlanStatus.CANCELLED:
            return
        if statuses and all(status is StepStatus.SUCCEEDED for status in statuses):
            plan.status = PlanStatus.COMPLETED
        elif any(status is StepStatus.RUNNING for status in statuses):
            plan.status = PlanStatus.RUNNING
        elif any(status is StepStatus.AWAITING_APPROVAL for status in statuses):
            plan.status = PlanStatus.AWAITING_APPROVAL
        elif any(status is StepStatus.PENDING for status in statuses):
            plan.status = PlanStatus.PENDING
        elif any(status in {StepStatus.BLOCKED, StepStatus.OUTCOME_UNKNOWN} for status in statuses):
            plan.status = (
                PlanStatus.PARTIAL
                if any(status is StepStatus.SUCCEEDED for status in statuses)
                else PlanStatus.BLOCKED
            )
        elif any(status is StepStatus.FAILED for status in statuses):
            plan.status = (
                PlanStatus.PARTIAL
                if any(status is StepStatus.SUCCEEDED for status in statuses)
                else PlanStatus.FAILED
            )
        elif any(status is StepStatus.CANCELLED for status in statuses):
            plan.status = PlanStatus.PARTIAL
        else:
            plan.status = PlanStatus.BLOCKED

    @staticmethod
    def _ready_steps(plan: AgentPlan) -> list[PlanStep]:
        by_id = {step.step_id: step for step in plan.steps}
        return [
            step
            for step in plan.steps
            if step.status is StepStatus.PENDING
            and all(
                by_id[dependency].status is StepStatus.SUCCEEDED for dependency in step.depends_on
            )
        ]

    async def _revalidate_nonterminal(
        self, plan: AgentPlan, snapshot: Mapping[str, CapabilityState], failure: StepFailure | None
    ) -> None:
        for step in plan.steps:
            if step.status in {
                StepStatus.SUCCEEDED,
                StepStatus.FAILED,
                StepStatus.OUTCOME_UNKNOWN,
                StepStatus.CANCELLED,
            }:
                continue
            state = snapshot.get(step.capability.capability_id)
            capability_failure = failure or self._capability_failure(step, state)
            if capability_failure is not None:
                step.status = StepStatus.BLOCKED
                step.failure = capability_failure
                continue
            if (
                step.capability.access is CapabilityAccess.WRITE
                and state is not None
                and state.requires_confirmation
            ):
                step.required_confirmation = True
                if step.confirmation_status is ConfirmationStatus.NOT_REQUIRED:
                    step.confirmation_status = ConfirmationStatus.PENDING

    @staticmethod
    def _gate_approvals(steps: Sequence[PlanStep]) -> list[PlanStep]:
        executable: list[PlanStep] = []
        for step in steps:
            if not step.required_confirmation:
                executable.append(step)
                continue
            if step.confirmation_status is ConfirmationStatus.APPROVED:
                executable.append(step)
                continue
            step.status = StepStatus.AWAITING_APPROVAL
            step.confirmation_status = ConfirmationStatus.PENDING
            step.failure = None
        return executable

    async def _preflight(self, step: PlanStep) -> tuple[CapabilityState | None, StepFailure | None]:
        snapshot, snapshot_failure = await self._snapshot()
        if snapshot_failure is not None:
            return None, snapshot_failure
        state = snapshot.get(step.capability.capability_id)
        return state, self._capability_failure(step, state)

    async def _preflight_batch(self, steps: Sequence[PlanStep]) -> list[PlanStep]:
        outcomes = await asyncio.gather(*(self._preflight(step) for step in steps))
        executable: list[PlanStep] = []
        for step, (state, failure) in zip(steps, outcomes, strict=True):
            if failure is not None:
                step.status = StepStatus.BLOCKED
                step.failure = failure
                continue
            if (
                step.capability.access is CapabilityAccess.WRITE
                and state is not None
                and state.requires_confirmation
                and step.confirmation_status is not ConfirmationStatus.APPROVED
            ):
                step.required_confirmation = True
                step.confirmation_status = ConfirmationStatus.PENDING
                step.status = StepStatus.AWAITING_APPROVAL
                continue
            executable.append(step)
        return executable

    async def _call_executor(self, plan: AgentPlan, step: PlanStep) -> CapabilityExecutionResult:
        try:
            arguments = self._resolve_step_arguments(plan, step)
        except PlanValidationError as exc:
            return CapabilityExecutionResult(
                status=ExecutionStatus.FAILED,
                error_code="argument_resolution_failed",
                error=str(exc),
                retryable=False,
            )
        request = CapabilityExecutionRequest(
            plan_id=plan.plan_id,
            step_id=step.step_id,
            action_id=step.action_id,
            conversation_id=plan.conversation_id,
            capability_id=step.capability.capability_id,
            access=step.capability.access,
            arguments=arguments,
        )
        try:
            result = await self.executor.execute(request)
        except Exception as exc:
            if step.capability.access is CapabilityAccess.WRITE:
                return CapabilityExecutionResult(
                    status=ExecutionStatus.OUTCOME_UNKNOWN,
                    error_code="executor_exception_outcome_unknown",
                    error=(
                        "The provider call ended without a reliable write outcome; "
                        "verification is required before retrying."
                    ),
                    retryable=False,
                    result={"error_type": type(exc).__name__},
                )
            return CapabilityExecutionResult(
                status=ExecutionStatus.FAILED,
                error_code="executor_exception",
                error="The provider call failed before returning usable evidence.",
                retryable=True,
                result={"error_type": type(exc).__name__},
            )
        if (
            not isinstance(result, CapabilityExecutionResult)
            or not isinstance(result.status, ExecutionStatus)
            or type(result.accepted) is not bool
            or type(result.verified) is not bool
            or type(result.retryable) is not bool
        ):
            return CapabilityExecutionResult(
                status=(
                    ExecutionStatus.OUTCOME_UNKNOWN
                    if step.capability.access is CapabilityAccess.WRITE
                    else ExecutionStatus.FAILED
                ),
                error_code="invalid_executor_result",
                error="The provider returned an invalid structured result.",
                retryable=False,
            )
        return result

    @staticmethod
    def _resolve_step_arguments(plan: AgentPlan, step: PlanStep) -> JsonObject:
        """Resolve persisted ancestor evidence through a bounded JSON path."""

        by_id = {item.step_id: item for item in plan.steps}

        def resolve(item: Any, depth: int) -> Any:
            if depth > 20:
                raise PlanValidationError("Step argument references are nested too deeply")
            if isinstance(item, Mapping):
                if "$from_step" in item:
                    source_id = str(item.get("$from_step") or "")
                    source = by_id.get(source_id)
                    if (
                        source is None
                        or source.status is not StepStatus.SUCCEEDED
                        or source.result is None
                    ):
                        raise PlanValidationError(
                            f"Referenced step {source_id!r} has no successful result"
                        )
                    value: Any = source.result
                    for part in str(item.get("path") or "").split("."):
                        if isinstance(value, Mapping) and part in value:
                            value = value[part]
                        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                            value = value[int(part)]
                        else:
                            raise PlanValidationError(
                                f"Result path {item.get('path')!r} was not present "
                                f"in step {source_id!r}"
                            )
                    return resolve(value, depth + 1)
                return {key: resolve(value, depth + 1) for key, value in item.items()}
            if isinstance(item, (list, tuple)):
                return [resolve(value, depth + 1) for value in item]
            return item

        resolved = resolve(step.arguments, 0)
        if not isinstance(resolved, dict):  # pragma: no cover - stored invariant
            raise PlanValidationError("Resolved step arguments must be an object")
        detached = _json_object(resolved, name="resolved step arguments") or {}
        _reject_secret_material(detached, name="resolved step arguments")
        return detached

    @staticmethod
    def _apply_execution_result(step: PlanStep, execution: CapabilityExecutionResult) -> None:
        try:
            result = _json_object(redact_secrets(execution.result), name="execution result") or {}
            receipt = _json_object(redact_secrets(execution.action_receipt), name="action receipt")
            continuation = _json_object(
                redact_secrets(execution.continuation),
                name="execution continuation",
            )
        except PlanValidationError as exc:
            result = {}
            receipt = None
            continuation = None
            execution = CapabilityExecutionResult(
                status=(
                    ExecutionStatus.OUTCOME_UNKNOWN
                    if step.capability.access is CapabilityAccess.WRITE
                    else ExecutionStatus.FAILED
                ),
                error_code="invalid_execution_evidence",
                error=str(exc),
                retryable=False,
            )

        step.result = result
        step.action_receipt = receipt
        if continuation is not None:
            step.continuation = continuation
        step.completed_at = _utc_now()

        if execution.status is ExecutionStatus.OUTCOME_UNKNOWN:
            step.status = StepStatus.OUTCOME_UNKNOWN
            step.failure = StepFailure(
                code=redact_text(execution.error_code or "outcome_unknown"),
                message=redact_text(
                    execution.error or "The provider could not determine the action outcome."
                ),
                retryable=False,
            )
            return
        if execution.status is ExecutionStatus.FAILED:
            can_retry = bool(execution.retryable and step.attempts < step.max_attempts)
            step.status = StepStatus.FAILED
            step.failure = StepFailure(
                code=redact_text(execution.error_code or "execution_failed"),
                message=redact_text(execution.error or "The capability execution failed."),
                retryable=can_retry,
            )
            return

        has_evidence = execution.verified or (
            step.capability.evidence is EvidenceRequirement.ACCEPTED and execution.accepted
        )
        if not has_evidence:
            if step.capability.access is CapabilityAccess.WRITE:
                step.status = StepStatus.OUTCOME_UNKNOWN
                step.failure = StepFailure(
                    code="required_evidence_missing",
                    message=(
                        "The provider reported success without the required execution "
                        "evidence; the write outcome is unknown."
                    ),
                    retryable=False,
                )
            else:
                step.status = StepStatus.FAILED
                step.failure = StepFailure(
                    code="required_evidence_missing",
                    message="The provider reported success without the required evidence.",
                    retryable=False,
                )
            return
        if step.capability.access is CapabilityAccess.WRITE and not receipt:
            step.status = StepStatus.OUTCOME_UNKNOWN
            step.failure = StepFailure(
                code="action_receipt_missing",
                message=(
                    "The provider reported a write without an action receipt; "
                    "completion cannot be claimed."
                ),
                retryable=False,
            )
            return
        if (
            step.capability.access is CapabilityAccess.WRITE
            and str((receipt or {}).get("status") or "").casefold() != "verified"
        ):
            step.status = StepStatus.OUTCOME_UNKNOWN
            step.failure = StepFailure(
                code="action_receipt_not_verified",
                message=(
                    "The write receipt does not record verified execution; "
                    "completion cannot be claimed."
                ),
                retryable=False,
            )
            return
        step.status = StepStatus.SUCCEEDED
        step.failure = None

    async def _execute_batch(self, plan: AgentPlan, candidates: Sequence[PlanStep]) -> None:
        executable = await self._preflight_batch(candidates)
        if not executable:
            return
        now = _utc_now()
        for step in executable:
            step.status = StepStatus.RUNNING
            step.attempts += 1
            step.started_at = now
            step.completed_at = None
            step.failure = None
        plan.status = PlanStatus.RUNNING
        plan.updated_at = now
        # Commit RUNNING and the stable action IDs before an external side effect.
        await self.store.save(plan)

        semaphore = asyncio.Semaphore(self.max_parallel_reads)

        async def execute_one(step: PlanStep) -> CapabilityExecutionResult:
            async with semaphore:
                return await self._call_executor(plan, step)

        results = await asyncio.gather(*(execute_one(step) for step in executable))
        for step, result in zip(executable, results, strict=True):
            self._apply_execution_result(step, result)

    async def resume(self, plan_id: str, *, retry_failed: bool = True) -> AgentPlan:
        lock = await self._plan_lock(plan_id)
        async with lock:
            plan = await self.store.get(plan_id)
            if plan is None:
                raise KeyError(plan_id)
            if plan.status in {PlanStatus.CANCELLED, PlanStatus.COMPLETED}:
                return plan

            recovered = await self._recover_interrupted_steps(plan)
            if recovered:
                plan.updated_at = _utc_now()
                self._derive_status(plan)
                # Persist receipt reconciliation before any new snapshot or call.
                await self.store.save(plan)
            self._clear_dynamic_blocks(plan)
            if retry_failed:
                self._reset_retryable_failures(plan)
            snapshot, snapshot_failure = await self._snapshot()
            await self._revalidate_nonterminal(plan, snapshot, snapshot_failure)
            self._propagate_dependency_blocks(plan)

            while True:
                ready = self._ready_steps(plan)
                if not ready:
                    break
                executable = self._gate_approvals(ready)
                if not executable:
                    break

                reads = [
                    step for step in executable if step.capability.access is CapabilityAccess.READ
                ]
                if reads:
                    await self._execute_batch(plan, reads)
                    self._propagate_dependency_blocks(plan)
                    plan.updated_at = _utc_now()
                    self._derive_status(plan)
                    await self.store.save(plan)
                    continue

                # Writes are deliberately serialized, even when independent.
                await self._execute_batch(plan, executable[:1])
                self._propagate_dependency_blocks(plan)
                plan.updated_at = _utc_now()
                self._derive_status(plan)
                await self.store.save(plan)

            self._propagate_dependency_blocks(plan)
            plan.updated_at = _utc_now()
            self._derive_status(plan)
            await self.store.save(plan)
            return plan

    execute = resume

    async def approve(self, plan_id: str, step_id: str, *, approved: bool = True) -> AgentPlan:
        lock = await self._plan_lock(plan_id)
        async with lock:
            plan = await self.store.get(plan_id)
            if plan is None:
                raise KeyError(plan_id)
            if plan.status in {PlanStatus.CANCELLED, PlanStatus.COMPLETED}:
                raise PlanValidationError("A terminal plan cannot be approved")
            step = plan.step(step_id)
            if not step.required_confirmation:
                raise PlanValidationError(f"Step {step_id!r} does not require approval")
            if step.status in {
                StepStatus.SUCCEEDED,
                StepStatus.FAILED,
                StepStatus.OUTCOME_UNKNOWN,
                StepStatus.CANCELLED,
            }:
                raise PlanValidationError("A terminal step cannot be approved")
            if approved:
                step.confirmation_status = ConfirmationStatus.APPROVED
                if step.status is StepStatus.AWAITING_APPROVAL:
                    step.status = StepStatus.PENDING
                step.failure = None
            else:
                step.confirmation_status = ConfirmationStatus.DENIED
                step.status = StepStatus.CANCELLED
                step.failure = StepFailure(
                    code="approval_denied",
                    message="The user declined this external action.",
                    retryable=False,
                )
            self._clear_dynamic_blocks(plan)
            self._propagate_dependency_blocks(plan)
            plan.updated_at = _utc_now()
            self._derive_status(plan)
            await self.store.save(plan)
            return plan

    async def cancel(self, plan_id: str) -> AgentPlan:
        lock = await self._plan_lock(plan_id)
        async with lock:
            plan = await self.store.get(plan_id)
            if plan is None:
                raise KeyError(plan_id)
            if plan.status is PlanStatus.COMPLETED:
                raise PlanValidationError("A completed plan cannot be cancelled")
            for step in plan.steps:
                if step.status is not StepStatus.SUCCEEDED:
                    step.status = StepStatus.CANCELLED
                    step.failure = StepFailure(
                        code="plan_cancelled",
                        message="The plan was cancelled by the user.",
                        retryable=False,
                    )
                    step.completed_at = _utc_now()
            plan.status = PlanStatus.CANCELLED
            plan.updated_at = _utc_now()
            await self.store.save(plan)
            return plan

    # Compatibility alias for callers predating the unambiguous method name.  It is
    # intentionally declared last so it cannot shadow ``list[...]`` annotations in
    # the class body.
    list = list_plans
