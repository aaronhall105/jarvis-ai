"""Deterministic, durable room occupancy derived from grounded HA evidence.

The engine is the single Core-owned occupancy state machine.  It consumes
``HomeSnapshot`` evidence, never performs an action, and never asks a language
model to choose a state.  Persisted state is reconciled with live evidence on
every refresh; stale stored observations are not trusted after restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable

from app.home_canonicalization import occupancy_evidence_class
from app.home_intelligence import GroundedHomeEntity, HomeSnapshot
from app.runtime_observability import runtime_metrics


class RoomOccupancyValue(str, Enum):
    OCCUPIED = "OCCUPIED"
    LIKELY_OCCUPIED = "LIKELY_OCCUPIED"
    PROBABLY_CLEAR = "PROBABLY_CLEAR"
    UNKNOWN = "UNKNOWN"


class EvidenceStrength(str, Enum):
    STRONG = "STRONG"
    SUPPORTING = "SUPPORTING"
    WEAK = "WEAK"


class OccupancyFreshness(str, Enum):
    CURRENT = "CURRENT"
    RECENT = "RECENT"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class RoomOccupancyPolicy:
    """Timing policy shared by all rooms unless configuration overrides it."""

    strong_persistence_seconds: float = 300.0
    motion_persistence_seconds: float = 120.0
    clear_confirmation_seconds: float = 300.0
    disconnected_stale_seconds: float = 120.0

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = float(getattr(self, field_name))
            if value < 1.0:
                raise ValueError(f"{field_name} must be at least one second")


@dataclass(frozen=True, slots=True)
class OccupancyEvidence:
    source_type: str
    entity_id: str
    device_id: str | None
    area_id: str
    display_name: str
    observed_state: str
    observed_at: str
    strength: EvidenceStrength
    freshness: OccupancyFreshness
    available: bool
    active: bool
    provenance: str
    occupancy_count: int | None = None
    identified_people: tuple[str, ...] = ()
    anonymous_person_count: int | None = None

    def as_dict(self, *, diagnostics: bool = False) -> dict[str, Any]:
        value = asdict(self)
        value["strength"] = self.strength.value
        value["freshness"] = self.freshness.value
        if not diagnostics:
            value.pop("entity_id", None)
            value.pop("device_id", None)
            value.pop("provenance", None)
        return value


@dataclass(frozen=True, slots=True)
class RoomOccupancyState:
    area_id: str
    room_name: str
    state: RoomOccupancyValue
    confidence: float
    observed_at: str
    last_changed_at: str
    last_strong_evidence_at: str | None
    clear_candidate_since: str | None
    freshness: OccupancyFreshness
    evidence: tuple[OccupancyEvidence, ...]
    reason_code: str
    source_health: str

    def as_dict(self, *, diagnostics: bool = False) -> dict[str, Any]:
        return {
            "area_id": self.area_id,
            "room_name": self.room_name,
            "state": self.state.value,
            "confidence": round(self.confidence, 3),
            "observed_at": self.observed_at,
            "last_changed_at": self.last_changed_at,
            "last_strong_evidence_at": self.last_strong_evidence_at,
            "clear_candidate_since": self.clear_candidate_since,
            "freshness": self.freshness.value,
            "evidence": [item.as_dict(diagnostics=diagnostics) for item in self.evidence],
            "reason_code": self.reason_code,
            "source_health": self.source_health,
        }


_ACTIVE = frozenset({"on", "detected", "true", "occupied", "person"})
_MEDIA_ACTIVE = frozenset({"on", "playing", "paused", "buffering"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _evidence_kind(entity: GroundedHomeEntity) -> tuple[str, EvidenceStrength] | None:
    if entity.domain == "media_player":
        return "media_activity", EvidenceStrength.SUPPORTING
    if entity.domain != "binary_sensor":
        return None
    evidence_class = occupancy_evidence_class(entity)
    if evidence_class in {"person_presence", "presence"}:
        return evidence_class, EvidenceStrength.STRONG
    if evidence_class == "motion":
        return evidence_class, EvidenceStrength.SUPPORTING
    if evidence_class in {"object_detection", "diagnostic_observation"}:
        return evidence_class, EvidenceStrength.WEAK
    return None


def _evidence_from_entity(
    entity: GroundedHomeEntity,
    *,
    area_id: str,
    observed_at: datetime,
    source_connected: bool,
    disconnected_at: datetime | None,
    policy: RoomOccupancyPolicy,
) -> OccupancyEvidence | None:
    classified = _evidence_kind(entity)
    if classified is None:
        return None
    source_type, strength = classified
    source_time = (
        _parse_time(entity.source_last_changed)
        or _parse_time(entity.source_last_updated)
        or _parse_time(entity.observed_at)
        or observed_at
    )
    age = max(0.0, (observed_at - source_time).total_seconds())
    if not entity.available:
        freshness = OccupancyFreshness.UNAVAILABLE
    elif (
        not source_connected
        and disconnected_at is not None
        and (observed_at - disconnected_at).total_seconds() > policy.disconnected_stale_seconds
    ):
        freshness = OccupancyFreshness.STALE
    elif age <= policy.motion_persistence_seconds:
        freshness = OccupancyFreshness.CURRENT
    else:
        freshness = OccupancyFreshness.RECENT
    state = entity.state.casefold()
    active = state in (_MEDIA_ACTIVE if entity.domain == "media_player" else _ACTIVE)
    return OccupancyEvidence(
        source_type=source_type,
        entity_id=entity.entity_id,
        device_id=entity.device_id,
        area_id=area_id,
        display_name=entity.name,
        observed_state=state,
        observed_at=_iso(source_time),
        strength=strength,
        freshness=freshness,
        available=entity.available,
        active=active,
        provenance="home_assistant_registry_state",
    )


class RoomOccupancyEngine:
    """Thread-safe state machine with SQLite-backed state and transition history."""

    def __init__(
        self,
        *,
        database_path: str | Path | None = None,
        policy: RoomOccupancyPolicy | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.database_path = Path(database_path) if database_path is not None else None
        if self.database_path is not None:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.policy = policy or RoomOccupancyPolicy()
        self._clock = clock
        self._lock = threading.RLock()
        self._states: dict[str, RoomOccupancyState] = {}
        self._sources: dict[str, dict[str, GroundedHomeEntity]] = {}
        self._room_names: dict[str, str] = {}
        self._source_connected = True
        self._source_disconnected_at: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._memory_transitions: list[dict[str, Any]] = []
        if self.database_path is not None:
            self._initialise_database()
            self._load_states()

    def _connect(self) -> sqlite3.Connection:
        if self.database_path is None:
            raise RuntimeError("Room occupancy persistence is disabled")
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialise_database(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS room_occupancy_state (
                    area_id TEXT PRIMARY KEY,
                    room_name TEXT NOT NULL,
                    state TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    last_changed_at TEXT NOT NULL,
                    last_strong_evidence_at TEXT,
                    clear_candidate_since TEXT,
                    freshness TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    source_health TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS room_occupancy_transitions (
                    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    area_id TEXT NOT NULL,
                    room_name TEXT NOT NULL,
                    previous_state TEXT,
                    new_state TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_room_occupancy_transitions_area_time
                ON room_occupancy_transitions(area_id, occurred_at DESC);
                """
            )

    @staticmethod
    def _evidence_from_mapping(value: Mapping[str, Any]) -> OccupancyEvidence:
        return OccupancyEvidence(
            source_type=str(value.get("source_type") or "diagnostic_observation"),
            entity_id=str(value.get("entity_id") or ""),
            device_id=str(value.get("device_id") or "") or None,
            area_id=str(value.get("area_id") or ""),
            display_name=str(value.get("display_name") or "Evidence"),
            observed_state=str(value.get("observed_state") or "unknown"),
            observed_at=str(value.get("observed_at") or ""),
            strength=EvidenceStrength(str(value.get("strength") or "WEAK")),
            freshness=OccupancyFreshness(str(value.get("freshness") or "STALE")),
            available=bool(value.get("available")),
            active=bool(value.get("active")),
            provenance=str(value.get("provenance") or "persisted_state"),
            occupancy_count=(
                int(value["occupancy_count"])
                if isinstance(value.get("occupancy_count"), int)
                else None
            ),
            identified_people=tuple(
                str(item) for item in value.get("identified_people") or () if str(item).strip()
            ),
            anonymous_person_count=(
                int(value["anonymous_person_count"])
                if isinstance(value.get("anonymous_person_count"), int)
                else None
            ),
        )

    def _load_states(self) -> None:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM room_occupancy_state").fetchall()
        for row in rows:
            try:
                raw_evidence = json.loads(str(row["evidence_json"]))
                evidence = tuple(
                    self._evidence_from_mapping(item)
                    for item in raw_evidence
                    if isinstance(item, Mapping)
                )
                state = RoomOccupancyState(
                    area_id=str(row["area_id"]),
                    room_name=str(row["room_name"]),
                    state=RoomOccupancyValue(str(row["state"])),
                    confidence=float(row["confidence"]),
                    observed_at=str(row["observed_at"]),
                    last_changed_at=str(row["last_changed_at"]),
                    last_strong_evidence_at=row["last_strong_evidence_at"],
                    clear_candidate_since=row["clear_candidate_since"],
                    freshness=OccupancyFreshness(str(row["freshness"])),
                    evidence=evidence,
                    reason_code=str(row["reason_code"]),
                    source_health=str(row["source_health"]),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            self._states[state.area_id] = state
            self._room_names[state.area_id] = state.room_name

    def set_source_connected(self, connected: bool) -> None:
        with self._lock:
            value = bool(connected)
            if value:
                self._source_disconnected_at = None
            elif self._source_connected:
                self._source_disconnected_at = self._clock()
            self._source_connected = value

    async def start(self, *, evaluation_interval_seconds: float = 5.0) -> None:
        if self._task is not None:
            return
        interval = max(1.0, float(evaluation_interval_seconds))

        async def run() -> None:
            while True:
                await asyncio.sleep(interval)
                self.evaluate()

        self._task = asyncio.create_task(run(), name="jarvis_room_occupancy")

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def reconcile(self, snapshot: HomeSnapshot) -> dict[str, RoomOccupancyState]:
        """Reconcile persisted state against one authoritative current snapshot."""

        started = time.monotonic()
        observed_at = _parse_time(snapshot.observed_at) or self._clock()
        entities = snapshot.presentation_entities or snapshot.entities
        names = {
            str(item.get("area_id") or item.get("id")): str(item.get("name") or "Room")
            for item in snapshot.areas
            if item.get("area_id") or item.get("id")
        }
        grouped: dict[str, dict[str, GroundedHomeEntity]] = {}
        for entity in entities:
            if not entity.area_id or _evidence_kind(entity) is None:
                continue
            grouped.setdefault(entity.area_id, {})[entity.entity_id] = entity
            if entity.area_name:
                names.setdefault(entity.area_id, entity.area_name)
        with self._lock:
            self._source_connected = True
            self._source_disconnected_at = None
            for area_id, sources in grouped.items():
                self._sources[area_id] = sources
            for area_id in set(names) | set(grouped) | set(self._states):
                self._room_names[area_id] = names.get(
                    area_id, self._room_names.get(area_id, area_id.replace("_", " ").title())
                )
                # A live full snapshot authoritatively replaces a room's old
                # source set, including sources removed from the HA registry.
                self._sources[area_id] = grouped.get(area_id, {})
                self._recompute(area_id, observed_at)
            result = dict(self._states)
        runtime_metrics.observe(
            "room_occupancy_reconciliation_ms", (time.monotonic() - started) * 1000
        )
        return result

    def observe(self, entity: GroundedHomeEntity, *, observed_at: datetime | None = None) -> None:
        """Incrementally update only the room affected by one HA state event."""

        if not entity.area_id or _evidence_kind(entity) is None:
            return
        started = time.monotonic()
        now = observed_at or self._clock()
        with self._lock:
            self._source_connected = True
            self._source_disconnected_at = None
            self._room_names[entity.area_id] = (
                entity.area_name
                or self._room_names.get(entity.area_id)
                or entity.area_id.replace("_", " ").title()
            )
            self._sources.setdefault(entity.area_id, {})[entity.entity_id] = entity
            self._recompute(entity.area_id, now)
        runtime_metrics.observe(
            "room_occupancy_evidence_update_ms", (time.monotonic() - started) * 1000
        )

    def observe_mapping(self, row: Mapping[str, Any]) -> None:
        """Consume one registry-enriched row from the existing awareness cache."""

        now = self._clock()
        try:
            entity = GroundedHomeEntity.from_state(row, _iso(now))
        except ValueError:
            return
        self.observe(entity, observed_at=now)

    def evaluate(self, *, observed_at: datetime | None = None) -> dict[str, RoomOccupancyState]:
        """Advance clear timers without manufacturing a sensor transition."""

        now = observed_at or self._clock()
        with self._lock:
            for area_id in set(self._sources) | set(self._states):
                self._recompute(area_id, now)
            return dict(self._states)

    def states(self) -> dict[str, RoomOccupancyState]:
        return self.evaluate()

    def state(self, area_id: str) -> RoomOccupancyState | None:
        return self.evaluate().get(area_id)

    def transitions(self, area_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        if self.database_path is None:
            return [
                item for item in reversed(self._memory_transitions) if item["area_id"] == area_id
            ][:safe_limit]
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT area_id,room_name,previous_state,new_state,occurred_at,
                          reason_code,confidence,evidence_json
                   FROM room_occupancy_transitions WHERE area_id=?
                   ORDER BY transition_id DESC LIMIT ?""",
                (area_id, safe_limit),
            ).fetchall()
        return [
            {
                "area_id": str(row["area_id"]),
                "room_name": str(row["room_name"]),
                "previous_state": row["previous_state"],
                "new_state": str(row["new_state"]),
                "occurred_at": str(row["occurred_at"]),
                "reason_code": str(row["reason_code"]),
                "confidence": float(row["confidence"]),
                "evidence": json.loads(str(row["evidence_json"])),
            }
            for row in rows
        ]

    def status(self) -> dict[str, Any]:
        with self._lock:
            counts = {state.value: 0 for state in RoomOccupancyValue}
            for item in self._states.values():
                counts[item.state.value] += 1
            transition_count = len(self._memory_transitions)
            if self.database_path is not None:
                with self._connect() as connection:
                    transition_count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM room_occupancy_transitions"
                        ).fetchone()[0]
                    )
            return {
                "room_count": len(self._states),
                "state_counts": counts,
                "transition_count": transition_count,
                "source_connected": self._source_connected,
                "policy": asdict(self.policy),
            }

    def _recompute(self, area_id: str, now: datetime) -> RoomOccupancyState:
        previous = self._states.get(area_id)
        evidence = tuple(
            item
            for entity in self._sources.get(area_id, {}).values()
            if (
                item := _evidence_from_entity(
                    entity,
                    area_id=area_id,
                    observed_at=now,
                    source_connected=self._source_connected,
                    disconnected_at=self._source_disconnected_at,
                    policy=self.policy,
                )
            )
            is not None
        )
        result = self._derive(area_id, self._room_names[area_id], evidence, previous, now)
        self._states[area_id] = result
        self._persist(result, previous)
        return result

    def _derive(
        self,
        area_id: str,
        room_name: str,
        evidence: Sequence[OccupancyEvidence],
        previous: RoomOccupancyState | None,
        now: datetime,
    ) -> RoomOccupancyState:
        usable = tuple(
            item
            for item in evidence
            if item.available
            and item.freshness not in {OccupancyFreshness.STALE, OccupancyFreshness.UNAVAILABLE}
        )
        strong = tuple(item for item in usable if item.strength is EvidenceStrength.STRONG)
        supporting = tuple(item for item in usable if item.strength is EvidenceStrength.SUPPORTING)
        active_strong = tuple(item for item in strong if item.active)
        active_supporting = tuple(item for item in supporting if item.active)
        unavailable_strong = tuple(
            item
            for item in evidence
            if item.strength is EvidenceStrength.STRONG and item not in strong
        )
        prior_last_strong = _parse_time(
            previous.last_strong_evidence_at if previous is not None else None
        )
        active_times = [_parse_time(item.observed_at) for item in active_strong]
        latest_active = max((item for item in active_times if item is not None), default=None)
        last_strong = max(
            (item for item in (prior_last_strong, latest_active) if item is not None),
            default=None,
        )

        if not evidence:
            state = RoomOccupancyValue.UNKNOWN
            confidence = 0.0
            reason = "NO_OCCUPANCY_SOURCES"
            health = "MISSING"
            candidate = None
            freshness = OccupancyFreshness.UNAVAILABLE
        elif active_strong:
            state = RoomOccupancyValue.OCCUPIED
            confidence = 1.0 if len(active_strong) > 1 else 0.95
            reason = "FRESH_STRONG_EVIDENCE"
            health = "DEGRADED" if unavailable_strong else "HEALTHY"
            candidate = None
            freshness = OccupancyFreshness.CURRENT
        elif not strong:
            state = RoomOccupancyValue.UNKNOWN
            confidence = 0.0
            reason = "STRONG_SOURCES_UNAVAILABLE" if unavailable_strong else "NO_STRONG_SOURCE"
            health = "UNAVAILABLE" if unavailable_strong else "INSUFFICIENT"
            candidate = None
            freshness = (
                OccupancyFreshness.UNAVAILABLE if unavailable_strong else OccupancyFreshness.STALE
            )
        else:
            # Supporting evidence may extend a recent strong observation, but
            # never creates human occupancy from an UNKNOWN baseline.
            recent_strong = bool(
                last_strong is not None
                and now - last_strong <= timedelta(seconds=self.policy.strong_persistence_seconds)
            )
            prior_occupied = bool(
                previous is not None
                and previous.state
                in {RoomOccupancyValue.OCCUPIED, RoomOccupancyValue.LIKELY_OCCUPIED}
            )
            if active_supporting and (recent_strong or prior_occupied):
                state = RoomOccupancyValue.LIKELY_OCCUPIED
                confidence = 0.72
                reason = "RECENT_STRONG_WITH_SUPPORTING_EVIDENCE"
                candidate = None
                freshness = OccupancyFreshness.CURRENT
            else:
                evidence_times = [_parse_time(item.observed_at) for item in (*strong, *supporting)]
                latest_qualifying_observation = max(
                    (item for item in evidence_times if item is not None), default=now
                )
                candidate = (
                    _parse_time(previous.clear_candidate_since if previous is not None else None)
                    or latest_qualifying_observation
                )
                clear_elapsed = (now - candidate).total_seconds()
                if recent_strong or prior_occupied:
                    if clear_elapsed < self.policy.clear_confirmation_seconds:
                        state = RoomOccupancyValue.LIKELY_OCCUPIED
                        confidence = 0.62 if recent_strong else 0.55
                        reason = "STRONG_EVIDENCE_PERSISTENCE"
                        freshness = OccupancyFreshness.RECENT
                    else:
                        state = RoomOccupancyValue.PROBABLY_CLEAR
                        confidence = 0.8
                        reason = "CONSERVATIVE_CLEAR_INTERVAL_ELAPSED"
                        freshness = OccupancyFreshness.CURRENT
                elif clear_elapsed >= self.policy.clear_confirmation_seconds:
                    state = RoomOccupancyValue.PROBABLY_CLEAR
                    confidence = 0.75
                    reason = "NO_QUALIFYING_EVIDENCE_AFTER_CLEAR_INTERVAL"
                    freshness = OccupancyFreshness.CURRENT
                elif previous is not None and previous.state is RoomOccupancyValue.PROBABLY_CLEAR:
                    state = RoomOccupancyValue.PROBABLY_CLEAR
                    confidence = previous.confidence
                    reason = "CLEAR_STATE_RECONFIRMED"
                    freshness = OccupancyFreshness.CURRENT
                else:
                    state = RoomOccupancyValue.UNKNOWN
                    confidence = 0.25
                    reason = "CLEAR_INTERVAL_PENDING_WITHOUT_PRIOR_OCCUPANCY"
                    freshness = OccupancyFreshness.RECENT
            health = "DEGRADED" if unavailable_strong else "HEALTHY"
            if unavailable_strong and state is RoomOccupancyValue.PROBABLY_CLEAR:
                state = RoomOccupancyValue.UNKNOWN
                confidence = 0.2
                reason = "CONTRADICTORY_OR_UNAVAILABLE_STRONG_SOURCES"
                freshness = OccupancyFreshness.UNAVAILABLE

        observed = _iso(now)
        changed = previous is None or previous.state is not state
        if previous is not None and not changed:
            last_changed = previous.last_changed_at
        else:
            last_changed = observed
        return RoomOccupancyState(
            area_id=area_id,
            room_name=room_name,
            state=state,
            confidence=confidence,
            observed_at=observed,
            last_changed_at=last_changed,
            last_strong_evidence_at=_iso(last_strong) if last_strong is not None else None,
            clear_candidate_since=_iso(candidate) if candidate is not None else None,
            freshness=freshness,
            evidence=tuple(
                sorted(
                    evidence,
                    key=lambda item: (
                        0 if item.strength is EvidenceStrength.STRONG else 1,
                        0 if item.active else 1,
                        item.display_name.casefold(),
                    ),
                )
            ),
            reason_code=reason,
            source_health=health,
        )

    def _persist(
        self,
        state: RoomOccupancyState,
        previous: RoomOccupancyState | None,
    ) -> None:
        materially_unchanged = bool(
            previous is not None
            and previous.state is state.state
            and previous.confidence == state.confidence
            and previous.last_changed_at == state.last_changed_at
            and previous.last_strong_evidence_at == state.last_strong_evidence_at
            and previous.clear_candidate_since == state.clear_candidate_since
            and previous.freshness is state.freshness
            and previous.reason_code == state.reason_code
            and previous.source_health == state.source_health
            and previous.evidence == state.evidence
        )
        if materially_unchanged:
            return
        started = time.monotonic()
        evidence_json = json.dumps(
            [item.as_dict(diagnostics=True) for item in state.evidence],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        changed = previous is None or previous.state is not state.state
        transition = {
            "area_id": state.area_id,
            "room_name": state.room_name,
            "previous_state": previous.state.value if previous is not None else None,
            "new_state": state.state.value,
            "occurred_at": state.last_changed_at,
            "reason_code": state.reason_code,
            "confidence": state.confidence,
            "evidence": [item.as_dict(diagnostics=True) for item in state.evidence],
        }
        if self.database_path is None:
            if changed:
                self._memory_transitions.append(transition)
            runtime_metrics.observe(
                "room_occupancy_persistence_ms", (time.monotonic() - started) * 1000
            )
            return
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO room_occupancy_state(
                       area_id,room_name,state,confidence,observed_at,last_changed_at,
                       last_strong_evidence_at,clear_candidate_since,freshness,
                       reason_code,source_health,evidence_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(area_id) DO UPDATE SET
                       room_name=excluded.room_name,state=excluded.state,
                       confidence=excluded.confidence,observed_at=excluded.observed_at,
                       last_changed_at=excluded.last_changed_at,
                       last_strong_evidence_at=excluded.last_strong_evidence_at,
                       clear_candidate_since=excluded.clear_candidate_since,
                       freshness=excluded.freshness,reason_code=excluded.reason_code,
                       source_health=excluded.source_health,evidence_json=excluded.evidence_json,
                       updated_at=excluded.updated_at""",
                (
                    state.area_id,
                    state.room_name,
                    state.state.value,
                    state.confidence,
                    state.observed_at,
                    state.last_changed_at,
                    state.last_strong_evidence_at,
                    state.clear_candidate_since,
                    state.freshness.value,
                    state.reason_code,
                    state.source_health,
                    evidence_json,
                    state.observed_at,
                ),
            )
            if changed:
                connection.execute(
                    """INSERT INTO room_occupancy_transitions(
                           area_id,room_name,previous_state,new_state,occurred_at,
                           reason_code,confidence,evidence_json
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        state.area_id,
                        state.room_name,
                        previous.state.value if previous is not None else None,
                        state.state.value,
                        state.last_changed_at,
                        state.reason_code,
                        state.confidence,
                        evidence_json,
                    ),
                )
        runtime_metrics.observe(
            "room_occupancy_persistence_ms", (time.monotonic() - started) * 1000
        )


def entities_from_grounded_rows(
    rows: Iterable[Mapping[str, Any]], *, observed_at: str
) -> tuple[GroundedHomeEntity, ...]:
    """Convert the existing awareness cache contract without a second registry model."""

    values: list[GroundedHomeEntity] = []
    for row in rows:
        try:
            values.append(GroundedHomeEntity.from_state(row, observed_at))
        except ValueError:
            continue
    return tuple(values)


__all__ = [
    "EvidenceStrength",
    "OccupancyEvidence",
    "OccupancyFreshness",
    "RoomOccupancyEngine",
    "RoomOccupancyPolicy",
    "RoomOccupancyState",
    "RoomOccupancyValue",
    "entities_from_grounded_rows",
]
