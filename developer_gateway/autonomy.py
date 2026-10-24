"""Observe-only roadmap. This module has no development executor or control authority.

Only typed, content-free observations enter the journal. Research is a separate
untrusted evidence channel, never an instruction channel. Planning writes go to
a private journal, not source, Git, the Developer tool ledger or production data.
"""

from __future__ import annotations

import hashlib
import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

from .state import State

COMPONENTS = {
    "core": ("Jarvis", "AUTOMATION"),
    "database": ("Saved information", "MEMORY"),
    "home_assistant": ("Home connection", "HOME"),
    "task_centre": ("Task management", "AUTOMATION"),
    "followup_worker": ("Scheduled follow-ups", "AUTOMATION"),
    "developer": ("Development service", "DEVELOPER"),
    "api_developer": ("API development service", "DEVELOPER"),
}
ROADMAP = {"CURRENT", "NEXT", "WATCHING", "RESEARCHING", "COMPLETED", "ROLLED_BACK", "REJECTED"}


def priority(component: str) -> int:
    return {"core": 100, "database": 95, "home_assistant": 80}.get(component, 60)


def goal_evidence(component: str, observed_at: float) -> dict[str, Any]:
    label = COMPONENTS[component][0]
    source = (
        "core_health"
        if component == "core"
        else (
            "developer_health" if component in {"developer", "api_developer"} else "system_status"
        )
    )
    return {
        "problem": f"The {label.lower()} health observation reported a problem.",
        "reason": "Investigate a reported reliability problem before proposing source changes.",
        "source": source,
        "observed_at": observed_at,
        "healthy": False,
        "priority": priority(component),
        "expected_improvement": "Understand the failure before choosing a fix.",
        "success_measurement": "A repeatable diagnosis and healthy follow-up observations.",
        "rollback_measurement": "No release is authorized in observe-only mode.",
    }


