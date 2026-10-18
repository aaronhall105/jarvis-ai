"""Durable lifecycle coordination for proactive speech delivery.

The proactive intelligence engine decides whether an event is worth surfacing.
This module owns only the delivery lifecycle after speech has been admitted.  It
does not classify household events or interpret user language.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.runtime_observability import runtime_metrics


class ProactiveSpeechState(StrEnum):
    QUEUED = "QUEUED"
    WAITING_FOR_GAP = "WAITING_FOR_GAP"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    RESUME_PENDING = "RESUME_PENDING"
    REPLAY_PENDING = "REPLAY_PENDING"
    COMPLETED = "COMPLETED"
    DROPPED = "DROPPED"
    CANCELLED = "CANCELLED"


TERMINAL_STATES = {
    ProactiveSpeechState.COMPLETED,
    ProactiveSpeechState.DROPPED,
    ProactiveSpeechState.CANCELLED,
}
ACTIVE_STATES = set(ProactiveSpeechState) - TERMINAL_STATES

_TRANSITIONS: dict[ProactiveSpeechState, set[ProactiveSpeechState]] = {
    ProactiveSpeechState.QUEUED: {
        ProactiveSpeechState.WAITING_FOR_GAP,
        ProactiveSpeechState.CANCELLED,
        ProactiveSpeechState.DROPPED,
    },
    ProactiveSpeechState.WAITING_FOR_GAP: {
        ProactiveSpeechState.SPEAKING,
        ProactiveSpeechState.CANCELLED,
        ProactiveSpeechState.DROPPED,
    },
    ProactiveSpeechState.SPEAKING: {
        ProactiveSpeechState.COMPLETED,
        ProactiveSpeechState.INTERRUPTED,
        ProactiveSpeechState.DROPPED,
        ProactiveSpeechState.CANCELLED,
    },
    ProactiveSpeechState.INTERRUPTED: {
        ProactiveSpeechState.RESUME_PENDING,
        ProactiveSpeechState.REPLAY_PENDING,
        ProactiveSpeechState.DROPPED,
        ProactiveSpeechState.CANCELLED,
    },
    ProactiveSpeechState.RESUME_PENDING: {
        ProactiveSpeechState.SPEAKING,
        ProactiveSpeechState.CANCELLED,
        ProactiveSpeechState.DROPPED,
    },
    ProactiveSpeechState.REPLAY_PENDING: {
        ProactiveSpeechState.SPEAKING,
        ProactiveSpeechState.CANCELLED,
        ProactiveSpeechState.DROPPED,
    },
    ProactiveSpeechState.COMPLETED: set(),
    ProactiveSpeechState.DROPPED: set(),
    ProactiveSpeechState.CANCELLED: set(),
}


@dataclass(frozen=True, slots=True)
class EndpointActivity:
    available: bool = True
    user_speaking: bool = False
    assistant_speaking: bool = False
    turn_in_progress: bool = False

    @property
    def busy(self) -> bool:
        return self.user_speaking or self.assistant_speaking or self.turn_in_progress


@dataclass(frozen=True, slots=True)
class SpeechStartResult:
    accepted: bool
    session_id: str = ""
    completed: bool = False
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SpeechQueueRequest:
    event_id: str
    event_type: str
    message: str
    importance: int
    endpoint: str
    freshness_deadline: int
    dedupe_key: str
    supersession_key: str
    provenance: dict[str, Any]
    trigger_evidence: dict[str, Any]
    target_user: str
    resume_policy: str = "IMPORTANCE_AND_FRESHNESS"
    interruption_policy: str = "STOP_AND_DECIDE"


EndpointStatus = Callable[[str], Awaitable[EndpointActivity]]
SpeechStarter = Callable[[dict[str, Any], bool], Awaitable[SpeechStartResult]]
SpeechStopper = Callable[[dict[str, Any]], Awaitable[bool]]
FreshnessCheck = Callable[[dict[str, Any]], Awaitable[bool]]


class ProactiveSpeechCoordinator:
    """One durable, deterministic lifecycle for admitted proactive speech."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        endpoint_status: EndpointStatus,
        start_speech: SpeechStarter,
        stop_speech: SpeechStopper,
        revalidate: FreshnessCheck,
        now_fn: Callable[[], int] | None = None,
        resume_window_seconds: int = 45,
        completion_grace_seconds: int = 2,
    ) -> None:
        self.database_path = Path(database_path)
        self.endpoint_status = endpoint_status
        self.start_speech = start_speech
        self.stop_speech = stop_speech
        self.revalidate = revalidate
        self.now_fn = now_fn or (lambda: int(time.time()))
        self.resume_window_seconds = max(5, min(int(resume_window_seconds), 300))
        self.completion_grace_seconds = max(1, min(int(completion_grace_seconds), 30))
        self._lock = asyncio.Lock()
        self._interaction_owners: set[str] = set()
        self._initialised = False

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def initialise(self) -> None:
        if self._initialised:
            return
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS proactive_speech_lifecycle (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    target_user TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    state_reason TEXT NOT NULL,
                    freshness_deadline INTEGER NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    supersession_key TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    trigger_evidence_json TEXT NOT NULL,
                    interruption_policy TEXT NOT NULL,
                    resume_policy TEXT NOT NULL,
                    generation INTEGER NOT NULL DEFAULT 1,
                    speech_session_id TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_transition_at INTEGER NOT NULL,
                    speech_started_at INTEGER,
                    completed_at INTEGER,
                    interrupted_at INTEGER,
                    resume_until INTEGER,
                    delivered_fraction REAL NOT NULL DEFAULT 0,
                    replay_count INTEGER NOT NULL DEFAULT 0,
                    retry_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_proactive_speech_active
                ON proactive_speech_lifecycle(lifecycle_state, importance DESC, created_at);
                CREATE INDEX IF NOT EXISTS idx_proactive_speech_dedupe
                ON proactive_speech_lifecycle(dedupe_key, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_proactive_speech_supersession
                ON proactive_speech_lifecycle(supersession_key, created_at DESC);
                CREATE TABLE IF NOT EXISTS proactive_speech_transitions (
                    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_proactive_speech_transition_event
                ON proactive_speech_transitions(event_id, transition_id);
                """
            )
            columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(proactive_speech_lifecycle)"
                ).fetchall()
            }
            if "target_user" not in columns:
                connection.execute(
                    "ALTER TABLE proactive_speech_lifecycle ADD COLUMN target_user "
                    "TEXT NOT NULL DEFAULT 'all'"
                )
        self._reconcile_restart()
        self._initialised = True

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        for source, target in (
            ("provenance_json", "provenance"),
            ("trigger_evidence_json", "trigger_evidence"),
        ):
            try:
                item[target] = json.loads(str(item.pop(source)))
            except (json.JSONDecodeError, TypeError):
                item[target] = {}
        return item

    def _audit_transition(
        self,
        connection: sqlite3.Connection,
        event_id: str,
        old: ProactiveSpeechState | None,
        new: ProactiveSpeechState,
        reason: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO proactive_speech_transitions "
            "(event_id,from_state,to_state,reason,metadata_json,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                event_id,
                old.value if old else None,
                new.value,
                reason,
                json.dumps(metadata or {}, separators=(",", ":"), default=str),
                self.now_fn(),
            ),
        )
        runtime_metrics.increment(f"proactive_speech_{new.value.casefold()}")

    def _transition(
        self,
        event_id: str,
        new: ProactiveSpeechState,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialise()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_speech_lifecycle WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None:
                raise KeyError(event_id)
            old = ProactiveSpeechState(str(row["lifecycle_state"]))
            if new not in _TRANSITIONS[old]:
                raise ValueError(f"Invalid proactive speech transition {old.value}->{new.value}")
            transition_now = self.now_fn()
            if new is ProactiveSpeechState.SPEAKING:
                runtime_metrics.observe(
                    "proactive_queue_to_speech_ms",
                    max(0, transition_now - int(row["created_at"])) * 1000,
                )
                if old is ProactiveSpeechState.WAITING_FOR_GAP:
                    runtime_metrics.observe(
                        "proactive_waiting_for_gap_ms",
                        max(0, transition_now - int(row["last_transition_at"])) * 1000,
                    )
                if old in {
                    ProactiveSpeechState.RESUME_PENDING,
                    ProactiveSpeechState.REPLAY_PENDING,
                }:
                    runtime_metrics.observe(
                        "proactive_interrupted_to_resume_ms",
                        max(0, transition_now - int(row["interrupted_at"] or transition_now))
                        * 1000,
                    )
            allowed = {
                "speech_session_id",
                "speech_started_at",
                "completed_at",
                "interrupted_at",
                "resume_until",
                "delivered_fraction",
                "replay_count",
                "retry_count",
                "generation",
            }
            updates = {key: value for key, value in (fields or {}).items() if key in allowed}
            assignments = [
                "lifecycle_state=?",
                "state_reason=?",
                "updated_at=?",
                "last_transition_at=?",
            ]
            values: list[Any] = [new.value, reason, transition_now, transition_now]
            for key, value in updates.items():
                assignments.append(f"{key}=?")
                values.append(value)
            values.append(event_id)
            connection.execute(
                f"UPDATE proactive_speech_lifecycle SET {', '.join(assignments)} WHERE event_id=?",
                values,
            )
            self._audit_transition(connection, event_id, old, new, reason, metadata)
        item = self.get(event_id)
        if item is None:
            raise KeyError(event_id)
        return item

    def _reconcile_restart(self) -> None:
        now = self.now_fn()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_id,lifecycle_state,freshness_deadline,resume_until,importance "
                "FROM proactive_speech_lifecycle WHERE lifecycle_state NOT IN ('COMPLETED','DROPPED','CANCELLED')"
            ).fetchall()
            for row in rows:
                event_id = str(row["event_id"])
                old = ProactiveSpeechState(str(row["lifecycle_state"]))
                if int(row["freshness_deadline"]) <= now:
                    new, reason = ProactiveSpeechState.CANCELLED, "expired_during_restart"
                elif old is ProactiveSpeechState.SPEAKING:
                    # Delivery completion is unknowable after process death. Never replay blindly.
                    new, reason = ProactiveSpeechState.DROPPED, "restart_delivery_outcome_unknown"
                elif old is ProactiveSpeechState.INTERRUPTED:
                    resumable = int(row["resume_until"] or 0) > now and int(row["importance"]) >= 95
                    new = (
                        ProactiveSpeechState.RESUME_PENDING
                        if resumable
                        else ProactiveSpeechState.DROPPED
                    )
                    reason = "restart_resume_reconciled" if resumable else "restart_resume_expired"
                else:
                    continue
                connection.execute(
                    "UPDATE proactive_speech_lifecycle SET lifecycle_state=?,state_reason=?,"
                    "updated_at=?,last_transition_at=? WHERE event_id=?",
                    (new.value, reason, now, now, event_id),
                )
                self._audit_transition(connection, event_id, old, new, reason)

    def queue(self, request: SpeechQueueRequest) -> dict[str, Any]:
        self.initialise()
        now = self.now_fn()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM proactive_speech_lifecycle WHERE dedupe_key=? "
                "AND lifecycle_state NOT IN ('COMPLETED','DROPPED','CANCELLED') "
                "ORDER BY created_at DESC LIMIT 1",
                (request.dedupe_key,),
            ).fetchone()
            if existing is not None:
                runtime_metrics.increment("proactive_speech_deduplicated")
                decoded = self._decode(existing)
                if decoded is None:
                    raise RuntimeError("Unable to decode proactive speech item")
                return decoded

            superseded = connection.execute(
                "SELECT event_id,lifecycle_state FROM proactive_speech_lifecycle "
                "WHERE supersession_key=? AND lifecycle_state IN "
                "('QUEUED','WAITING_FOR_GAP','INTERRUPTED','RESUME_PENDING','REPLAY_PENDING')",
                (request.supersession_key,),
            ).fetchall()
            for row in superseded:
                old = ProactiveSpeechState(str(row["lifecycle_state"]))
                event_id = str(row["event_id"])
                connection.execute(
                    "UPDATE proactive_speech_lifecycle SET lifecycle_state='CANCELLED',"
                    "state_reason='superseded',updated_at=?,last_transition_at=? WHERE event_id=?",
                    (now, now, event_id),
                )
                self._audit_transition(
                    connection,
                    event_id,
                    old,
                    ProactiveSpeechState.CANCELLED,
                    "superseded",
                    {"superseded_by": request.event_id},
                )
                runtime_metrics.increment("proactive_speech_superseded")

            connection.execute(
                "INSERT INTO proactive_speech_lifecycle ("
                "event_id,event_type,message,importance,target_user,endpoint,"
                "lifecycle_state,state_reason,"
                "freshness_deadline,dedupe_key,supersession_key,provenance_json,"
                "trigger_evidence_json,interruption_policy,resume_policy,created_at,updated_at,"
                "last_transition_at) VALUES(?,?,?,?,?,?,'QUEUED','admitted',?,?,?,?,?,?,?,?,?,?)",
                (
                    request.event_id,
                    request.event_type,
                    request.message,
                    max(0, min(100, int(request.importance))),
                    request.target_user,
                    request.endpoint,
                    int(request.freshness_deadline),
                    request.dedupe_key,
                    request.supersession_key,
                    json.dumps(request.provenance, separators=(",", ":"), default=str),
                    json.dumps(request.trigger_evidence, separators=(",", ":"), default=str),
                    request.interruption_policy,
                    request.resume_policy,
                    now,
                    now,
                    now,
                ),
            )
            self._audit_transition(
                connection,
                request.event_id,
                None,
                ProactiveSpeechState.QUEUED,
                "admitted",
            )
        item = self.get(request.event_id)
        if item is None:
            raise RuntimeError("Queued proactive speech item was not persisted")
        return item

    def get(self, event_id: str) -> dict[str, Any] | None:
        self.initialise()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_speech_lifecycle WHERE event_id=?", (event_id,)
            ).fetchone()
        return self._decode(row)

    def list_items(self, *, active_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        self.initialise()
        clause = (
            "WHERE lifecycle_state NOT IN ('COMPLETED','DROPPED','CANCELLED')"
            if active_only
            else ""
        )
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM proactive_speech_lifecycle {clause} "
                "ORDER BY importance DESC,created_at LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
        return [item for row in rows if (item := self._decode(row)) is not None]

    def transitions(self, event_id: str) -> list[dict[str, Any]]:
        self.initialise()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM proactive_speech_transitions WHERE event_id=? "
                "ORDER BY transition_id",
                (event_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(str(item.pop("metadata_json")))
            except (json.JSONDecodeError, TypeError):
                item["metadata"] = {}
            result.append(item)
        return result

    async def set_interaction_active(self, owner: str, active: bool) -> None:
        if active:
            self._interaction_owners.add(owner)
        else:
            self._interaction_owners.discard(owner)
            await self.process_ready()

    async def _is_fresh(self, item: dict[str, Any]) -> bool:
        if int(item["freshness_deadline"]) <= self.now_fn():
            return False
        try:
            return bool(await self.revalidate(item))
        except Exception:
            # Missing evidence is not proof that a claim remains current.
            return False

    async def _cancel_if_stale(self, item: dict[str, Any]) -> bool:
        if await self._is_fresh(item):
            return False
        self._transition(
            str(item["event_id"]),
            ProactiveSpeechState.CANCELLED,
            "freshness_revalidation_failed",
        )
        runtime_metrics.increment("proactive_speech_cancelled_before_speaking")
        return True

    async def process_ready(self) -> list[dict[str, Any]]:
        self.initialise()
        async with self._lock:
            await self._reconcile_speaking()
            results: list[dict[str, Any]] = []
            active = self.list_items(active_only=True, limit=100)
            for item in active:
                state = ProactiveSpeechState(str(item["lifecycle_state"]))
                if state not in {
                    ProactiveSpeechState.QUEUED,
                    ProactiveSpeechState.WAITING_FOR_GAP,
                    ProactiveSpeechState.RESUME_PENDING,
                    ProactiveSpeechState.REPLAY_PENDING,
                }:
                    continue
                if await self._cancel_if_stale(item):
                    continue
                event_id = str(item["event_id"])
                if state is ProactiveSpeechState.QUEUED:
                    item = self._transition(
                        event_id,
                        ProactiveSpeechState.WAITING_FOR_GAP,
                        "awaiting_conversational_gap",
                    )
                    state = ProactiveSpeechState.WAITING_FOR_GAP
                activity = await self.endpoint_status(str(item["endpoint"]))
                if self._interaction_owners or not activity.available or activity.busy:
                    continue
                competing = next(
                    (
                        other
                        for other in self.list_items(active_only=True, limit=100)
                        if other["endpoint"] == item["endpoint"]
                        and other["lifecycle_state"] == ProactiveSpeechState.SPEAKING.value
                    ),
                    None,
                )
                if competing is not None:
                    continue
                replay = state is ProactiveSpeechState.REPLAY_PENDING
                started = await self.start_speech(item, replay)
                if not started.accepted:
                    # Keep the item eligible for a bounded later cycle without claiming speech.
                    with self._connect() as connection:
                        connection.execute(
                            "UPDATE proactive_speech_lifecycle SET retry_count=retry_count+1,"
                            "state_reason='endpoint_start_rejected',updated_at=? WHERE event_id=?",
                            (self.now_fn(), event_id),
                        )
                    continue
                now = self.now_fn()
                fields = {
                    "speech_session_id": started.session_id or str(uuid.uuid4()),
                    "speech_started_at": now,
                    "generation": int(item["generation"]) + (1 if replay else 0),
                    "replay_count": int(item["replay_count"]) + (1 if replay else 0),
                }
                item = self._transition(
                    event_id,
                    ProactiveSpeechState.SPEAKING,
                    "playback_started",
                    metadata={"replay": replay, "detail": started.detail},
                    fields=fields,
                )
                results.append(item)
                if started.completed:
                    results.append(self.mark_completed(event_id, reason="playback_completed"))
            return results

    async def _reconcile_speaking(self) -> None:
        for item in self.list_items(active_only=True, limit=100):
            if item["lifecycle_state"] != ProactiveSpeechState.SPEAKING.value:
                continue
            activity = await self.endpoint_status(str(item["endpoint"]))
            if activity.user_speaking:
                await self.interrupt(
                    event_id=str(item["event_id"]),
                    topic_changed=False,
                    delivered_fraction=float(item["delivered_fraction"]),
                )
                continue
            started_at = int(item["speech_started_at"] or self.now_fn())
            if (
                activity.available
                and not activity.assistant_speaking
                and not activity.turn_in_progress
                and self.now_fn() - started_at >= self.completion_grace_seconds
            ):
                self.mark_completed(str(item["event_id"]), reason="endpoint_playback_idle")

    def mark_completed(
        self, event_id: str, *, reason: str = "playback_completed"
    ) -> dict[str, Any]:
        item = self.get(event_id)
        if item is None:
            raise KeyError(event_id)
        if item["lifecycle_state"] == ProactiveSpeechState.COMPLETED.value:
            return item
        return self._transition(
            event_id,
            ProactiveSpeechState.COMPLETED,
            reason,
            fields={"completed_at": self.now_fn(), "delivered_fraction": 1.0},
        )

    async def interrupt(
        self,
        *,
        event_id: str | None = None,
        endpoint: str | None = None,
        topic_changed: bool,
        explicit_continuation: bool = False,
        delivered_fraction: float = 0.0,
        defer_decision: bool = False,
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in self.list_items(active_only=True, limit=100)
            if item["lifecycle_state"] == ProactiveSpeechState.SPEAKING.value
            and (event_id is None or item["event_id"] == event_id)
            and (endpoint is None or item["endpoint"] == endpoint)
        ]
        if not candidates:
            return None
        item = candidates[0]
        started = time.monotonic()
        stopped = await self.stop_speech(item)
        runtime_metrics.observe(
            "proactive_interrupt_audio_stop_ms", (time.monotonic() - started) * 1000
        )
        fraction = max(0.0, min(1.0, float(delivered_fraction)))
        item = self._transition(
            str(item["event_id"]),
            ProactiveSpeechState.INTERRUPTED,
            "user_interrupted",
            metadata={"audio_stop_confirmed": stopped},
            fields={
                "interrupted_at": self.now_fn(),
                "resume_until": self.now_fn() + self.resume_window_seconds,
                "delivered_fraction": fraction,
            },
        )
        if defer_decision:
            return item
        return await self.decide_interruption(
            str(item["event_id"]),
            topic_changed=topic_changed,
            explicit_continuation=explicit_continuation,
        )

    async def decide_interruption(
        self,
        event_id: str,
        *,
        topic_changed: bool,
        explicit_continuation: bool = False,
    ) -> dict[str, Any]:
        item = self.get(event_id)
        if item is None:
            raise KeyError(event_id)
        if item["lifecycle_state"] != ProactiveSpeechState.INTERRUPTED.value:
            raise ValueError("Only an interrupted proactive contribution can be decided")
        if not await self._is_fresh(item):
            return self._transition(
                event_id, ProactiveSpeechState.CANCELLED, "interrupted_event_became_stale"
            )
        if int(item["resume_until"] or 0) <= self.now_fn():
            return self._transition(event_id, ProactiveSpeechState.DROPPED, "resume_window_expired")
        if topic_changed and not explicit_continuation:
            return self._transition(event_id, ProactiveSpeechState.DROPPED, "user_changed_topic")
        fraction = float(item["delivered_fraction"])
        if explicit_continuation:
            target = (
                ProactiveSpeechState.REPLAY_PENDING
                if fraction < 0.15
                else ProactiveSpeechState.RESUME_PENDING
            )
            return self._transition(event_id, target, "user_requested_continuation")
        if int(item["importance"]) >= 95:
            target = (
                ProactiveSpeechState.REPLAY_PENDING
                if fraction < 0.15
                else ProactiveSpeechState.RESUME_PENDING
            )
            return self._transition(event_id, target, "important_interruption_deferred")
        return self._transition(event_id, ProactiveSpeechState.DROPPED, "interrupted_noncritical")

    async def request_continuation(
        self,
        event_id: str | None = None,
        *,
        principal_id: str | None = None,
    ) -> dict[str, Any] | None:
        candidates = [
            item
            for item in self.list_items(active_only=True, limit=100)
            if item["lifecycle_state"]
            in {
                ProactiveSpeechState.INTERRUPTED.value,
                ProactiveSpeechState.RESUME_PENDING.value,
                ProactiveSpeechState.REPLAY_PENDING.value,
            }
            and (event_id is None or item["event_id"] == event_id)
            and (principal_id is None or item["target_user"] in {principal_id, "all"})
        ]
        if not candidates:
            return None
        item = candidates[0]
        state = ProactiveSpeechState(str(item["lifecycle_state"]))
        if state is ProactiveSpeechState.INTERRUPTED:
            item = await self.decide_interruption(
                str(item["event_id"]), topic_changed=False, explicit_continuation=True
            )
        elif int(item["resume_until"] or 0) <= self.now_fn():
            item = self._transition(
                str(item["event_id"]),
                ProactiveSpeechState.DROPPED,
                "resume_window_expired",
            )
        if item["lifecycle_state"] in {
            ProactiveSpeechState.RESUME_PENDING.value,
            ProactiveSpeechState.REPLAY_PENDING.value,
        }:
            await self.process_ready()
            item = self.get(str(item["event_id"])) or item
        return item

    async def drop_undecided_interruptions(self) -> int:
        dropped = 0
        for item in self.list_items(active_only=True, limit=100):
            if item["lifecycle_state"] != ProactiveSpeechState.INTERRUPTED.value:
                continue
            await self.decide_interruption(str(item["event_id"]), topic_changed=True)
            dropped += 1
        return dropped

    async def cancel(self, event_id: str, reason: str) -> dict[str, Any]:
        item = self.get(event_id)
        if item is None:
            raise KeyError(event_id)
        state = ProactiveSpeechState(str(item["lifecycle_state"]))
        if state is ProactiveSpeechState.SPEAKING:
            await self.stop_speech(item)
        return self._transition(event_id, ProactiveSpeechState.CANCELLED, reason)