class Governor:
    """Durable Phase 1 planner; constructing it cannot start engineering work."""

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        # Never open an unrelated Developer/user database through State's job
        # recovery logic, and never put the planning journal in a source tree.
        if path.is_symlink() or any(
            (parent / ".git").exists() for parent in path.resolve().parents
        ):
            raise ValueError("Observer journal must be outside source worktrees")
        if path.exists():
            if (
                path.stat().st_uid != os.getuid()
                or path.stat().st_nlink != 1
                or path.stat().st_mode & 0o077
            ):
                raise ValueError("Observer journal ownership mismatch")
            existing = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                if not existing.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='observer_meta'"
                ).fetchone():
                    raise ValueError("Not an observer journal")
            finally:
                existing.close()
        self.clock = clock
        self._refresh_lock = asyncio.Lock()
        self._last_refresh: float | None = None
        self.state = State(path)
        self.db = self.state.db
        with self.db:
            self.db.executescript(
                "CREATE TABLE IF NOT EXISTS observations (component TEXT PRIMARY KEY, "
                "healthy INTEGER NOT NULL, observed REAL NOT NULL, fingerprint TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS roadmap (id TEXT PRIMARY KEY, component TEXT NOT NULL, "
                "state TEXT NOT NULL, evidence TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);"
                "CREATE TABLE IF NOT EXISTS research (goal TEXT NOT NULL, evidence TEXT NOT NULL, "
                "state TEXT NOT NULL, sources TEXT NOT NULL, PRIMARY KEY(goal,evidence));"
                "CREATE TABLE IF NOT EXISTS observer_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            )
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(roadmap)")}
            if "details" not in columns:
                self.db.execute("ALTER TABLE roadmap ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")
            for row in self.db.execute(
                "SELECT id,component,created FROM roadmap WHERE details='{}'"
            ).fetchall():
                self.db.execute(
                    "UPDATE roadmap SET details=? WHERE id=?",
                    (json.dumps(goal_evidence(row["component"], row["created"])), row["id"]),
                )
            self.db.execute("UPDATE research SET state='interrupted' WHERE state='running'")
            self.db.execute("UPDATE roadmap SET state='NEXT' WHERE state='RESEARCHING'")
            self._prioritize(self.clock())

    def close(self) -> None:
        self.state.close()

    async def refresh(
        self, collector: Callable[[], tuple[dict[str, bool], tuple[str, ...]]]
    ) -> None:
        """Coalesce startup, schedule and dashboard events; never poll a model."""
        async with self._refresh_lock:
            if self._last_refresh is not None and time.monotonic() - self._last_refresh < 30:
                return
            readings, unavailable = await asyncio.to_thread(collector)
            self.observe(readings, unavailable)
            self._last_refresh = time.monotonic()

    def observe(self, readings: dict[str, bool], unavailable: tuple[str, ...] = ()) -> None:
        # Reject the entire event before persisting anything. No arbitrary text,
        # errors, prompts, URLs, tokens or provider response bodies are accepted.
        if set(readings) - COMPONENTS.keys() or any(type(v) is not bool for v in readings.values()):
            raise ValueError("Invalid observation")
        if set(unavailable) - {"core", "system", "developer"}:
            raise ValueError("Invalid collection status")
        now = self.clock()
        with self.db:
            for component, healthy in readings.items():
                fingerprint = hashlib.sha256(f"{component}:{healthy}".encode()).hexdigest()
                self.db.execute(
                    "INSERT INTO observations VALUES(?,?,?,?) ON CONFLICT(component) DO UPDATE SET "
                    "healthy=excluded.healthy,observed=excluded.observed,fingerprint=excluded.fingerprint",
                    (component, int(healthy), now, fingerprint),
                )
                if not healthy:
                    # One open idea per semantic health topic, not per polling tick.
                    self.db.execute(
                        "INSERT OR IGNORE INTO roadmap VALUES(?,?,'NEXT',?,?,?,?)",
                        (
                            "health-" + component,
                            component,
                            fingerprint,
                            now,
                            now,
                            json.dumps(goal_evidence(component, now)),
                        ),
                    )
                    # A recovered topic can fail again. Preserve its identity
                    # and rejected-idea fence, but do not strand a fresh failure
                    # in WATCHING forever after an earlier healthy observation.
                    self.db.execute(
                        "UPDATE roadmap SET state='NEXT',updated=?,details=? "
                        "WHERE component=? AND state='WATCHING'",
                        (now, json.dumps(goal_evidence(component, now)), component),
                    )
                else:
                    # Recovery is not proof that an autonomous improvement shipped.
                    self.db.execute(
                        "UPDATE roadmap SET state='WATCHING',updated=? WHERE component=? "
                        "AND state IN ('CURRENT','NEXT','RESEARCHING')",
                        (now, component),
                    )
            self.db.execute(
                "INSERT OR REPLACE INTO observer_meta VALUES('last_observation',?)", (str(now),)
            )
            self.db.execute(
                "INSERT OR REPLACE INTO observer_meta VALUES('last_error',?)",
                (json.dumps(list(unavailable)),),
            )
            if set(readings) == COMPONENTS.keys() and all(readings.values()) and not unavailable:
                self.db.execute(
                    "INSERT OR REPLACE INTO observer_meta VALUES('last_healthy_cycle',?)",
                    (str(now),),
                )
            self._prioritize(now)

    def _prioritize(self, now: float) -> None:
        # Broken core/storage affects more functions than one integration. This
        # initial health-only policy makes no claims about unmeasured latency.
        rows = self.db.execute(
            "SELECT r.id,r.component FROM roadmap r JOIN observations o USING(component) "
            "WHERE r.state IN ('CURRENT','NEXT') AND o.healthy=0 AND o.observed>=?",
            (now - 900,),
        ).fetchall()
        self.db.execute("UPDATE roadmap SET state='NEXT' WHERE state='CURRENT'")
        if self.db.execute("SELECT 1 FROM roadmap WHERE state='RESEARCHING'").fetchone():
            return
        if rows:
            best = sorted(rows, key=lambda r: (-priority(r["component"]), r["id"]))[0]
            self.db.execute("UPDATE roadmap SET state='CURRENT' WHERE id=?", (best["id"],))

    def reject(self, goal: str) -> None:
        """Record an unsuccessful idea; unchanged evidence never reopens it."""
        with self.db:
            self.db.execute(
                "UPDATE roadmap SET state='REJECTED',updated=? WHERE id=? AND state!='COMPLETED'",
                (self.clock(), goal),
            )
            self._prioritize(self.clock())

    async def research(self, goal: str, engine: Any) -> bool:
        """Adapter for existing ResearchEngine; never starts a second research agent.

        Store provenance, not untrusted page prose. A failed/interrupted question
        is not automatically retried on every restart or identical observation.
        """
        row = self.db.execute(
            "SELECT r.* FROM roadmap r JOIN observations o USING(component) "
            "WHERE r.id=? AND r.state='CURRENT' AND o.healthy=0 AND o.observed>=?",
            (goal, self.clock() - 900),
        ).fetchone()
        if not row:
            return False
        with self.db:
            claimed = self.db.execute(
                "INSERT OR IGNORE INTO research VALUES(?,?,'running','[]')", (goal, row["evidence"])
            ).rowcount
            if claimed:
                self.db.execute("UPDATE roadmap SET state='RESEARCHING' WHERE id=?", (goal,))
        if not claimed:
            return False
        outcome = "interrupted"
        sources: list[dict[str, Any]] = []
        try:
            label = COMPONENTS[row["component"]][0]
            async with asyncio.timeout(120):
                result = await engine.research(
                    f"Official diagnostic guidance for {label} health failures"
                )
            sources = []
            for source in result.sources[:8]:
                url = urlsplit(source.url)
                if (
                    url.scheme != "https"
                    or not url.hostname
                    or url.username
                    or url.password
                    or url.query
                ):
                    continue
                # Provenance URL only; no source title, extract or summary can
                # become an instruction, capability claim or UI assertion.
                sources.append(
                    {
                        "url": urlunsplit((url.scheme, url.netloc, url.path[:1024], "", "")),
                        "classification": "HYPOTHESIS",
                        "retrieved_at": self.clock(),
                    }
                )
            outcome = "completed" if result.complete and sources else "unavailable"
        except Exception:
            outcome, sources = "failed", []
        finally:
            with self.db:
                self.db.execute(
                    "UPDATE research SET state=?,sources=? WHERE goal=? AND evidence=?",
                    (outcome, json.dumps(sources), goal, row["evidence"]),
                )
                self.db.execute(
                    "UPDATE roadmap SET state='NEXT' WHERE id=? AND state='RESEARCHING'", (goal,)
                )
                self._prioritize(self.clock())
        return outcome == "completed"

    def snapshot(self, guardian: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        trusted = guardian.get("trusted") is True
        status = "NEEDS ATTENTION"
        if trusted and guardian.get("state") == "STOPPED":
            status = "STOPPED"
        elif trusted and guardian.get("state") == "ACTIVE":
            status = "PAUSED"  # Observe-only does not become a developer on resume.
        observations = {
            r["component"]: dict(r) for r in self.db.execute("SELECT * FROM observations")
        }
        plans = []
        for row in self.db.execute("SELECT * FROM roadmap ORDER BY created,id"):
            observed = observations.get(row["component"], {})
            fresh = observed.get("observed", 0) >= now - 900
            if row["state"] in {"REJECTED", "ROLLED_BACK", "COMPLETED"}:
                continue
            label = COMPONENTS[row["component"]][0]
            details = json.loads(row["details"])
            plans.append(
                {
                    **details,
                    "id": row["id"],
                    "state": row["state"],
                    "title": f"Investigate {label.lower()}",
                    "why": f"The latest {label.lower()} health observation reported a problem."
                    if fresh and not observed.get("healthy")
                    else "An earlier health observation needs follow-up.",
                    "doing": "Looking for relevant diagnostic guidance."
                    if row["state"] == "RESEARCHING"
                    else "Checking evidence before proposing a change.",
                    "progress": "Researching" if row["state"] == "RESEARCHING" else "Thinking",
                    "evidence_id": row["evidence"],
                    "observed_at": observed.get("observed"),
                    "fresh": fresh,
                }
            )
        current = next(
            (p for p in plans if p["state"] in {"CURRENT", "RESEARCHING"} and p["fresh"]), None
        )
        capabilities = []
        for component, (label, category) in COMPONENTS.items():
            observation = observations.get(component)
            verified = bool(observation and observation["observed"] >= now - 900)
            available = bool(
                observation and observation["healthy"] and observation["observed"] >= now - 900
            )
            capabilities.append(
                {
                    "name": label,
                    "category": category,
                    "state": "Available" if available else "Temporarily unavailable",
                    "verified": verified,
                }
            )
        # Coverage gaps are explicit, not claims that these product functions are broken.
        for category, label in [
            ("VOICE", "Voice conversations"),
            ("COMMUNICATION", "Connected email and calendar"),
            ("RESEARCH", "Internet research"),
        ]:
            capabilities.append(
                {
                    "name": label,
                    "category": category,
                    "state": "Temporarily unavailable",
                    "verified": False,
                }
            )
        return {
            "phase": "observe_only",
            "status": status,
            "explanation": "Jarvis is still working normally. Automatic development is stopped."
            if status == "STOPPED"
            else "Observe-only planning; automatic development is not enabled.",
            "current": current,
            "plans": plans,
            "capabilities": capabilities,
            "recent_improvements": [],
            "rollbacks": [],
            "new_capabilities": [],
            "daily_summary": "No autonomous development changes today.",
            "observation_notice": "Some health observations are unavailable. No failure is assumed."
            if self._meta("last_error") not in (None, "[]")
            else "",
            "technical": {
                "guardian_trusted": trusted,
                "phase": 1,
                "development_execution_available": False,
                "observations": len(observations),
                "research_attempts": self.db.execute("SELECT count(*) FROM research").fetchone()[0],
                "last_healthy_cycle": self._meta("last_healthy_cycle"),
                "unavailable_sources": json.loads(self._meta("last_error") or "[]"),
            },
        }

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM observer_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
