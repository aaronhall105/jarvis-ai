#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
from collections import Counter
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - request_patch raises a clear setup error
    OpenAI = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "jarvis_improvement.db"
POLICY_PATH = ROOT / "config" / "self_improvement_policy.json"
ENV_PATH = ROOT / ".env"
WORK_ROOT = ROOT / ".jarvis-improver"
WORKTREES = WORK_ROOT / "worktrees"
ARTIFACTS = WORK_ROOT / "artifacts"
LOCK_PATH = WORK_ROOT / "worker.lock"
VENV_PYTHON = ROOT / ".venv-improver" / "bin" / "python"
AUTHORITATIVE_PRODUCTION_BRANCH = "jarvis/unified-production"


class WorkerError(RuntimeError):
    pass


@dataclass(slots=True)
class WorkerConfig:
    model: str
    poll_seconds: int
    max_attempts_per_day: int
    max_patch_lines: int
    max_changed_files: int
    github_enabled: bool
    ai_review_enabled: bool
    notify_enabled: bool
    notify_service: str
    auto_deploy_low_risk: bool
    proposal_only: bool
    candidate_timeout_seconds: int
    deploy_health_timeout_seconds: int
    base_branch: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_after(
    seconds: int,
) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def timestamp_expired(
    value: str | None,
    *,
    missing_is_expired: bool = True,
) -> bool:
    raw = str(value or "").strip()

    if not raw:
        return missing_is_expired

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return True

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc)


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def env_bool(values: dict[str, str], name: str, default: bool) -> bool:
    raw = values.get(name, os.getenv(name, ""))
    if not raw:
        return default
    return raw.strip().casefold() not in {"0", "false", "no", "off", "disabled"}


def env_int(values: dict[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = values.get(name, os.getenv(name, ""))
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def load_config() -> tuple[WorkerConfig, dict[str, str]]:
    values = load_env(ENV_PATH)
    model = values.get("JARVIS_IMPROVEMENT_MODEL") or values.get("OPENAI_MODEL") or "gpt-5.1-codex"
    return (
        WorkerConfig(
            model=model,
            poll_seconds=env_int(values, "JARVIS_IMPROVEMENT_POLL_SECONDS", 15, 5, 300),
            max_attempts_per_day=env_int(
                values, "JARVIS_IMPROVEMENT_MAX_ATTEMPTS_PER_DAY", 3, 1, 20
            ),
            max_patch_lines=env_int(values, "JARVIS_IMPROVEMENT_MAX_PATCH_LINES", 450, 40, 3000),
            max_changed_files=env_int(values, "JARVIS_IMPROVEMENT_MAX_CHANGED_FILES", 5, 1, 20),
            github_enabled=env_bool(values, "JARVIS_IMPROVEMENT_GITHUB_ENABLED", False),
            ai_review_enabled=env_bool(values, "JARVIS_IMPROVEMENT_AI_REVIEW_ENABLED", True),
            notify_enabled=env_bool(values, "JARVIS_IMPROVEMENT_NOTIFY_ENABLED", True),
            notify_service=values.get(
                "JARVIS_IMPROVEMENT_NOTIFY_SERVICE", "notify.mobile_app_aaron_s_phone"
            ).strip(),
            auto_deploy_low_risk=env_bool(values, "JARVIS_IMPROVEMENT_AUTO_DEPLOY_LOW_RISK", False),
            proposal_only=env_bool(
                values,
                "JARVIS_IMPROVEMENT_PROPOSAL_ONLY",
                True,
            ),
            candidate_timeout_seconds=env_int(
                values, "JARVIS_IMPROVEMENT_CANDIDATE_TIMEOUT_SECONDS", 600, 60, 3600
            ),
            deploy_health_timeout_seconds=env_int(
                values, "JARVIS_IMPROVEMENT_DEPLOY_HEALTH_TIMEOUT_SECONDS", 90, 20, 600
            ),
            base_branch=values.get(
                "JARVIS_IMPROVEMENT_BASE_BRANCH",
                AUTHORITATIVE_PRODUCTION_BRANCH,
            ).strip()
            or AUTHORITATIVE_PRODUCTION_BRANCH,
        ),
        values,
    )


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


TRANSACTION_COLUMNS: dict[str, str] = {
    "approval_code_expires_at": "TEXT",
    "deploy_ticket_hash": "TEXT",
    "deploy_ticket_salt": "TEXT",
    "deploy_ticket_expires_at": "TEXT",
    "deploy_ticket_consumed_at": "TEXT",
    "rollback_ticket_hash": "TEXT",
    "rollback_ticket_salt": "TEXT",
    "rollback_ticket_expires_at": "TEXT",
    "rollback_ticket_consumed_at": "TEXT",
    "base_commit": "TEXT",
    "candidate_commit": "TEXT",
    "validated_patch_sha256": "TEXT",
    "deploy_lease_id": "TEXT",
    "deploy_lease_started_at": "TEXT",
    "deploy_lease_expires_at": "TEXT",
    "deploy_phase": "TEXT",
}


def ensure_candidate_transaction_columns() -> None:
    """
    Keep the host worker compatible with the transactional
    approval schema even before Jarvis Core is restarted.
    """

    with connect() as connection:
        existing = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(improvement_candidates)").fetchall()
        }

        if not existing:
            raise WorkerError("improvement_candidates table is missing.")

        for (
            name,
            data_type,
        ) in TRANSACTION_COLUMNS.items():
            if name in existing:
                continue

            connection.execute(f"ALTER TABLE improvement_candidates ADD COLUMN {name} {data_type}")


def json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def update_setting(key: str, value: str) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO improvement_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, utc_now()),
        )


def setting(key: str, default: str = "") -> str:
    with connect() as connection:
        row = connection.execute(
            "SELECT value FROM improvement_settings WHERE key = ?",
            (key,),
        ).fetchone()
    return str(row["value"]) if row else default


def improvement_enabled() -> bool:
    if (DATA_DIR / "self_improvement.disabled").exists():
        return False
    return setting("enabled", "true").casefold() not in {"0", "false", "no", "off", "disabled"}


def audit(
    event_type: str,
    *,
    actor: str = "worker",
    failure_id: int | None = None,
    candidate_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO improvement_audit (
                created_at, event_type, actor, failure_id,
                candidate_id, details_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (utc_now(), event_type, actor, failure_id, candidate_id, json_dump(details or {})),
        )


def plain_failure_explanation(error: str) -> tuple[str, str]:
    text = str(error or "").casefold()

    if "unified diff" in text or ("patch" in text and ("invalid" in text or "malformed" in text)):
        return (
            "Creating the code change",
            "Jarvis created the change, but the code patch was formatted "
            "incorrectly, so it could not be safely applied.",
        )

    if "pytest" in text or "test failed" in text or "tests failed" in text:
        return (
            "Automated testing",
            "Jarvis created the change, but one or more automated tests found a problem.",
        )

    if "security" in text or "bandit" in text or "pip-audit" in text:
        return (
            "Security checks",
            "Jarvis created the change, but a security check blocked it.",
        )

    if "forbidden" in text or "policy" in text:
        return (
            "Safety checks",
            "Jarvis created a change that did not meet the safety rules, so it was blocked.",
        )

    if "docker" in text or "build failed" in text:
        return (
            "Building the change",
            "Jarvis created the change, but it could not be built successfully.",
        )

    if "timeout" in text or "timed out" in text:
        return (
            "Processing the improvement",
            "Jarvis ran out of time while preparing or testing the change.",
        )

    return (
        "Automated checks",
        "Jarvis could not complete this improvement because one of its automated checks failed.",
    )


def notify_aaron(
    message: str,
    *,
    title: str,
    config: WorkerConfig,
    env_values: dict[str, str],
) -> bool:
    if not config.notify_enabled or not config.notify_service.startswith("notify."):
        return False
    base_url = (env_values.get("HOME_ASSISTANT_URL") or "").rstrip("/")
    token = env_values.get("HOME_ASSISTANT_TOKEN") or ""
    if not base_url or not token:
        return False
    try:
        domain, service = config.notify_service.split(".", 1)
        payload = json.dumps({"title": title, "message": message}).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}/api/services/{domain}/{service}",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    timeout: int = 300,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=env,
        check=False,
    )
    if check and completed.returncode != 0:
        raise WorkerError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout[-8000:]}"
        )
    return completed


def ensure_repo() -> None:
    if not (ROOT / ".git").exists():
        raise WorkerError(f"{ROOT} is not a Git repository.")
    status = run(["git", "status", "--porcelain"], check=True).stdout.strip()
    if status:
        raise WorkerError(
            "The live Jarvis repository has uncommitted changes. Commit or stash them before self-improvement runs.\n"
            + status[:4000]
        )


def load_policy() -> dict[str, Any]:
    if not POLICY_PATH.exists():
        raise WorkerError(f"Missing security policy: {POLICY_PATH}")
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise WorkerError("The self-improvement policy must be a JSON object.")
    return policy


def path_matches(path: str, patterns: Iterable[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, pattern) for pattern in patterns)


def fetch_candidate(statuses: tuple[str, ...]) -> dict[str, Any] | None:
    placeholders = ",".join("?" for _ in statuses)
    with connect() as connection:
        row = connection.execute(
            f"""
            SELECT * FROM improvement_candidates
            WHERE status IN ({placeholders})
            ORDER BY candidate_id ASC LIMIT 1
            """,
            statuses,
        ).fetchone()
    return dict(row) if row else None


def fetch_candidate_by_id(
    candidate_id: int,
) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM improvement_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

    return dict(row) if row else None


PREMERGE_DEPLOY_PHASES = frozenset(
    {
        "claimed",
        "premerge_verified",
        "merging",
    }
)

POSTMERGE_DEPLOY_PHASES = frozenset(
    {
        "merged",
        "rebuilding",
        "verifying",
    }
)

ROLLBACK_DEPLOY_PHASES = frozenset(
    {
        "rollback_started",
        "rollback_rebuilding",
        "rollback_verifying",
        "recovery_rolling_back",
        "recovery_rebuilding",
        "recovery_verifying",
    }
)

KNOWN_DEPLOY_PHASES = PREMERGE_DEPLOY_PHASES | POSTMERGE_DEPLOY_PHASES | ROLLBACK_DEPLOY_PHASES


MANUAL_ROLLBACK_ACTIVE_PHASES = frozenset(
    {
        "manual_rollback_claimed",
        "manual_rollback_rebuilding",
        "manual_rollback_verifying",
    }
)


def fetch_manual_rollback_candidate() -> dict[str, Any] | None:
    """
    Return interrupted manual rollback work before accepting
    a new rollback request.

    A claimed rollback is durable work: process restart must
    resume it instead of leaving status='rolling_back' orphaned.
    """

    placeholders = ",".join("?" for _ in MANUAL_ROLLBACK_ACTIVE_PHASES)

    with connect() as connection:
        row = connection.execute(
            f"""
            SELECT *
            FROM improvement_candidates
            WHERE status = 'rollback_requested'
               OR (
                    status = 'rolling_back'
                    AND deploy_phase IN ({placeholders})
               )
            ORDER BY
                CASE
                    WHEN status = 'rolling_back' THEN 0
                    ELSE 1
                END,
                candidate_id ASC
            LIMIT 1
            """,
            tuple(MANUAL_ROLLBACK_ACTIVE_PHASES),
        ).fetchone()

    return dict(row) if row else None


def deployment_lease_is_expired(
    candidate: dict[str, Any],
) -> bool:
    return timestamp_expired(
        candidate.get("deploy_lease_expires_at"),
        missing_is_expired=True,
    )


def claim_deployment(
    candidate_id: int,
    *,
    lease_seconds: int = 15 * 60,
) -> dict[str, Any]:
    ensure_candidate_transaction_columns()

    now = utc_now()
    expires_at = utc_after(lease_seconds)
    lease_id = secrets.token_hex(16)

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")

        row = connection.execute(
            """
            SELECT *
            FROM improvement_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

        if row is None:
            raise WorkerError(f"Candidate {candidate_id} was not found.")

        candidate = dict(row)

        if str(candidate.get("status") or "") != "deploy_requested":
            raise WorkerError("Candidate is not available for deployment claiming.")

        cursor = connection.execute(
            """
            UPDATE improvement_candidates
            SET
                status = 'deploying',
                updated_at = ?,
                deploy_lease_id = ?,
                deploy_lease_started_at = ?,
                deploy_lease_expires_at = ?,
                deploy_phase = 'claimed',
                rollback_ref = COALESCE(
                    rollback_ref,
                    base_commit
                )
            WHERE candidate_id = ?
              AND status = 'deploy_requested'
            """,
            (
                now,
                lease_id,
                now,
                expires_at,
                candidate_id,
            ),
        )

        if cursor.rowcount != 1:
            raise WorkerError("Deployment claim lost a database race.")

        claimed = connection.execute(
            """
            SELECT *
            FROM improvement_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

        if claimed is None:
            raise WorkerError("Claimed candidate disappeared.")

        return dict(claimed)


def update_deployment_phase(
    candidate_id: int,
    lease_id: str,
    phase: str,
    *,
    lease_seconds: int = 15 * 60,
) -> None:
    if not lease_id:
        raise WorkerError("Deployment lease ID is missing.")

    now = utc_now()
    expires_at = utc_after(lease_seconds)

    with connect() as connection:
        cursor = connection.execute(
            """
            UPDATE improvement_candidates
            SET
                updated_at = ?,
                deploy_phase = ?,
                deploy_lease_expires_at = ?
            WHERE candidate_id = ?
              AND status = 'deploying'
              AND deploy_lease_id = ?
            """,
            (
                now,
                phase,
                expires_at,
                candidate_id,
                lease_id,
            ),
        )

        if cursor.rowcount != 1:
            raise WorkerError("Deployment lease was lost.")


def deployment_lease_owned(
    candidate_id: int,
    lease_id: str,
) -> bool:
    if not lease_id:
        return False

    with connect() as connection:
        row = connection.execute(
            """
            SELECT
                status,
                deploy_lease_id
            FROM improvement_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

    if row is None:
        return False

    stored = str(row["deploy_lease_id"] or "")

    return (
        str(row["status"] or "") == "deploying"
        and bool(stored)
        and secrets.compare_digest(
            stored,
            lease_id,
        )
    )


def transition_deployment_state(
    candidate_id: int,
    lease_id: str,
    *,
    status: str,
    phase: str,
    error: str | None = None,
    deployed_at: str | None = None,
    rolled_back_at: str | None = None,
) -> None:
    if not lease_id:
        raise WorkerError("Deployment lease ID is missing.")

    now = utc_now()

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")

        cursor = connection.execute(
            """
            UPDATE improvement_candidates
            SET
                status = ?,
                updated_at = ?,
                deploy_phase = ?,
                deploy_lease_id = NULL,
                deploy_lease_started_at = NULL,
                deploy_lease_expires_at = NULL,
                error = ?,
                deployed_at = COALESCE(
                    ?,
                    deployed_at
                ),
                rolled_back_at = COALESCE(
                    ?,
                    rolled_back_at
                )
            WHERE candidate_id = ?
              AND status = 'deploying'
              AND deploy_lease_id = ?
            """,
            (
                status,
                now,
                phase,
                error,
                deployed_at,
                rolled_back_at,
                candidate_id,
                lease_id,
            ),
        )

        if cursor.rowcount != 1:
            raise WorkerError("Deployment lease was lost before the state transition completed.")


def claim_stale_deployment_recovery(
    candidate_id: int,
    *,
    lease_seconds: int = 15 * 60,
) -> dict[str, Any] | None:
    ensure_candidate_transaction_columns()

    now = utc_now()
    expires_at = utc_after(lease_seconds)
    lease_id = secrets.token_hex(16)

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")

        row = connection.execute(
            """
            SELECT *
            FROM improvement_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

        if row is None:
            return None

        candidate = dict(row)

        if str(candidate.get("status") or "") != "deploying":
            return None

        if not deployment_lease_is_expired(candidate):
            return None

        cursor = connection.execute(
            """
            UPDATE improvement_candidates
            SET
                updated_at = ?,
                deploy_lease_id = ?,
                deploy_lease_started_at = ?,
                deploy_lease_expires_at = ?
            WHERE candidate_id = ?
              AND status = 'deploying'
            """,
            (
                now,
                lease_id,
                now,
                expires_at,
                candidate_id,
            ),
        )

        if cursor.rowcount != 1:
            return None

        claimed = connection.execute(
            """
            SELECT *
            FROM improvement_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

        return dict(claimed) if claimed else None


def fetch_failure(failure_id: int) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM improvement_failures WHERE failure_id = ?",
            (failure_id,),
        ).fetchone()
    if not row:
        raise WorkerError(f"Failure {failure_id} was not found.")
    item = dict(row)
    item["evidence"] = json_load(item.pop("evidence_json", "{}"), {})
    return item


def update_candidate(candidate_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields.setdefault("updated_at", utc_now())
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = [
        json_dump(value) if key.endswith("_json") and not isinstance(value, str) else value
        for key, value in fields.items()
    ]
    values.append(candidate_id)
    with connect() as connection:
        connection.execute(
            f"UPDATE improvement_candidates SET {assignments} WHERE candidate_id = ?",
            tuple(values),
        )


def update_failure(failure_id: int, **fields: Any) -> None:
    if not fields:
        return
    fields.setdefault("updated_at", utc_now())
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [failure_id]
    with connect() as connection:
        connection.execute(
            f"UPDATE improvement_failures SET {assignments} WHERE failure_id = ?",
            tuple(values),
        )


def attempts_today() -> int:
    """Count only autonomous generation attempts started today."""
    today = datetime.now(timezone.utc).date().isoformat()

    with connect() as connection:
        rows = connection.execute(
            """
            SELECT details_json
            FROM improvement_audit
            WHERE event_type = 'candidate_generation_started'
              AND substr(created_at, 1, 10) = ?
            """,
            (today,),
        ).fetchall()

    count = 0

    for row in rows:
        details = json_load(row["details_json"], {})
        if bool(details.get("manual_request")):
            continue
        count += 1

    return count


def uses_autonomous_attempt_quota(
    failure: dict[str, Any],
) -> bool:
    return str(failure.get("category") or "").casefold() != "requested_improvement"


def infer_context_files(
    failure: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    category = str(failure.get("category") or "general")

    category_map = policy.get(
        "context_files_by_category",
        {},
    )

    keyword_map = policy.get(
        "context_files_by_keyword",
        {},
    )

    allowed = policy.get(
        "allowed_context_paths",
        [
            "bridge/app/*.py",
            "bridge/tests/*.py",
        ],
    )

    max_files = int(
        policy.get(
            "max_context_files",
            6,
        )
    )

    evidence = failure.get(
        "evidence",
        {},
    )

    source = " ".join(
        (
            str(failure.get("summary") or ""),
            category,
            json.dumps(
                evidence,
                ensure_ascii=False,
                default=str,
            ),
        )
    ).casefold()

    selected: list[str] = []

    for raw_keyword, values in keyword_map.items():
        keyword = str(raw_keyword or "").strip().casefold()

        if not keyword or keyword not in source:
            continue

        if isinstance(
            values,
            str,
        ):
            selected.append(values)

        elif isinstance(
            values,
            list,
        ):
            selected.extend(str(value) for value in values)

    selected.extend(category_map.get(category) or category_map.get("general") or [])

    result: list[str] = []
    seen: set[str] = set()

    for value in selected:
        path = str(value)

        if (
            path in seen
            or not path_matches(
                path,
                allowed,
            )
            or not (ROOT / path).is_file()
        ):
            continue

        seen.add(path)
        result.append(path)

        if len(result) >= max_files:
            break

    return result


def redact(text: str) -> str:
    patterns = (
        re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
        re.compile(r"(?i)((?:api[_ -]?key|token|password|secret|pin)\s*[:=]\s*)\S+"),
        re.compile(r"(?i)(https?://[^\s:@/]+:)[^@\s/]+@"),
        re.compile(r"\b[A-Za-z0-9_-]{32,}\b"),
    )
    output = text
    for pattern in patterns:
        if pattern.groups:
            output = pattern.sub(r"\1[REDACTED]", output)
        else:
            output = pattern.sub("[REDACTED]", output)
    return output


def _context_search_terms(
    failure: dict[str, Any],
) -> list[str]:
    evidence = failure.get(
        "evidence",
        {},
    )

    source = " ".join(
        (
            str(failure.get("summary") or ""),
            str(failure.get("category") or ""),
            json.dumps(
                evidence,
                ensure_ascii=False,
                default=str,
            ),
        )
    )

    source = redact(source).casefold()

    stop = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "were",
        "your",
        "into",
        "true",
        "false",
        "none",
    }

    terms: list[str] = []

    for token in re.findall(
        r"[a-z_][a-z0-9_]{3,}",
        source,
    ):
        if token in stop or token in terms:
            continue

        terms.append(token)

    if "failed" in source or '"success": false' in source:
        for token in (
            "failed_tool",
            "failure_like",
            "record_failure",
            "completed_calls",
            "record_result",
        ):
            if token not in terms:
                terms.append(token)

    return terms[:48]


def _relevant_context_excerpt(
    content: str,
    terms: list[str],
    budget: int,
) -> str:
    if len(content) <= budget:
        return content

    lines = content.splitlines()
    count = len(lines)

    def render(
        ranges: list[tuple[int, int]],
    ) -> str:
        merged: list[list[int]] = []

        for left, right in sorted(ranges):
            if merged and left <= merged[-1][1]:
                merged[-1][1] = max(
                    merged[-1][1],
                    right,
                )
            else:
                merged.append([left, right])

        parts: list[str] = []

        for left, right in merged:
            parts.append(f"===== SOURCE LINES {left + 1}-{right} OF {count} =====")
            parts.extend(lines[left:right])

        return "\n".join(parts) + "\n"

    ranges = [
        (0, min(30, count)),
        (max(0, count - 30), count),
    ]

    ranked: list[tuple[int, int]] = []

    for index, line in enumerate(lines):
        folded = line.casefold()

        hits = {term for term in terms if term in folded}

        if not hits:
            continue

        score = sum(4 if "_" in term else 2 if len(term) >= 8 else 1 for term in hits)

        ranked.append((-score, index))

    for _, index in sorted(ranked):
        if any(left <= index < right for left, right in ranges):
            continue

        trial = ranges + [
            (
                max(0, index - 24),
                min(count, index + 25),
            )
        ]

        candidate = render(trial)

        if len(candidate) > budget:
            continue

        ranges = trial

    excerpt = render(ranges)

    return excerpt[:budget]


def _apply_failure_source_feedback(
    error: str,
    base_commit: str,
    *,
    radius: int = 20,
) -> str:
    """Return exact base-commit source around rejected patch hunks."""
    matches = re.findall(
        r"patch failed: ([^:\n]+):(\d+)",
        error,
    )

    if not matches:
        return ""

    sections: list[str] = []
    seen: set[tuple[str, int]] = set()

    for path, raw_line in matches:
        line_no = int(raw_line)
        key = (path, line_no)

        if key in seen:
            continue

        seen.add(key)

        result = run(
            [
                "git",
                "show",
                f"{base_commit}:{path}",
            ],
            cwd=ROOT,
            check=False,
        )

        if result.returncode != 0:
            continue

        lines = result.stdout.splitlines()

        if not lines:
            continue

        start = max(
            1,
            line_no - radius,
        )
        end = min(
            len(lines),
            line_no + radius,
        )

        body = "\n".join(
            f"{index:6d}  {lines[index - 1]}"
            for index in range(
                start,
                end + 1,
            )
        )

        sections.append(f"===== EXACT BASE SOURCE: {path}:{start}-{end} =====\n{body}")

    if not sections:
        return ""

    return (
        "\n\nExact source around Git's rejected "
        "hunk from the captured base commit:\n" + "\n\n".join(sections)
    )


def build_context(
    failure: dict[str, Any],
    policy: dict[str, Any],
    *,
    base_commit: str | None = None,
) -> tuple[str, list[str]]:
    files = infer_context_files(
        failure,
        policy,
    )

    max_chars = int(
        policy.get(
            "max_context_characters",
            180000,
        )
    )

    if not files:
        return "", []

    terms = _context_search_terms(failure)

    per_file = max(
        4000,
        (max_chars // len(files)) - 256,
    )

    sections: list[str] = []
    included: list[str] = []

    for path in files:
        if base_commit:
            content = run(
                [
                    "git",
                    "show",
                    f"{base_commit}:{path}",
                ],
                cwd=ROOT,
            ).stdout
        else:
            content = (ROOT / path).read_text(
                encoding="utf-8",
                errors="replace",
            )

        content = redact(content)

        excerpt = _relevant_context_excerpt(
            content,
            terms,
            per_file,
        )

        section = f"\n===== FILE: {path} =====\n{excerpt}"

        if sum(len(item) for item in sections) + len(section) > max_chars:
            continue

        sections.append(section)
        included.append(path)

    context = "".join(sections)

    if len(context) > max_chars:
        raise WorkerError("Improvement context exceeded the configured character limit.")

    return context, included


def parse_tool_arguments(response: Any, tool_name: str) -> dict[str, Any]:
    for item in getattr(response, "output", []) or []:
        if (
            str(getattr(item, "type", "")) == "function_call"
            and str(getattr(item, "name", "")) == tool_name
        ):
            return json.loads(getattr(item, "arguments", "{}"))
    text = str(getattr(response, "output_text", "") or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    return json.loads(text)


def request_independent_review(
    *,
    failure: dict[str, Any],
    workspace: Path,
    tests: dict[str, Any],
    security: dict[str, Any],
    config: WorkerConfig,
    env_values: dict[str, str],
) -> dict[str, Any]:
    if not config.ai_review_enabled:
        return {"enabled": False, "verdict": "approve", "findings": []}
    api_key = env_values.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise WorkerError("OPENAI_API_KEY is unavailable for independent review.")
    if OpenAI is None:
        raise WorkerError("The OpenAI package is missing from the worker environment.")
    diff = run(["git", "diff", "--no-ext-diff", "--unified=3"], cwd=workspace).stdout
    evidence = redact(
        json.dumps(failure.get("evidence", {}), ensure_ascii=False, indent=2, default=str)
    )
    prompt = f"""
Review this proposed Jarvis Core patch independently. The generator and reviewer are separate passes. Reject the patch if it could operate the wrong Home Assistant device, weaken identity/Admin Mode controls, invent state, leak secrets, add unsafe execution, omit a meaningful regression test, or fail to address the recorded failure.

Failure:
{evidence}

Patch:
{redact(diff)}

Local validation summary:
{json.dumps({"tests": tests, "security": security}, ensure_ascii=False, default=str)[:30000]}
""".strip()
    tool = {
        "type": "function",
        "name": "submit_review",
        "description": "Submit an independent safety and correctness review.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["approve", "reject"]},
                "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": {"type": "string"}},
                "required_changes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "risk", "summary", "findings", "required_changes"],
            "additionalProperties": False,
        },
    }
    client = OpenAI(api_key=api_key, timeout=180, max_retries=2)
    kwargs: dict[str, Any] = {
        "model": config.model,
        "instructions": "Act as a sceptical senior reviewer. Do not rewrite the patch; only review it through the submit_review tool.",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "tools": [tool],
        "tool_choice": {"type": "function", "name": "submit_review"},
        "parallel_tool_calls": False,
        "store": False,
        "max_output_tokens": 16000,
    }
    if config.model.lower().startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": "high"}
        kwargs["text"] = {"verbosity": "low"}

    response = client.responses.create(**kwargs)

    review_usage = _require_completed_response(
        response,
        purpose="Independent review",
    )

    review = parse_tool_arguments(
        response,
        "submit_review",
    )

    review["enabled"] = True

    review["response_id"] = review_usage["response_id"]

    review["response_status"] = review_usage["response_status"]

    review["output_tokens"] = review_usage["output_tokens"]

    review["reasoning_tokens"] = review_usage["reasoning_tokens"]

    return review


def parse_patch_arguments(response: Any) -> dict[str, Any]:
    return parse_tool_arguments(response, "submit_patch")


def _response_completion_metadata(
    response: Any,
) -> dict[str, Any]:
    status = str(
        getattr(
            response,
            "status",
            "",
        )
        or ""
    )

    incomplete = getattr(
        response,
        "incomplete_details",
        None,
    )

    if isinstance(
        incomplete,
        dict,
    ):
        incomplete_reason = str(incomplete.get("reason") or "")
    else:
        incomplete_reason = str(
            getattr(
                incomplete,
                "reason",
                "",
            )
            or ""
        )

    usage = getattr(
        response,
        "usage",
        None,
    )

    output_details = getattr(
        usage,
        "output_tokens_details",
        None,
    )

    return {
        "response_status": status,
        "incomplete_reason": incomplete_reason,
        "input_tokens": int(
            getattr(
                usage,
                "input_tokens",
                0,
            )
            or 0
        ),
        "output_tokens": int(
            getattr(
                usage,
                "output_tokens",
                0,
            )
            or 0
        ),
        "reasoning_tokens": int(
            getattr(
                output_details,
                "reasoning_tokens",
                0,
            )
            or 0
        ),
        "response_id": str(
            getattr(
                response,
                "id",
                "",
            )
            or ""
        ),
    }


def _require_completed_response(
    response: Any,
    *,
    purpose: str,
) -> dict[str, Any]:
    metadata = _response_completion_metadata(response)

    if metadata["response_status"] != "completed":
        raise WorkerError(
            f"{purpose} response was not completed: "
            f"status={metadata['response_status'] or 'unknown'} "
            f"incomplete_reason="
            f"{metadata['incomplete_reason'] or 'unknown'} "
            f"output_tokens={metadata['output_tokens']} "
            f"reasoning_tokens={metadata['reasoning_tokens']} "
            f"response_id={metadata['response_id'] or 'unknown'}"
        )

    return metadata


def request_patch(
    *,
    failure: dict[str, Any],
    context: str,
    context_files: list[str],
    policy: dict[str, Any],
    config: WorkerConfig,
    env_values: dict[str, str],
    previous_error: str | None = None,
    previous_patch: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = env_values.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise WorkerError("OPENAI_API_KEY is not available to the improvement worker.")

    evidence = failure.get("evidence", {})
    failure_json = redact(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
    allowed_edit_paths = policy.get("allowed_edit_paths", [])
    forbidden_paths = policy.get("forbidden_paths", [])
    prompt = f"""
You are repairing Jarvis Core, a production Home Assistant AI assistant.
Generate the smallest safe set of exact structured source edits that fixes the recorded failure and adds a regression test.
Failure ID: {failure["failure_id"]}
Category: {failure.get("category")}
Severity: {failure.get("severity")}
Occurrences: {failure.get("occurrences")}
Summary: {failure.get("summary")}
Redacted evidence:
{failure_json}
Files supplied for context: {context_files}
Allowed edit path patterns: {allowed_edit_paths}
Forbidden paths: {forbidden_paths}
Authoritative repository source context:
The source below was read from the exact Git base commit that the
candidate will be applied to. Generate structured edits against this
source, not against remembered or assumed versions of Jarvis.
{context}
Requirements:
- Preserve all existing working Jarvis features.
- Never add credentials, tokens, network backdoors, shell execution, eval/exec, or Docker socket access.
- Never edit .env, data, logs, authentication, systemd, Docker daemon settings, or Home Assistant tokens.
- Never weaken room/device safety, Admin Mode confirmation, user identity separation, or tool verification.
- Add or update a focused pytest regression test.
- Keep the resulting Git patch under {config.max_patch_lines} changed lines and {config.max_changed_files} files.
- Return structured edits through submit_patch.
- Each edit must contain one repository-relative path, exact old_text copied from the authoritative source, and replacement new_text.
- old_text must be non-empty and must identify exactly one occurrence in the target file.
- Never invent line numbers, Git hunk coordinates, diff headers, or unchanged diff context. The worker generates the Git diff deterministically.
- Every structured edit must be COMPLETE. Never submit truncated old_text/new_text, unfinished expressions, or unbalanced parentheses/brackets.
- Before calling submit_patch, verify every old_text value was copied exactly from the authoritative source, including whitespace and blank lines.
- Before calling submit_patch, re-check that every Python new_text replacement is syntactically complete.
- Prefer fewer, smaller exact edits over broad replacements.
""".strip()

    if previous_error or previous_patch:
        prompt += (
            "\n\nA previous structured-edit attempt failed. "
            "Treat any previous generated patch only as diagnostic "
            "evidence, never as authoritative source."
        )

        if previous_patch:
            prompt += (
                "\n\nPrevious failed patch "
                "(REFERENCE ONLY — DO NOT COPY ITS CONTEXT):\n" + redact(previous_patch)[:24000]
            )

        prompt += (
            "\n\nDiscard any incorrect remembered source from the "
            "previous attempt. Re-read the authoritative repository "
            "source supplied above and generate fresh structured "
            "edits. Every old_text value must be copied exactly from "
            "that source, including whitespace and blank lines."
        )

        if previous_error:
            retry_error = redact(previous_error)[-12000:]

            if "===== EXACT BASE SOURCE:" in retry_error:
                prompt += (
                    "\n\nAPPLY-FAILURE REPAIR RULES:\n"
                    "- EXACT BASE SOURCE is authoritative "
                    "for every rejected edit.\n"
                    "- If it conflicts with the previous "
                    "failed patch or any remembered source, "
                    "EXACT BASE SOURCE wins.\n"
                    "- Rebuild each rejected structured edit "
                    "from scratch. Do not recycle stale "
                    "source text.\n"
                    "- Copy each old_text value "
                    "character-for-character from "
                    "EXACT BASE SOURCE.\n"
                    "- Source line numbers shown in EXACT "
                    "BASE SOURCE are annotations only and "
                    "must not appear in old_text.\n"
                    "- Before submit_patch, verify every "
                    "old_text value exists exactly once in "
                    "the authoritative source.\n"
                    "- Prefer a smaller exact old_text "
                    "anchor over a broad replacement."
                )

            prompt += "\n\nFailure feedback (AUTHORITATIVE FOR THIS RETRY):\n" + retry_error

    instructions = """
Act as a conservative senior Python engineer and safety reviewer. You may only submit bounded structured source edits through the submit_patch tool. Prefer deterministic fixes over prompt-only changes. A candidate must include a regression test and must not modify forbidden paths. Never generate Git diff syntax; the local worker will materialise the edits and generate the Git patch. Do not claim tests passed; the local worker will verify them.
""".strip()

    tool = {
        "type": "function",
        "name": "submit_patch",
        "description": (
            "Submit bounded exact Jarvis source edits for "
            "deterministic local materialisation and validation."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "root_cause": {"type": "string"},
                "risk": {
                    "type": "string",
                    "enum": [
                        "low",
                        "medium",
                        "high",
                    ],
                },
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                            },
                            "old_text": {
                                "type": "string",
                            },
                            "new_text": {
                                "type": "string",
                            },
                        },
                        "required": [
                            "path",
                            "old_text",
                            "new_text",
                        ],
                        "additionalProperties": False,
                    },
                },
                "tests_added": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
                "notes": {
                    "type": "array",
                    "items": {
                        "type": "string",
                    },
                },
            },
            "required": [
                "summary",
                "root_cause",
                "risk",
                "edits",
                "tests_added",
                "notes",
            ],
            "additionalProperties": False,
        },
    }

    if OpenAI is None:
        raise WorkerError(
            "The improvement worker virtual environment is missing the OpenAI package. "
            "Run tools/install_self_improvement_v14.sh again."
        )
    client = OpenAI(api_key=api_key, timeout=180, max_retries=2)
    kwargs: dict[str, Any] = {
        "model": config.model,
        "instructions": instructions,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "tools": [tool],
        "tool_choice": {"type": "function", "name": "submit_patch"},
        "parallel_tool_calls": False,
        "store": False,
        "max_output_tokens": 32000,
    }
    if config.model.lower().startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": "medium"}
        kwargs["text"] = {"verbosity": "medium"}

    response = client.responses.create(**kwargs)

    usage = _require_completed_response(
        response,
        purpose="Patch generation",
    )

    payload = parse_patch_arguments(response)

    return payload, usage


def patch_paths(patch: str) -> list[str]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("+++ "):
            continue
        value = line[4:].strip().split("\t", 1)[0]
        if value == "/dev/null":
            continue
        if value.startswith("b/"):
            value = value[2:]
        paths.append(value)
    return sorted(set(paths))


def patch_line_count(patch: str) -> int:
    return sum(
        1
        for line in patch.splitlines()
        if (line.startswith("+") and not line.startswith("+++"))
        or (line.startswith("-") and not line.startswith("---"))
    )


def validate_patch_policy(
    patch: str, policy: dict[str, Any], config: WorkerConfig
) -> tuple[list[str], str]:
    paths = patch_paths(patch)
    if not paths:
        raise WorkerError("The model did not return a valid unified diff.")
    if len(paths) > config.max_changed_files:
        raise WorkerError(
            f"Patch changes {len(paths)} files; policy allows {config.max_changed_files}."
        )
    lines = patch_line_count(patch)
    if lines > config.max_patch_lines:
        raise WorkerError(f"Patch changes {lines} lines; policy allows {config.max_patch_lines}.")

    allowed = policy.get("allowed_edit_paths", [])
    forbidden = policy.get("forbidden_paths", [])
    for path in paths:
        if path.startswith("/") or ".." in Path(path).parts:
            raise WorkerError(f"Unsafe patch path: {path}")
        if path_matches(path, forbidden):
            raise WorkerError(f"Patch touches forbidden path: {path}")
        if not path_matches(path, allowed):
            raise WorkerError(f"Patch touches path outside the allow-list: {path}")

    forbidden_added_patterns = [
        re.compile(pattern, re.I) for pattern in policy.get("forbidden_added_patterns", [])
    ]
    additions = "\n".join(
        line[1:]
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    for pattern in forbidden_added_patterns:
        if pattern.search(additions):
            raise WorkerError(f"Patch adds a forbidden construct matching: {pattern.pattern}")
    return paths, hashlib.sha256(patch.encode("utf-8")).hexdigest()


def create_worktree(
    candidate_id: int,
    branch_name: str,
    base_ref: str = "HEAD",
) -> Path:
    workspace = WORKTREES / str(candidate_id)

    if workspace.exists():
        run(
            [
                "git",
                "worktree",
                "remove",
                "--force",
                str(workspace),
            ],
            check=False,
        )

        shutil.rmtree(
            workspace,
            ignore_errors=True,
        )

    run(
        [
            "git",
            "branch",
            "-D",
            branch_name,
        ],
        check=False,
    )

    WORKTREES.mkdir(
        parents=True,
        exist_ok=True,
    )

    run(
        [
            "git",
            "worktree",
            "add",
            "-b",
            branch_name,
            str(workspace),
            base_ref,
        ]
    )

    return workspace


def _validate_structured_edit_path(
    path: str,
    policy: dict[str, Any],
) -> str:
    if not path or path.startswith("/") or ".." in Path(path).parts:
        raise WorkerError(f"Unsafe structured edit path: {path}")

    forbidden = policy.get(
        "forbidden_paths",
        [],
    )
    allowed = policy.get(
        "allowed_edit_paths",
        [],
    )

    if path_matches(path, forbidden):
        raise WorkerError(f"Structured edit touches forbidden path: {path}")

    if not path_matches(path, allowed):
        raise WorkerError(f"Structured edit touches path outside the allow-list: {path}")

    return path


def materialise_structured_edits(
    workspace: Path,
    payload: dict[str, Any],
    policy: dict[str, Any],
    config: WorkerConfig,
) -> tuple[str, list[str], str]:
    edits = payload.get("edits")

    if not isinstance(edits, list) or not edits:
        raise WorkerError("The model did not return any structured edits.")

    if len(edits) > config.max_patch_lines:
        raise WorkerError("Structured edit count exceeds the candidate patch limit.")

    grouped: dict[
        str,
        list[tuple[str, str]],
    ] = {}

    for index, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict):
            raise WorkerError(f"Structured edit {index} is not an object.")

        path_value = edit.get("path")
        old_text = edit.get("old_text")
        new_text = edit.get("new_text")

        if not isinstance(path_value, str):
            raise WorkerError(f"Structured edit {index} has an invalid path.")

        path = _validate_structured_edit_path(
            path_value,
            policy,
        )

        if not isinstance(old_text, str):
            raise WorkerError(f"Structured edit {index} has invalid old_text.")

        if not isinstance(new_text, str):
            raise WorkerError(f"Structured edit {index} has invalid new_text.")

        if not old_text:
            raise WorkerError("Structured edit old_text must not be empty.")

        if old_text == new_text:
            raise WorkerError("Structured edit would make no change.")

        grouped.setdefault(
            path,
            [],
        ).append(
            (
                old_text,
                new_text,
            )
        )

    if len(grouped) > config.max_changed_files:
        raise WorkerError(
            "Structured edits change "
            f"{len(grouped)} files; policy allows "
            f"{config.max_changed_files}."
        )

    workspace_root = workspace.resolve()

    for path, replacements in grouped.items():
        target = workspace / path

        if target.is_symlink():
            raise WorkerError(f"Structured edits may not modify symlinks: {path}")

        if not target.exists() or not target.is_file():
            raise WorkerError(f"Structured edit target is not an existing regular file: {path}")

        resolved = target.resolve()

        if not resolved.is_relative_to(workspace_root):
            raise WorkerError(f"Structured edit escaped the candidate workspace: {path}")

        original = target.read_text(
            encoding="utf-8",
        )

        spans: list[tuple[int, int, str]] = []

        for old_text, new_text in replacements:
            occurrences = original.count(old_text)

            if occurrences != 1:
                raise WorkerError(
                    "Structured edit old_text for "
                    f"{path} occurs {occurrences} "
                    "times; exactly one occurrence "
                    "is required."
                )

            start = original.index(old_text)
            end = start + len(old_text)

            for (
                existing_start,
                existing_end,
                _,
            ) in spans:
                if start < existing_end and end > existing_start:
                    raise WorkerError(f"Structured edits overlap in {path}.")

            spans.append(
                (
                    start,
                    end,
                    new_text,
                )
            )

        updated = original

        for start, end, new_text in sorted(
            spans,
            key=lambda item: item[0],
            reverse=True,
        ):
            updated = updated[:start] + new_text + updated[end:]

        target.write_text(
            updated,
            encoding="utf-8",
        )

    patch = run(
        [
            "git",
            "diff",
            "--no-ext-diff",
            "--binary",
            "HEAD",
            "--",
        ],
        cwd=workspace,
    ).stdout

    if not patch.strip():
        raise WorkerError("Structured edits produced no Git diff.")

    paths, patch_hash = validate_patch_policy(
        patch,
        policy,
        config,
    )

    expected_paths = sorted(grouped)

    if paths != expected_paths:
        raise WorkerError(
            "Generated Git diff paths do not match the requested structured edit paths."
        )

    return patch, paths, patch_hash


def normalise_unified_diff_hunk_counts(
    patch: str,
) -> str:
    """Recalculate unified-diff hunk counts only."""
    lines = patch.splitlines(keepends=True)

    header = re.compile(
        r"^@@ -(\d+)(?:,\d+)? "
        r"\+(\d+)(?:,\d+)? @@(.*)$"
    )

    index = 0

    while index < len(lines):
        raw = lines[index]
        line = raw.rstrip("\r\n")
        match = header.match(line)

        if match is None:
            index += 1
            continue

        old_count = 0
        new_count = 0
        end = index + 1

        while end < len(lines):
            body = lines[end].rstrip("\r\n")

            if body.startswith("@@ ") or body.startswith("diff --git "):
                break

            if body == r"\ No newline at end of file":
                end += 1
                continue

            if not body:
                raise WorkerError("Unified diff hunk contains an unprefixed blank line.")

            prefix = body[0]

            if prefix == " ":
                old_count += 1
                new_count += 1
            elif prefix == "-":
                old_count += 1
            elif prefix == "+":
                new_count += 1
            else:
                raise WorkerError("Unified diff hunk contains an invalid body line.")

            end += 1

        newline = "\r\n" if raw.endswith("\r\n") else "\n" if raw.endswith("\n") else ""

        lines[index] = (
            f"@@ -{match.group(1)},{old_count} "
            f"+{match.group(2)},{new_count} "
            f"@@{match.group(3)}"
            f"{newline}"
        )

        index = end

    return "".join(lines)


def apply_patch(workspace: Path, patch: str, candidate_id: int) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    patch_path = ARTIFACTS / f"candidate-{candidate_id}.patch"
    patch_path.write_text(patch, encoding="utf-8")
    run(["git", "apply", "--check", str(patch_path)], cwd=workspace)
    run(["git", "apply", str(patch_path)], cwd=workspace)
    return patch_path


def security_diff_scan(workspace: Path, policy: dict[str, Any]) -> dict[str, Any]:
    diff = run(["git", "diff", "--unified=0"], cwd=workspace).stdout
    findings: list[str] = []
    additions = "\n".join(
        line[1:]
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    patterns = {
        "shell=True": r"shell\s*=\s*True",
        "os.system": r"\bos\.system\s*\(",
        "eval": r"\beval\s*\(",
        "exec": r"\bexec\s*\(",
        "pickle": r"\bpickle\.(?:loads?|load)\s*\(",
        "docker socket": r"/var/run/docker\.sock",
        "credential literal": r"(?i)(?:api[_-]?key|token|password|secret)\s*=\s*['\"][^'\"]{8,}",
        "unsafe yaml": r"yaml\.load\s*\(",
    }
    for name, expression in patterns.items():
        if re.search(expression, additions):
            findings.append(name)
    return {"passed": not findings, "findings": findings}


def command_result(
    name: str, completed: subprocess.CompletedProcess[str], blocking: bool = True
) -> dict[str, Any]:
    return {
        "name": name,
        "passed": completed.returncode == 0,
        "blocking": blocking,
        "returncode": completed.returncode,
        "output": completed.stdout[-12000:],
    }


def _normalise_bandit_filename(
    value: Any,
) -> str:
    filename = str(value or "").replace(
        "\\\\",
        "/",
    )

    marker = "bridge/app/"

    if marker in filename:
        filename = (
            marker
            + filename.split(
                marker,
                1,
            )[1]
        )

    return filename


def _bandit_issue_key(
    issue: dict[str, Any],
) -> tuple[str, str, str, str, str]:
    """
    Build a line-number-independent Bandit fingerprint.

    Counter semantics preserve duplicate findings in one file, while
    excluding line numbers means harmless line shifts do not appear
    to be newly introduced security findings.
    """

    return (
        _normalise_bandit_filename(issue.get("filename")),
        str(issue.get("test_id") or ""),
        str(issue.get("issue_text") or ""),
        str(issue.get("issue_severity") or "").upper(),
        str(issue.get("issue_confidence") or "").upper(),
    )


def _parse_bandit_json(
    output: str,
) -> list[dict[str, Any]] | None:
    text = str(output or "").strip()

    candidates = [
        text,
    ]

    first = text.find("{")

    last = text.rfind("}")

    if first >= 0 and last >= first:
        candidates.append(text[first : last + 1])

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (
            TypeError,
            ValueError,
        ):
            continue

        results = payload.get("results")

        if not isinstance(
            results,
            list,
        ):
            continue

        return [
            item
            for item in results
            if isinstance(
                item,
                dict,
            )
        ]

    return None


def _run_bandit_json(
    workspace: Path,
) -> tuple[
    subprocess.CompletedProcess[str],
    list[dict[str, Any]] | None,
]:
    completed = run(
        [
            str(VENV_PYTHON),
            "-m",
            "bandit",
            "-q",
            "-f",
            "json",
            "-r",
            "bridge/app",
        ],
        cwd=workspace,
        timeout=300,
        check=False,
    )

    if completed.returncode not in {
        0,
        1,
    }:
        return (
            completed,
            None,
        )

    return (
        completed,
        _parse_bandit_json(completed.stdout),
    )


def bandit_baseline_result(
    workspace: Path,
) -> dict[str, Any]:
    """
    Compare the candidate's Bandit findings with the clean live
    repository baseline.

    Existing security debt remains visible, but only a finding newly
    introduced by the candidate is blocking.
    """

    baseline_completed, baseline_issues = _run_bandit_json(ROOT)

    candidate_completed, candidate_issues = _run_bandit_json(workspace)

    if baseline_issues is None:
        return {
            "name": "bandit_baseline",
            "passed": False,
            "blocking": True,
            "returncode": (baseline_completed.returncode),
            "baseline_findings": None,
            "candidate_findings": None,
            "new_findings": [],
            "fixed_findings": None,
            "output": (
                "Unable to parse or execute the "
                "production Bandit baseline.\n" + baseline_completed.stdout[-8000:]
            ),
        }

    if candidate_issues is None:
        return {
            "name": "bandit_baseline",
            "passed": False,
            "blocking": True,
            "returncode": (candidate_completed.returncode),
            "baseline_findings": len(baseline_issues),
            "candidate_findings": None,
            "new_findings": [],
            "fixed_findings": None,
            "output": (
                "Unable to parse or execute the "
                "candidate Bandit scan.\n" + candidate_completed.stdout[-8000:]
            ),
        }

    baseline_counts = Counter(_bandit_issue_key(issue) for issue in baseline_issues)

    candidate_counts = Counter(_bandit_issue_key(issue) for issue in candidate_issues)

    new_counts = candidate_counts - baseline_counts

    fixed_counts = baseline_counts - candidate_counts

    remaining = Counter(new_counts)

    new_findings: list[dict[str, Any]] = []

    for issue in candidate_issues:
        key = _bandit_issue_key(issue)

        if (
            remaining.get(
                key,
                0,
            )
            <= 0
        ):
            continue

        new_findings.append(
            {
                "filename": (_normalise_bandit_filename(issue.get("filename"))),
                "test_id": issue.get("test_id"),
                "issue_text": issue.get("issue_text"),
                "severity": issue.get("issue_severity"),
                "confidence": issue.get("issue_confidence"),
                "line_number": issue.get("line_number"),
            }
        )

        remaining[key] -= 1

    new_count = sum(new_counts.values())

    fixed_count = sum(fixed_counts.values())

    passed = new_count == 0

    return {
        "name": "bandit_baseline",
        "passed": passed,
        "blocking": True,
        "returncode": (0 if passed else 1),
        "baseline_findings": len(baseline_issues),
        "candidate_findings": len(candidate_issues),
        "new_findings": new_findings,
        "new_findings_count": new_count,
        "fixed_findings": fixed_count,
        "output": (
            "Bandit baseline comparison: "
            f"baseline={len(baseline_issues)}, "
            f"candidate={len(candidate_issues)}, "
            f"new={new_count}, "
            f"fixed={fixed_count}."
        ),
    }


def _pytest_failure_identity(
    testcase: ET.Element,
) -> str:
    file_name = testcase.get("file") or ""

    class_name = testcase.get("classname") or ""

    test_name = testcase.get("name") or ""

    return "::".join(
        value
        for value in (
            file_name,
            class_name,
            test_name,
        )
        if value
    )


def _load_pytest_junit(
    path: Path,
) -> tuple[
    Counter[str],
    Counter[str],
    int,
    dict[str, list[str]],
]:
    if not path.is_file():
        raise WorkerError(f"Missing pytest JUnit report: {path}")
    try:
        root = ET.parse(path).getroot()
    except (
        ET.ParseError,
        OSError,
    ) as exc:
        raise WorkerError(f"Invalid pytest JUnit report: {path}: {exc}") from exc

    failures: Counter[str] = Counter()
    tests: Counter[str] = Counter()
    failure_details: dict[str, list[str]] = {}
    total = 0

    for testcase in root.findall(".//testcase"):
        total += 1
        identity = _pytest_failure_identity(testcase)
        if not identity:
            raise WorkerError("Pytest JUnit report contains a testcase with no stable identity.")

        tests[identity] += 1

        failure_node = testcase.find("failure")
        error_node = testcase.find("error")
        if failure_node is None and error_node is None:
            continue

        failures[identity] += 1

        detail_node = failure_node if failure_node is not None else error_node
        if detail_node is None:
            continue

        detail_parts: list[str] = []
        message = str(detail_node.get("message") or "").strip()
        body = str(detail_node.text or "").strip()

        if message:
            detail_parts.append(message)
        if body:
            detail_parts.append(body)

        detail = "\n".join(detail_parts).strip()
        if not detail:
            continue

        # Evidence is diagnostic only. Keep it bounded and
        # redact secrets before it can reach candidate feedback.
        clean_detail = redact(detail)[-6000:]

        failure_details.setdefault(
            identity,
            [],
        ).append(clean_detail)

    return (
        failures,
        tests,
        total,
        failure_details,
    )


def _prepare_pytest_build_context(
    workspace: Path,
    destination: Path,
) -> None:
    """
    Build a deliberately narrow Docker build context.

    Docker must never receive the whole Jarvis repository or
    candidate worktree merely to run pytest.
    """

    workspace_root = workspace.resolve()

    if not workspace_root.is_dir():
        raise WorkerError(f"Invalid pytest workspace: {workspace}")

    destination.mkdir(
        parents=True,
        exist_ok=False,
    )

    allowed_sources = (
        "bridge",
        "config",
        "tools",
        "requirements-improver.txt",
    )

    forbidden_components = {
        ".git",
        ".ssh",
        ".gnupg",
    }

    def validate_source(
        source: Path,
    ) -> None:
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise WorkerError(f"Unable to resolve pytest context source: {source}: {exc}") from exc

        if not resolved.is_relative_to(workspace_root):
            raise WorkerError(f"Pytest context source escapes workspace: {source}")

        if source.is_symlink():
            raise WorkerError(f"Pytest context source may not be a symlink: {source}")

        items = source.rglob("*") if source.is_dir() else ()

        for item in items:
            relative = item.relative_to(workspace)

            if item.is_symlink():
                raise WorkerError(f"Pytest build context contains a symlink: {relative}")

            if any(
                (part in forbidden_components or part == ".env" or part.startswith(".env."))
                for part in relative.parts
            ):
                raise WorkerError(f"Pytest build context contains a forbidden path: {relative}")

    for name in allowed_sources:
        source = workspace / name

        if not source.exists():
            raise WorkerError(f"Required pytest build-context source is missing: {name}")

        validate_source(source)

        target = destination / name

        if source.is_dir():
            shutil.copytree(
                source,
                target,
                symlinks=False,
                ignore=shutil.ignore_patterns(
                    "__pycache__",
                    "*.pyc",
                    ".pytest_cache",
                    ".ruff_cache",
                ),
            )
        else:
            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            shutil.copy2(
                source,
                target,
            )

    forbidden_top_level = (
        ".env",
        ".git",
        "data",
        "logs",
        "backup",
        ".jarvis-improver",
        ".venv",
        ".venv-improver",
        "speaker-data",
    )

    leaked = [name for name in forbidden_top_level if (destination / name).exists()]

    if leaked:
        raise WorkerError(
            "Forbidden paths leaked into pytest Docker context: " + ", ".join(sorted(leaked))
        )


def _pytest_runtime_dockerfile() -> str:
    return """FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/bridge

RUN apt-get update \\
    && apt-get install -y --no-install-recommends git \\
    && rm -rf /var/lib/apt/lists/*

COPY bridge/requirements.txt /tmp/app-requirements.txt

RUN pip install --no-cache-dir \\
    -r /tmp/app-requirements.txt \\
    "pytest>=8,<10" \\
    "pytest-asyncio>=0.24,<2"

WORKDIR /workspace

COPY bridge /workspace/bridge
COPY config /workspace/config
COPY tools /workspace/tools
COPY requirements-improver.txt /workspace/requirements-improver.txt

RUN mkdir -p \\
    /workspace/data \\
    /workspace/logs \\
    /workspace/.jarvis-improver

CMD ["python", "-m", "pytest", "-q", "bridge/tests", "-p", "no:cacheprovider"]
"""


def _docker_pytest_scan(
    workspace: Path,
    *,
    label: str,
    timeout: int,
) -> dict[str, Any]:
    safe_label = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "-",
        label,
    ).strip("-")

    if not safe_label:
        safe_label = "scan"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix=(f"jarvis-pytest-{safe_label}-"),
            dir=WORK_ROOT,
        )
    )

    results_dir = temp_root / "results"

    results_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Temporary result mount only.
    results_dir.chmod(0o777)

    build_context = temp_root / "context"

    image = f"jarvis-pytest-{safe_label}:{os.getpid()}-{time.time_ns()}"

    report = results_dir / "report.xml"

    try:
        try:
            _prepare_pytest_build_context(
                workspace,
                build_context,
            )
        except WorkerError as exc:
            return {
                "ok": False,
                "stage": "context",
                "returncode": 1,
                "total_tests": None,
                "failures": Counter(),
                "tests": Counter(),
                "output": str(exc),
            }

        dockerfile = build_context / "Dockerfile.pytest"

        dockerfile.write_text(
            _pytest_runtime_dockerfile(),
            encoding="utf-8",
        )

        build = run(
            [
                "docker",
                "build",
                "-f",
                "Dockerfile.pytest",
                "-t",
                image,
                ".",
            ],
            cwd=build_context,
            timeout=timeout,
            check=False,
        )

        if build.returncode != 0:
            return {
                "ok": False,
                "stage": "build",
                "returncode": (build.returncode),
                "total_tests": None,
                "failures": Counter(),
                "tests": Counter(),
                "output": build.stdout[-12000:],
            }

        completed = run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--security-opt",
                "no-new-privileges:true",
                "--cap-drop",
                "ALL",
                "--memory",
                "1g",
                "--cpus",
                "2",
                "--pids-limit",
                "256",
                "--tmpfs",
                ("/tmp:rw,noexec,nosuid,size=128m"),
                "--tmpfs",
                ("/workspace/data:rw,noexec,nosuid,size=128m"),
                "--tmpfs",
                ("/workspace/logs:rw,noexec,nosuid,size=64m"),
                "--tmpfs",
                ("/workspace/.jarvis-improver:rw,noexec,nosuid,size=64m"),
                "-v",
                (f"{results_dir}:/results:rw"),
                image,
                "python",
                "-m",
                "pytest",
                "-q",
                "bridge/tests",
                "-p",
                "no:cacheprovider",
                ("--junitxml=/results/report.xml"),
            ],
            cwd=build_context,
            timeout=timeout,
            check=False,
        )

        if completed.returncode not in {
            0,
            1,
        }:
            return {
                "ok": False,
                "stage": "pytest",
                "returncode": (completed.returncode),
                "total_tests": None,
                "failures": Counter(),
                "tests": Counter(),
                "output": completed.stdout[-12000:],
            }

        if not report.is_file():
            return {
                "ok": False,
                "stage": "report",
                "returncode": (completed.returncode),
                "total_tests": None,
                "failures": Counter(),
                "tests": Counter(),
                "output": (
                    "Pytest finished but "
                    "did not create its "
                    "JUnit report.\n" + completed.stdout[-12000:]
                ),
            }

        try:
            (
                failures,
                tests,
                total,
                failure_details,
            ) = _load_pytest_junit(report)
        except WorkerError as exc:
            return {
                "ok": False,
                "stage": "report",
                "returncode": (completed.returncode),
                "total_tests": None,
                "failures": Counter(),
                "tests": Counter(),
                "output": str(exc),
            }

        return {
            "ok": True,
            "stage": "complete",
            "returncode": (completed.returncode),
            "total_tests": total,
            "failures": failures,
            "tests": tests,
            "failure_details": failure_details,
            "output": completed.stdout[-12000:],
        }

    finally:
        run(
            [
                "docker",
                "image",
                "rm",
                "-f",
                image,
            ],
            cwd=workspace,
            timeout=60,
            check=False,
        )

        shutil.rmtree(
            temp_root,
            ignore_errors=True,
        )


def pytest_baseline_result(
    workspace: Path,
    timeout: int,
) -> dict[str, Any]:
    baseline = _docker_pytest_scan(
        ROOT,
        label="baseline",
        timeout=timeout,
    )

    if not baseline.get("ok"):
        return {
            "name": "pytest_baseline",
            "passed": False,
            "blocking": True,
            "returncode": (baseline.get("returncode")),
            "runtime": "python:3.12-slim",
            "baseline_total_tests": None,
            "candidate_total_tests": None,
            "baseline_failures": None,
            "candidate_failures": None,
            "new_failures_count": None,
            "new_failures": [],
            "resolved_failures_count": None,
            "missing_tests_count": None,
            "missing_tests": [],
            "added_tests_count": None,
            "output": (
                "Unable to establish "
                "the production pytest "
                "baseline.\n" + str(baseline.get("output") or "")[-12000:]
            ),
        }

    candidate = _docker_pytest_scan(
        workspace,
        label="candidate",
        timeout=timeout,
    )

    if not candidate.get("ok"):
        return {
            "name": "pytest_baseline",
            "passed": False,
            "blocking": True,
            "returncode": (candidate.get("returncode")),
            "runtime": "python:3.12-slim",
            "baseline_total_tests": (baseline.get("total_tests")),
            "candidate_total_tests": None,
            "baseline_failures": sum(baseline["failures"].values()),
            "candidate_failures": None,
            "new_failures_count": None,
            "new_failures": [],
            "resolved_failures_count": None,
            "missing_tests_count": None,
            "missing_tests": [],
            "added_tests_count": None,
            "output": (
                "Unable to execute "
                "candidate pytest "
                "validation.\n" + str(candidate.get("output") or "")[-12000:]
            ),
        }

    baseline_failures: Counter[str] = baseline["failures"]

    candidate_failures: Counter[str] = candidate["failures"]

    baseline_tests: Counter[str] = baseline["tests"]

    candidate_tests: Counter[str] = candidate["tests"]

    new_counts = candidate_failures - baseline_failures

    resolved_counts = baseline_failures - candidate_failures

    missing_test_counts = baseline_tests - candidate_tests

    added_test_counts = candidate_tests - baseline_tests

    candidate_failure_details = candidate.get("failure_details") or {}
    new_failures: list[dict[str, Any]] = []

    for identity, count in sorted(new_counts.items()):
        item: dict[str, Any] = {
            "test": identity,
            "count": count,
        }

        raw_details = candidate_failure_details.get(identity) or []
        if isinstance(
            raw_details,
            list,
        ):
            evidence = "\n\n".join(str(value) for value in raw_details[:2] if str(value).strip())[
                -8000:
            ]
            if evidence:
                item["evidence"] = evidence

        new_failures.append(item)

    missing_tests = [
        {
            "test": identity,
            "count": count,
        }
        for identity, count in sorted(missing_test_counts.items())
    ]

    new_count = sum(new_counts.values())

    resolved_count = sum(resolved_counts.values())

    missing_count = sum(missing_test_counts.values())

    added_count = sum(added_test_counts.values())

    passed = new_count == 0 and missing_count == 0

    return {
        "name": "pytest_baseline",
        "passed": passed,
        "blocking": True,
        "returncode": (0 if passed else 1),
        "runtime": "python:3.12-slim",
        "baseline_total_tests": (baseline["total_tests"]),
        "candidate_total_tests": (candidate["total_tests"]),
        "baseline_failures": sum(baseline_failures.values()),
        "candidate_failures": sum(candidate_failures.values()),
        "new_failures_count": (new_count),
        "new_failures": (new_failures),
        "resolved_failures_count": (resolved_count),
        "missing_tests_count": (missing_count),
        "missing_tests": (missing_tests),
        "added_tests_count": (added_count),
        "output": (
            "Pytest baseline comparison: "
            f"baseline_total="
            f"{baseline['total_tests']}, "
            f"candidate_total="
            f"{candidate['total_tests']}, "
            f"baseline_failures="
            f"{sum(baseline_failures.values())}, "
            f"candidate_failures="
            f"{sum(candidate_failures.values())}, "
            f"new_failures={new_count}, "
            f"resolved_failures={resolved_count}, "
            f"missing_tests={missing_count}, "
            f"added_tests={added_count}."
        ),
    }


def run_validation(
    workspace: Path, policy: dict[str, Any], config: WorkerConfig
) -> tuple[dict[str, Any], dict[str, Any]]:
    results: list[dict[str, Any]] = []

    commands: list[tuple[str, list[str], bool, int]] = [
        ("git_diff_check", ["git", "diff", "--check"], True, 60),
        ("compileall", [str(VENV_PYTHON), "-m", "compileall", "-q", "bridge/app"], True, 180),
        (
            "ruff",
            [str(VENV_PYTHON), "-m", "ruff", "check", "bridge/app", "bridge/tests"],
            True,
            240,
        ),
    ]
    for name, command, blocking, timeout in commands:
        completed = run(command, cwd=workspace, timeout=timeout, check=False)
        results.append(command_result(name, completed, blocking))

    results.append(
        pytest_baseline_result(
            workspace,
            config.candidate_timeout_seconds,
        )
    )

    results.append(bandit_baseline_result(workspace))

    if (workspace / "bridge/requirements.txt").exists():
        completed = run(
            [
                str(VENV_PYTHON),
                "-m",
                "pip_audit",
                "-r",
                "bridge/requirements.txt",
                "--progress-spinner",
                "off",
            ],
            cwd=workspace,
            timeout=300,
            check=False,
        )
        results.append(command_result("pip_audit", completed, blocking=False))

    security = security_diff_scan(workspace, policy)
    security["tools"] = [
        item
        for item in results
        if item["name"]
        in {
            "bandit_baseline",
            "pip_audit",
        }
    ]
    passed = all(item["passed"] for item in results if item["blocking"]) and security["passed"]
    return {"passed": passed, "checks": results}, security


def docker_smoke_test(
    workspace: Path,
    candidate_id: int,
    timeout: int,
) -> dict[str, Any]:
    image = f"jarvis-candidate:{candidate_id}"
    container = f"jarvis-candidate-{candidate_id}"

    temp_root = Path(
        tempfile.mkdtemp(
            prefix=f"jarvis-candidate-{candidate_id}-",
            dir=WORK_ROOT,
        )
    )

    for name in (
        "data",
        "logs",
        "config",
        "tmp",
    ):
        path = temp_root / name

        path.mkdir(
            parents=True,
            exist_ok=True,
        )

        if name in {
            "data",
            "logs",
            "config",
        }:
            # These directories are temporary and isolated to the
            # candidate container. Docker user namespaces may map
            # the container process to a host UID that is neither
            # the worker owner nor group, so normal 0775 ownership
            # is insufficient for SQLite and application logs.
            path.chmod(0o777)

    try:
        build = run(
            [
                "docker",
                "build",
                "-t",
                image,
                "bridge",
            ],
            cwd=workspace,
            timeout=timeout,
            check=False,
        )

        if build.returncode != 0:
            return {
                "passed": False,
                "stage": "build",
                "output": build.stdout[-12000:],
            }

        run_command = [
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "--network",
            "none",
            "--read-only",
            "--security-opt",
            "no-new-privileges:true",
            "--cap-drop",
            "ALL",
            "--memory",
            "512m",
            "--cpus",
            "1.0",
            "--pids-limit",
            "128",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "-v",
            f"{temp_root / 'data'}:/app/data:rw",
            "-v",
            f"{temp_root / 'logs'}:/app/logs:rw",
            "-v",
            f"{temp_root / 'config'}:/app/config:rw",
            "-e",
            "OPENAI_API_KEY=dummy",
            "-e",
            "OPENAI_MODEL=gpt-5-mini",
            "-e",
            "HOME_ASSISTANT_URL=http://127.0.0.1:9",
            "-e",
            "HOME_ASSISTANT_TOKEN=dummy",
            "-e",
            "JARVIS_ADMIN_MODE_ENABLED=false",
            "-e",
            "JARVIS_AWARENESS_ENABLED=false",
            "-e",
            "JARVIS_SELF_IMPROVEMENT_ENABLED=false",
            image,
        ]

        started = run(
            run_command,
            cwd=workspace,
            timeout=60,
            check=False,
        )

        if started.returncode != 0:
            return {
                "passed": False,
                "stage": "start",
                "output": started.stdout[-12000:],
            }

        deadline = time.monotonic() + min(
            timeout,
            120,
        )

        last_output = ""

        while time.monotonic() < deadline:
            state = run(
                [
                    "docker",
                    "inspect",
                    "-f",
                    ("{{.State.Running}} {{.State.ExitCode}}"),
                    container,
                ],
                cwd=workspace,
                timeout=10,
                check=False,
            )

            if state.returncode != 0:
                logs = run(
                    [
                        "docker",
                        "logs",
                        container,
                    ],
                    cwd=workspace,
                    timeout=20,
                    check=False,
                )

                return {
                    "passed": False,
                    "stage": "startup",
                    "output": (state.stdout + "\n" + logs.stdout)[-12000:],
                }

            state_parts = state.stdout.strip().split()

            running = bool(state_parts and state_parts[0].casefold() == "true")

            if not running:
                exit_code = state_parts[1] if len(state_parts) > 1 else "unknown"

                logs = run(
                    [
                        "docker",
                        "logs",
                        container,
                    ],
                    cwd=workspace,
                    timeout=20,
                    check=False,
                )

                return {
                    "passed": False,
                    "stage": "startup",
                    "output": (
                        f"Candidate container exited with code {exit_code}.\n" + logs.stdout
                    )[-12000:],
                }

            check = run(
                [
                    "docker",
                    "exec",
                    container,
                    "python",
                    "-c",
                    (
                        "import urllib.request; "
                        "print("
                        "urllib.request.urlopen("
                        "'http://127.0.0.1:8000/health', "
                        "timeout=3"
                        ").read().decode()"
                        ")"
                    ),
                ],
                cwd=workspace,
                timeout=10,
                check=False,
            )

            last_output = check.stdout

            if check.returncode == 0 and '"status":"healthy"' in check.stdout.replace(
                " ",
                "",
            ):
                return {
                    "passed": True,
                    "stage": "health",
                    "output": check.stdout[-4000:],
                }

            time.sleep(2)

        logs = run(
            [
                "docker",
                "logs",
                container,
            ],
            cwd=workspace,
            timeout=20,
            check=False,
        ).stdout

        return {
            "passed": False,
            "stage": "health",
            "output": (last_output + "\n" + logs)[-12000:],
        }

    finally:
        run(
            [
                "docker",
                "rm",
                "-f",
                container,
            ],
            cwd=workspace,
            timeout=30,
            check=False,
        )

        run(
            [
                "docker",
                "image",
                "rm",
                "-f",
                image,
            ],
            cwd=workspace,
            timeout=60,
            check=False,
        )

        shutil.rmtree(
            temp_root,
            ignore_errors=True,
        )


def determine_risk(paths: list[str], model_risk: str, policy: dict[str, Any]) -> str:
    high_patterns = policy.get("high_risk_paths", [])
    medium_patterns = policy.get("medium_risk_paths", [])
    if any(path_matches(path, high_patterns) for path in paths):
        return "high"
    if any(path_matches(path, medium_patterns) for path in paths):
        return "medium"
    if model_risk not in {"low", "medium", "high"}:
        return "medium"
    return model_risk


def commit_candidate(workspace: Path, candidate_id: int, failure_id: int, summary: str) -> str:
    run(["git", "add", "--", "bridge/app", "bridge/tests", "config"], cwd=workspace)
    status = run(["git", "status", "--porcelain"], cwd=workspace).stdout.strip()
    if not status:
        raise WorkerError("Candidate patch produced no tracked changes.")
    message = (
        f"Jarvis improvement {candidate_id}: {summary[:72]}\n\nFixes recorded failure {failure_id}."
    )
    run(["git", "commit", "-m", message], cwd=workspace)
    return run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()


def maybe_create_pr(
    workspace: Path, branch: str, candidate_id: int, summary: str, config: WorkerConfig
) -> str | None:
    if not config.github_enabled or shutil.which("gh") is None:
        return None
    auth = run(["gh", "auth", "status"], cwd=workspace, timeout=30, check=False)
    if auth.returncode != 0:
        return None
    push = run(["git", "push", "-u", "origin", branch], cwd=workspace, timeout=180, check=False)
    if push.returncode != 0:
        return None
    body = textwrap.dedent(
        f"""
        ## Jarvis autonomous improvement candidate {candidate_id}

        {summary}

        This pull request was generated from a recorded Jarvis failure, tested in an isolated worktree and candidate container, and still requires human review before deployment.
        """
    ).strip()
    pr = run(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--base",
            config.base_branch,
            "--head",
            branch,
            "--title",
            f"Jarvis improvement {candidate_id}: {summary[:60]}",
            "--body",
            body,
        ],
        cwd=workspace,
        timeout=120,
        check=False,
    )
    if pr.returncode == 0:
        for line in reversed(pr.stdout.splitlines()):
            if line.strip().startswith("http"):
                return line.strip()
    return None


def _normalise_commit_sha(
    value: Any,
    *,
    label: str,
) -> str:
    sha = str(value or "").strip().lower()

    if not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}",
        sha,
    ):
        raise WorkerError(f"Invalid {label} commit SHA.")

    return sha


def candidate_diff_sha256(
    base_commit: str,
    candidate_commit: str,
    *,
    cwd: Path = ROOT,
) -> str:
    base = _normalise_commit_sha(
        base_commit,
        label="base",
    )

    candidate = _normalise_commit_sha(
        candidate_commit,
        label="candidate",
    )

    completed = run(
        [
            "git",
            "diff",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            base,
            candidate,
            "--",
        ],
        cwd=cwd,
        timeout=120,
    )

    diff = completed.stdout

    if not diff.strip():
        raise WorkerError("Validated candidate diff is empty.")

    return hashlib.sha256(diff.encode("utf-8")).hexdigest()


def verify_candidate_deploy_binding(
    candidate: dict[str, Any],
) -> dict[str, str]:
    candidate_id = int(candidate["candidate_id"])

    if str(candidate.get("status") or "") not in {
        "deploy_requested",
        "deploying",
    }:
        raise WorkerError("Candidate is not in an authorised deployment state.")

    expected_branch = f"jarvis/improvement-{candidate_id}"

    branch = str(candidate.get("branch_name") or "")

    if branch != expected_branch:
        raise WorkerError("Candidate branch binding is invalid.")

    workspace_raw = str(candidate.get("workspace_path") or "").strip()

    if not workspace_raw:
        raise WorkerError("Candidate workspace binding is missing.")

    workspace = Path(workspace_raw)

    if not workspace.exists() or workspace.is_symlink():
        raise WorkerError("Candidate workspace is missing or unsafe.")

    expected_workspace = (WORKTREES / str(candidate_id)).resolve()

    try:
        resolved_workspace = workspace.resolve(strict=True)
    except OSError as exc:
        raise WorkerError("Candidate workspace cannot be resolved.") from exc

    if resolved_workspace != expected_workspace:
        raise WorkerError(
            "Candidate workspace path does not match the controlled worktree location."
        )

    base_commit = _normalise_commit_sha(
        candidate.get("base_commit"),
        label="stored base",
    )

    candidate_commit = _normalise_commit_sha(
        candidate.get("candidate_commit"),
        label="stored candidate",
    )

    stored_hash = str(candidate.get("validated_patch_sha256") or "").strip().lower()

    if not re.fullmatch(
        r"[0-9a-f]{64}",
        stored_hash,
    ):
        raise WorkerError("Validated candidate patch hash is missing or invalid.")

    ensure_repo()

    current_ref = _normalise_commit_sha(
        run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ]
        ).stdout.strip(),
        label="live HEAD",
    )

    if current_ref != base_commit:
        raise WorkerError(
            "Live Jarvis HEAD no longer matches the candidate's validated base commit."
        )

    branch_ref = _normalise_commit_sha(
        run(
            [
                "git",
                "rev-parse",
                "--verify",
                branch,
            ]
        ).stdout.strip(),
        label="candidate branch",
    )

    if branch_ref != candidate_commit:
        raise WorkerError("Candidate branch moved after validation.")

    workspace_ref = _normalise_commit_sha(
        run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            cwd=resolved_workspace,
        ).stdout.strip(),
        label="candidate workspace",
    )

    if workspace_ref != candidate_commit:
        raise WorkerError("Candidate worktree moved after validation.")

    merge_base = _normalise_commit_sha(
        run(
            [
                "git",
                "merge-base",
                base_commit,
                candidate_commit,
            ]
        ).stdout.strip(),
        label="merge base",
    )

    if merge_base != base_commit:
        raise WorkerError(
            "Candidate is no longer a direct descendant of its validated base commit."
        )

    actual_hash = candidate_diff_sha256(
        base_commit,
        candidate_commit,
        cwd=ROOT,
    )

    if not secrets.compare_digest(
        stored_hash,
        actual_hash,
    ):
        raise WorkerError("Candidate diff changed after validation.")

    return {
        "base_commit": base_commit,
        "candidate_commit": candidate_commit,
        "validated_patch_sha256": actual_hash,
        "branch": branch,
        "workspace": str(resolved_workspace),
    }


def reset_repository_to_ref(
    rollback_ref: str,
    *,
    repo_root: Path = ROOT,
) -> str:
    target = _normalise_commit_sha(
        rollback_ref,
        label="rollback",
    )

    run(
        [
            "git",
            "rev-parse",
            "--verify",
            f"{target}^{{commit}}",
        ],
        cwd=repo_root,
        timeout=60,
    )

    run(
        [
            "git",
            "reset",
            "--hard",
            target,
        ],
        cwd=repo_root,
        timeout=120,
    )

    actual = _normalise_commit_sha(
        run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ],
            cwd=repo_root,
            timeout=60,
        ).stdout.strip(),
        label="post-reset HEAD",
    )

    if actual != target:
        raise WorkerError("Repository reset did not land on the requested rollback commit.")

    return actual


def process_queued_candidate(
    candidate: dict[str, Any], config: WorkerConfig, env_values: dict[str, str]
) -> None:
    candidate_id = int(candidate["candidate_id"])
    failure_id = int(candidate["failure_id"])
    failure = fetch_failure(failure_id)
    manual_request = not uses_autonomous_attempt_quota(failure)

    if not manual_request and attempts_today() >= config.max_attempts_per_day:
        return

    if not improvement_enabled():
        return

    policy = load_policy()
    ensure_repo()
    ensure_candidate_transaction_columns()

    base_commit = run(
        [
            "git",
            "rev-parse",
            "HEAD",
        ]
    ).stdout.strip()

    base_commit = _normalise_commit_sha(
        base_commit,
        label="candidate base",
    )

    branch = f"jarvis/improvement-{candidate_id}"
    update_candidate(
        candidate_id, status="generating", model=config.model, branch_name=branch, error=None
    )
    audit(
        "candidate_generation_started",
        failure_id=failure_id,
        candidate_id=candidate_id,
        details={
            "model": config.model,
            "manual_request": manual_request,
        },
    )

    context, context_files = build_context(
        failure,
        policy,
        base_commit=base_commit,
    )
    generation_error: str | None = None
    previous_patch: str | None = None
    payload: dict[str, Any] | None = None
    usage: dict[str, Any] = {}
    workspace: Path | None = None

    try:
        for attempt in range(1, 3):
            payload, usage = request_patch(
                failure=failure,
                context=context,
                context_files=context_files,
                policy=policy,
                config=config,
                env_values=env_values,
                previous_error=generation_error,
                previous_patch=previous_patch,
            )
            patch = ""
            try:
                workspace = create_worktree(
                    candidate_id,
                    branch,
                    base_commit,
                )

                workspace_base = run(
                    [
                        "git",
                        "rev-parse",
                        "HEAD",
                    ],
                    cwd=workspace,
                ).stdout.strip()

                if (
                    _normalise_commit_sha(
                        workspace_base,
                        label="worktree base",
                    )
                    != base_commit
                ):
                    raise WorkerError(
                        "Candidate worktree was not created from the captured base commit."
                    )

                (
                    patch,
                    paths,
                    patch_hash,
                ) = materialise_structured_edits(
                    workspace,
                    payload,
                    policy,
                    config,
                )
                payload["patch"] = patch

                # Prove the Git-generated artifact can be
                # reapplied cleanly from the captured base.
                run(
                    [
                        "git",
                        "reset",
                        "--hard",
                        "HEAD",
                    ],
                    cwd=workspace,
                )

                patch_path = apply_patch(
                    workspace,
                    patch,
                    candidate_id,
                )
                break
            except Exception as exc:
                generation_error = str(exc)

                source_feedback = _apply_failure_source_feedback(
                    generation_error,
                    base_commit,
                )

                if source_feedback:
                    generation_error += source_feedback

                previous_patch = patch
                if workspace is not None:
                    run(["git", "worktree", "remove", "--force", str(workspace)], check=False)
                    workspace = None
                if attempt >= 2:
                    raise
        if payload is None or workspace is None:
            raise WorkerError("No candidate patch was generated.")

        validation_repair_used = False

        while True:
            tests, security = run_validation(
                workspace,
                policy,
                config,
            )
            smoke = docker_smoke_test(
                workspace,
                candidate_id,
                config.candidate_timeout_seconds,
            )
            tests["candidate_container"] = smoke
            tests["passed"] = bool(tests.get("passed")) and bool(smoke.get("passed"))

            if tests["passed"] and security.get("passed"):
                break

            validation_error = (
                "Candidate validation failed.\n"
                + json.dumps(
                    {
                        "tests": tests,
                        "security": security,
                    },
                    ensure_ascii=False,
                    indent=2,
                )[-12000:]
            )

            if validation_repair_used:
                raise WorkerError(validation_error)

            validation_repair_used = True
            failed_patch = str(payload.get("patch") or "")

            audit(
                "candidate_validation_repair_started",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={"repair_attempt": 1},
            )

            run(
                [
                    "git",
                    "worktree",
                    "remove",
                    "--force",
                    str(workspace),
                ],
                check=False,
            )
            workspace = None

            payload, usage = request_patch(
                failure=failure,
                context=context,
                context_files=context_files,
                policy=policy,
                config=config,
                env_values=env_values,
                previous_error=validation_error,
                previous_patch=failed_patch,
            )

            workspace = create_worktree(
                candidate_id,
                branch,
                base_commit,
            )

            workspace_base = run(
                ["git", "rev-parse", "HEAD"],
                cwd=workspace,
            ).stdout.strip()

            if (
                _normalise_commit_sha(
                    workspace_base,
                    label="worktree base",
                )
                != base_commit
            ):
                raise WorkerError(
                    "Candidate repair worktree was not created from the captured base commit."
                )

            (
                patch,
                paths,
                patch_hash,
            ) = materialise_structured_edits(
                workspace,
                payload,
                policy,
                config,
            )
            payload["patch"] = patch

            run(
                [
                    "git",
                    "reset",
                    "--hard",
                    "HEAD",
                ],
                cwd=workspace,
            )

            patch_path = apply_patch(
                workspace,
                patch,
                candidate_id,
            )

        review = request_independent_review(
            failure=failure,
            workspace=workspace,
            tests=tests,
            security=security,
            config=config,
            env_values=env_values,
        )
        security["independent_ai_review"] = review
        if str(review.get("verdict") or "reject") != "approve":
            raise WorkerError(
                "Independent AI review rejected the candidate: "
                + str(review.get("summary") or review.get("findings") or "unspecified")
            )

        summary = str(payload.get("summary") or failure.get("summary") or "Jarvis improvement")
        root_cause = str(payload.get("root_cause") or "")
        model_risk = str(payload.get("risk") or "medium").lower()
        risk = determine_risk(paths, model_risk, policy)
        commit_sha = commit_candidate(
            workspace,
            candidate_id,
            failure_id,
            summary,
        )

        commit_sha = _normalise_commit_sha(
            commit_sha,
            label="candidate",
        )

        validated_patch_hash = candidate_diff_sha256(
            base_commit,
            commit_sha,
            cwd=workspace,
        )

        pr_url = maybe_create_pr(
            workspace,
            branch,
            candidate_id,
            summary,
            config,
        )

        approval_code = f"{secrets.randbelow(900000) + 100000:06d}"

        approval_code_expires_at = utc_after(24 * 60 * 60)

        diff_stats = {
            "changed_files": len(paths),
            "changed_lines": patch_line_count(str(payload.get("patch") or "")),
            "patch_sha256": patch_hash,
            "source_patch_sha256": patch_hash,
            "validated_patch_sha256": validated_patch_hash,
            "base_commit": base_commit,
            "candidate_commit": commit_sha,
            "commit_sha": commit_sha,
            "context_files": context_files,
            "tests_added": payload.get("tests_added", []),
            "notes": payload.get("notes", []),
        }
        # Every candidate now requires the transactional
        # human approval flow. There is deliberately no
        # automatic transition to deploy_requested.
        next_status = "awaiting_approval"
        update_candidate(
            candidate_id,
            status=next_status,
            workspace_path=str(workspace),
            summary=summary,
            root_cause=root_cause,
            risk=risk,
            patch_path=str(patch_path),
            changed_files_json=paths,
            diff_stats_json=diff_stats,
            test_results_json=tests,
            security_results_json=security,
            usage_json=usage,
            approval_code=approval_code,
            approval_code_expires_at=approval_code_expires_at,
            base_commit=base_commit,
            candidate_commit=commit_sha,
            validated_patch_sha256=validated_patch_hash,
            deploy_ticket_hash=None,
            deploy_ticket_salt=None,
            deploy_ticket_expires_at=None,
            deploy_ticket_consumed_at=None,
            deploy_lease_id=None,
            deploy_lease_started_at=None,
            deploy_lease_expires_at=None,
            deploy_phase="awaiting_approval",
            pr_url=pr_url,
            error=None,
            deploy_requested_at=None,
        )
        update_failure(failure_id, status="candidate_ready")
        audit(
            "candidate_ready",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "summary": summary,
                "risk": risk,
                "base_commit": base_commit,
                "candidate_commit": commit_sha,
                "validated_patch_sha256": validated_patch_hash,
                "pr_url": pr_url,
            },
        )
        notify_aaron(
            f"Jarvis has finished preparing improvement {candidate_id}. "
            "It passed the automated checks and is ready for you to review. "
            "Nothing has been installed yet.",
            title="Improvement ready for review",
            config=config,
            env_values=env_values,
        )
    except Exception as exc:
        update_candidate(candidate_id, status="failed", error=str(exc)[-12000:])
        update_failure(failure_id, status="recorded")
        audit(
            "candidate_failed",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={"error": str(exc)[-4000:]},
        )
        failed_where, failed_reason = plain_failure_explanation(str(exc))
        notify_aaron(
            f"{failed_reason} Nothing was installed. "
            f"It failed during {failed_where.lower()}. "
            "Open Improvements and tap Fix & Retry to try a corrected version.",
            title=f"Improvement {candidate_id} needs attention",
            config=config,
            env_values=env_values,
        )
        raise


def health_check(timeout_seconds: int) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:
                text = response.read().decode("utf-8", errors="replace")
                if response.status == 200 and '"status":"healthy"' in text.replace(" ", ""):
                    return True, text
        except Exception as exc:
            last_error = str(exc)
        time.sleep(3)
    return False, last_error


def monitor_logs(seconds: int = 30) -> tuple[bool, str]:
    time.sleep(min(seconds, 10))
    logs = run(
        ["docker", "compose", "logs", "--since", f"{seconds}s", "--tail", "300", "jarvis-core"],
        timeout=60,
        check=False,
    ).stdout
    bad = re.search(
        r"(?i)(traceback|syntaxerror|importerror|critical|application startup failed)", logs
    )
    return bad is None, logs[-12000:]


def deploy_candidate(
    candidate: dict[str, Any],
    config: WorkerConfig,
    env_values: dict[str, str],
) -> None:
    if config.proposal_only:
        raise WorkerError("Deployment is disabled while Proposal Mode is active.")
    if config.base_branch != AUTHORITATIVE_PRODUCTION_BRANCH:
        raise WorkerError(
            "Deployment is restricted to the authoritative "
            f"{AUTHORITATIVE_PRODUCTION_BRANCH} product line."
        )

    ensure_candidate_transaction_columns()

    candidate_id = int(candidate["candidate_id"])

    failure_id = int(candidate["failure_id"])

    claimed = claim_deployment(candidate_id)

    lease_id = str(claimed.get("deploy_lease_id") or "")

    if not lease_id:
        raise WorkerError("Deployment claim did not produce a lease.")

    merged = False

    try:
        binding = verify_candidate_deploy_binding(claimed)

        current_ref = binding["base_commit"]

        candidate_commit = binding["candidate_commit"]

        update_deployment_phase(
            candidate_id,
            lease_id,
            "premerge_verified",
        )

        audit(
            "candidate_deployment_claimed",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "base_commit": current_ref,
                "candidate_commit": candidate_commit,
            },
        )

        premerge_ref = _normalise_commit_sha(
            run(
                [
                    "git",
                    "rev-parse",
                    "HEAD",
                ]
            ).stdout.strip(),
            label="pre-merge HEAD",
        )

        if premerge_ref != current_ref:
            raise WorkerError("Live Jarvis HEAD changed after deployment binding verification.")

        update_deployment_phase(
            candidate_id,
            lease_id,
            "merging",
        )

        run(
            [
                "git",
                "merge",
                "--ff-only",
                candidate_commit,
            ]
        )

        merged = True

        merged_ref = _normalise_commit_sha(
            run(
                [
                    "git",
                    "rev-parse",
                    "HEAD",
                ]
            ).stdout.strip(),
            label="merged HEAD",
        )

        if merged_ref != candidate_commit:
            raise WorkerError(
                "The live repository did not land on the exact validated candidate commit."
            )

        update_deployment_phase(
            candidate_id,
            lease_id,
            "merged",
        )

        update_deployment_phase(
            candidate_id,
            lease_id,
            "rebuilding",
        )

        run(
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--build",
            ],
            timeout=600,
        )

        update_deployment_phase(
            candidate_id,
            lease_id,
            "verifying",
        )

        healthy, health_output = health_check(config.deploy_health_timeout_seconds)

        logs_ok, logs = monitor_logs(30)

        if not healthy or not logs_ok:
            raise WorkerError(
                "Deployment health verification failed.\n" + health_output + "\n" + logs
            )

        transition_deployment_state(
            candidate_id,
            lease_id,
            status="deployed",
            phase="deployed",
            deployed_at=utc_now(),
            error=None,
        )

        update_failure(
            failure_id,
            status="deployed",
        )

        audit(
            "candidate_deployed",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "commit": candidate_commit,
                "base_commit": current_ref,
                "validated_patch_sha256": (binding["validated_patch_sha256"]),
            },
        )

        notify_aaron(
            f"Improvement {candidate_id} deployed successfully and passed health checks.",
            title="Jarvis updated",
            config=config,
            env_values=env_values,
        )

    except Exception as exc:
        error = str(exc)[-12000:]

        if not merged:
            transition_deployment_state(
                candidate_id,
                lease_id,
                status="deployment_blocked",
                phase="premerge_failed",
                error=error,
            )

            audit(
                "candidate_deployment_blocked",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={
                    "error": error[-4000:],
                },
            )

            raise

        rollback_ref = _normalise_commit_sha(
            claimed.get("base_commit"),
            label="rollback base",
        )

        try:
            # This phase transition is also an ownership check.
            # A stale worker that lost its lease must never reset
            # the repository after another worker has taken over.
            update_deployment_phase(
                candidate_id,
                lease_id,
                "rollback_started",
            )

            reset_repository_to_ref(
                rollback_ref,
                repo_root=ROOT,
            )

            update_deployment_phase(
                candidate_id,
                lease_id,
                "rollback_rebuilding",
            )

            run(
                [
                    "docker",
                    "compose",
                    "up",
                    "-d",
                    "--build",
                ],
                timeout=600,
            )

            update_deployment_phase(
                candidate_id,
                lease_id,
                "rollback_verifying",
            )

            healthy, rollback_health = health_check(config.deploy_health_timeout_seconds)

            logs_ok, rollback_logs = monitor_logs(30)

            if not healthy or not logs_ok:
                raise WorkerError(
                    "Automatic rollback verification failed.\n"
                    + rollback_health
                    + "\n"
                    + rollback_logs
                )

            transition_deployment_state(
                candidate_id,
                lease_id,
                status="rolled_back",
                phase="rolled_back",
                rolled_back_at=utc_now(),
                error=error,
            )

            update_failure(
                failure_id,
                status="recorded",
            )

            audit(
                "candidate_auto_rolled_back",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={
                    "error": error[-4000:],
                    "rollback_ref": rollback_ref,
                },
            )

            notify_aaron(
                f"Improvement {candidate_id} failed deployment "
                "checks and was rolled back automatically.",
                title="Jarvis rollback completed",
                config=config,
                env_values=env_values,
            )

        except Exception as rollback_exc:
            if not deployment_lease_owned(
                candidate_id,
                lease_id,
            ):
                raise WorkerError(
                    "Deployment worker lost its lease during "
                    "rollback and refused further repository "
                    "or database mutation."
                ) from rollback_exc

            rollback_error = (
                "Deployment failed and automatic rollback "
                "could not be verified. " + str(rollback_exc)
            )[-12000:]

            transition_deployment_state(
                candidate_id,
                lease_id,
                status="recovery_required",
                phase="rollback_failed",
                error=rollback_error,
            )

            audit(
                "candidate_recovery_required",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={
                    "error": rollback_error[-4000:],
                },
            )

            notify_aaron(
                f"Improvement {candidate_id} requires manual "
                "recovery after a failed deployment rollback.",
                title="Jarvis recovery required",
                config=config,
                env_values=env_values,
            )

        raise


def recover_interrupted_deployment(
    candidate: dict[str, Any],
    config: WorkerConfig,
    env_values: dict[str, str],
) -> str:
    if config.proposal_only:
        raise WorkerError("Deployment recovery is disabled while Proposal Mode is active.")

    candidate_id = int(candidate["candidate_id"])

    failure_id = int(candidate["failure_id"])

    claimed = claim_stale_deployment_recovery(candidate_id)

    if claimed is None:
        return "active"

    lease_id = str(claimed.get("deploy_lease_id") or "")

    if not lease_id:
        raise WorkerError("Recovery claim did not produce a lease.")

    phase = str(claimed.get("deploy_phase") or "").strip()

    base_commit = _normalise_commit_sha(
        claimed.get("base_commit"),
        label="recovery base",
    )

    candidate_commit = _normalise_commit_sha(
        claimed.get("candidate_commit"),
        label="recovery candidate",
    )

    try:
        ensure_repo()

        current_ref = _normalise_commit_sha(
            run(
                [
                    "git",
                    "rev-parse",
                    "HEAD",
                ]
            ).stdout.strip(),
            label="recovery live HEAD",
        )

    except Exception as exc:
        error = (
            "Interrupted deployment could not safely inspect the live repository: " + str(exc)
        )[-12000:]

        transition_deployment_state(
            candidate_id,
            lease_id,
            status="recovery_required",
            phase="inspection_failed",
            error=error,
        )

        audit(
            "candidate_recovery_required",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "error": error[-4000:],
            },
        )

        return "recovery_required"

    if phase not in KNOWN_DEPLOY_PHASES:
        error = (
            "Interrupted deployment has an unknown or "
            f"ambiguous phase: {phase or '[missing]'}. "
            "Automatic deployment or rollback was refused."
        )

        transition_deployment_state(
            candidate_id,
            lease_id,
            status="recovery_required",
            phase="ambiguous_phase",
            error=error,
        )

        audit(
            "candidate_recovery_required",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "reason": "ambiguous_phase",
                "stored_phase": phase,
                "actual_head": current_ref,
            },
        )

        return "recovery_required"

    if current_ref == base_commit:
        if phase in PREMERGE_DEPLOY_PHASES:
            transition_deployment_state(
                candidate_id,
                lease_id,
                status="deploy_requested",
                phase="recovered_requeued",
                error=("Recovered interrupted deployment before the candidate commit was merged."),
            )

            audit(
                "candidate_deployment_requeued",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={
                    "base_commit": base_commit,
                    "interrupted_phase": phase,
                },
            )

            return "requeued"

        # HEAD is already back at the exact validated base while
        # the durable phase proves the candidate had progressed
        # beyond the pre-merge boundary or was already rolling
        # back. Never redeploy it. Rebuild/verify the base and
        # finish the rollback transaction instead.
        try:
            update_deployment_phase(
                candidate_id,
                lease_id,
                "recovery_rebuilding",
            )

            run(
                [
                    "docker",
                    "compose",
                    "up",
                    "-d",
                    "--build",
                ],
                timeout=600,
            )

            update_deployment_phase(
                candidate_id,
                lease_id,
                "recovery_verifying",
            )

            healthy, output = health_check(config.deploy_health_timeout_seconds)

            logs_ok, logs = monitor_logs(30)

            if not healthy or not logs_ok:
                raise WorkerError(
                    "Recovered base did not pass runtime verification.\n" + output + "\n" + logs
                )

            transition_deployment_state(
                candidate_id,
                lease_id,
                status="rolled_back",
                phase="interrupted_rolled_back",
                rolled_back_at=utc_now(),
                error=(
                    "Interrupted deployment was already at "
                    "its validated base and rollback was "
                    "verified."
                ),
            )

            update_failure(
                failure_id,
                status="recorded",
            )

            audit(
                "candidate_interrupted_rolled_back",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={
                    "rollback_ref": base_commit,
                    "interrupted_phase": phase,
                },
            )

            notify_aaron(
                f"Interrupted improvement {candidate_id} was confirmed safely rolled back.",
                title="Jarvis deployment recovered",
                config=config,
                env_values=env_values,
            )

            return "rolled_back"

        except Exception as exc:
            if not deployment_lease_owned(
                candidate_id,
                lease_id,
            ):
                raise WorkerError(
                    "Recovery worker lost its lease and refused further mutation."
                ) from exc

            error = ("Interrupted base recovery verification failed: " + str(exc))[-12000:]

            transition_deployment_state(
                candidate_id,
                lease_id,
                status="recovery_required",
                phase="recovery_failed",
                error=error,
            )

            audit(
                "candidate_recovery_required",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={
                    "error": error[-4000:],
                },
            )

            return "recovery_required"

    if current_ref != candidate_commit:
        error = (
            "Interrupted deployment found an unexpected "
            "live HEAD. Automatic reset was refused. "
            f"expected_base={base_commit} "
            f"expected_candidate={candidate_commit} "
            f"actual={current_ref}"
        )

        transition_deployment_state(
            candidate_id,
            lease_id,
            status="recovery_required",
            phase="unexpected_head",
            error=error,
        )

        audit(
            "candidate_recovery_required",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "reason": "unexpected_head",
                "actual_head": current_ref,
                "interrupted_phase": phase,
            },
        )

        notify_aaron(
            f"Improvement {candidate_id} requires manual "
            "recovery because live Git HEAD is unexpected.",
            title="Jarvis recovery required",
            config=config,
            env_values=env_values,
        )

        return "recovery_required"

    # The exact validated candidate is still live. It is safe to
    # roll back only to its exact stored base. This path is also
    # valid for a crash after git merge but before the phase could
    # be advanced from "merging" to "merged".
    try:
        update_deployment_phase(
            candidate_id,
            lease_id,
            "recovery_rolling_back",
        )

        reset_repository_to_ref(
            base_commit,
            repo_root=ROOT,
        )

        update_deployment_phase(
            candidate_id,
            lease_id,
            "recovery_rebuilding",
        )

        run(
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--build",
            ],
            timeout=600,
        )

        update_deployment_phase(
            candidate_id,
            lease_id,
            "recovery_verifying",
        )

        healthy, output = health_check(config.deploy_health_timeout_seconds)

        logs_ok, logs = monitor_logs(30)

        if not healthy or not logs_ok:
            raise WorkerError(
                "Interrupted deployment rollback did "
                "not pass verification.\n" + output + "\n" + logs
            )

        transition_deployment_state(
            candidate_id,
            lease_id,
            status="rolled_back",
            phase="interrupted_rolled_back",
            rolled_back_at=utc_now(),
            error=("Interrupted deployment was automatically rolled back to its validated base."),
        )

        update_failure(
            failure_id,
            status="recorded",
        )

        audit(
            "candidate_interrupted_rolled_back",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "rollback_ref": base_commit,
                "candidate_commit": candidate_commit,
                "interrupted_phase": phase,
            },
        )

        notify_aaron(
            f"Interrupted improvement {candidate_id} was rolled back safely.",
            title="Jarvis deployment recovered",
            config=config,
            env_values=env_values,
        )

        return "rolled_back"

    except Exception as exc:
        if not deployment_lease_owned(
            candidate_id,
            lease_id,
        ):
            raise WorkerError(
                "Recovery worker lost its lease and refused further mutation."
            ) from exc

        error = ("Interrupted deployment recovery failed: " + str(exc))[-12000:]

        transition_deployment_state(
            candidate_id,
            lease_id,
            status="recovery_required",
            phase="recovery_failed",
            error=error,
        )

        audit(
            "candidate_recovery_required",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "error": error[-4000:],
            },
        )

        notify_aaron(
            f"Improvement {candidate_id} requires manual recovery after an interrupted deployment.",
            title="Jarvis recovery required",
            config=config,
            env_values=env_values,
        )

        return "recovery_required"


def claim_manual_rollback(
    candidate_id: int,
) -> dict[str, Any]:
    ensure_candidate_transaction_columns()

    now = utc_now()

    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")

        row = connection.execute(
            """
            SELECT *
            FROM improvement_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

        if row is None:
            raise WorkerError(f"Candidate {candidate_id} was not found.")

        candidate = dict(row)

        if str(candidate.get("status") or "") != "rollback_requested":
            raise WorkerError("Candidate is not available for manual rollback claiming.")

        cursor = connection.execute(
            """
            UPDATE improvement_candidates
            SET
                status = 'rolling_back',
                updated_at = ?,
                deploy_phase = 'manual_rollback_claimed'
            WHERE candidate_id = ?
              AND status = 'rollback_requested'
            """,
            (
                now,
                candidate_id,
            ),
        )

        if cursor.rowcount != 1:
            raise WorkerError("Manual rollback claim lost a database race.")

        claimed = connection.execute(
            """
            SELECT *
            FROM improvement_candidates
            WHERE candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()

        if claimed is None:
            raise WorkerError("Claimed rollback candidate disappeared.")

        return dict(claimed)


def verify_manual_rollback_binding(
    candidate: dict[str, Any],
) -> dict[str, str]:
    if str(candidate.get("status") or "") != "rolling_back":
        raise WorkerError("Candidate is not in a claimed manual rollback state.")

    base_commit = _normalise_commit_sha(
        candidate.get("base_commit"),
        label="manual rollback base",
    )

    candidate_commit = _normalise_commit_sha(
        candidate.get("candidate_commit"),
        label="manual rollback candidate",
    )

    rollback_ref = _normalise_commit_sha(
        candidate.get("rollback_ref"),
        label="stored rollback",
    )

    if rollback_ref != base_commit:
        raise WorkerError(
            "Stored rollback reference does not match the candidate's exact validated base commit."
        )

    ensure_repo()

    current_ref = _normalise_commit_sha(
        run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ]
        ).stdout.strip(),
        label="manual rollback live HEAD",
    )

    if current_ref == candidate_commit:
        action = "reset_candidate"

    elif current_ref == base_commit:
        action = "already_base"

    else:
        raise WorkerError(
            "Manual rollback found an unexpected live HEAD. "
            "Repository reset was refused. "
            f"expected_candidate={candidate_commit} "
            f"expected_base={base_commit} "
            f"actual={current_ref}"
        )

    return {
        "base_commit": base_commit,
        "candidate_commit": candidate_commit,
        "rollback_ref": rollback_ref,
        "current_ref": current_ref,
        "action": action,
    }


def rollback_candidate(
    candidate: dict[str, Any],
    config: WorkerConfig,
    env_values: dict[str, str],
) -> None:
    if config.proposal_only:
        raise WorkerError("Rollback execution is disabled while Proposal Mode is active.")

    candidate_id = int(candidate["candidate_id"])

    failure_id = int(candidate["failure_id"])

    candidate_status = str(candidate.get("status") or "")
    candidate_phase = str(candidate.get("deploy_phase") or "")

    if candidate_status == "rollback_requested":
        claimed = claim_manual_rollback(candidate_id)

    elif candidate_status == "rolling_back" and candidate_phase in MANUAL_ROLLBACK_ACTIVE_PHASES:
        claimed = fetch_candidate_by_id(candidate_id)

        if claimed is None:
            raise WorkerError("Interrupted manual rollback candidate disappeared.")

        if (
            str(claimed.get("status") or "") != "rolling_back"
            or str(claimed.get("deploy_phase") or "") not in MANUAL_ROLLBACK_ACTIVE_PHASES
        ):
            raise WorkerError(
                "Interrupted manual rollback changed state before recovery could resume."
            )

    else:
        raise WorkerError("Candidate is not available for manual rollback.")

    try:
        binding = verify_manual_rollback_binding(claimed)

    except Exception as exc:
        error = ("Manual rollback safety verification failed. " + str(exc))[-12000:]

        update_candidate(
            candidate_id,
            status="recovery_required",
            deploy_phase="manual_rollback_blocked",
            error=error,
        )

        audit(
            "candidate_manual_rollback_blocked",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "error": error[-4000:],
            },
        )

        notify_aaron(
            f"Manual rollback for improvement {candidate_id} "
            "was blocked because the live repository no "
            "longer matches its exact rollback binding.",
            title="Jarvis rollback blocked",
            config=config,
            env_values=env_values,
        )

        raise

    base_commit = binding["base_commit"]

    candidate_commit = binding["candidate_commit"]

    action = binding["action"]

    audit(
        "candidate_rolling_back",
        failure_id=failure_id,
        candidate_id=candidate_id,
        details={
            "base_commit": base_commit,
            "candidate_commit": candidate_commit,
            "head_state": action,
        },
    )

    try:
        # Re-read HEAD immediately before any destructive
        # action. Human or external Git activity between the
        # first inspection and this point must fail closed.
        pre_action_ref = _normalise_commit_sha(
            run(
                [
                    "git",
                    "rev-parse",
                    "HEAD",
                ]
            ).stdout.strip(),
            label="manual rollback pre-action HEAD",
        )

        if action == "reset_candidate":
            if pre_action_ref != candidate_commit:
                raise WorkerError(
                    "Live HEAD changed after manual rollback "
                    "binding verification. Repository reset "
                    "was refused."
                )

            reset_repository_to_ref(
                base_commit,
                repo_root=ROOT,
            )

        elif action == "already_base":
            if pre_action_ref != base_commit:
                raise WorkerError(
                    "Live HEAD changed after the rollback "
                    "base was verified. Repository mutation "
                    "was refused."
                )

            audit(
                "candidate_manual_rollback_already_at_base",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={
                    "base_commit": base_commit,
                },
            )

        else:
            raise WorkerError("Manual rollback action is invalid.")

        post_git_ref = _normalise_commit_sha(
            run(
                [
                    "git",
                    "rev-parse",
                    "HEAD",
                ]
            ).stdout.strip(),
            label="manual rollback post-Git HEAD",
        )

        if post_git_ref != base_commit:
            raise WorkerError(
                "Manual rollback did not leave the repository on the exact stored base."
            )

    except Exception as exc:
        error = ("Manual rollback Git safety operation failed. " + str(exc))[-12000:]

        update_candidate(
            candidate_id,
            status="recovery_required",
            deploy_phase="manual_rollback_git_failed",
            error=error,
        )

        audit(
            "candidate_manual_rollback_recovery_required",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "error": error[-4000:],
            },
        )

        raise

    update_candidate(
        candidate_id,
        status="rolling_back",
        deploy_phase="manual_rollback_rebuilding",
        error=None,
    )

    run(
        [
            "docker",
            "compose",
            "up",
            "-d",
            "--build",
        ],
        timeout=600,
    )

    update_candidate(
        candidate_id,
        status="rolling_back",
        deploy_phase="manual_rollback_verifying",
    )

    healthy, output = health_check(config.deploy_health_timeout_seconds)

    if not healthy:
        update_candidate(
            candidate_id,
            status="rollback_failed",
            deploy_phase="manual_rollback_verification_failed",
            error=output,
        )

        audit(
            "candidate_manual_rollback_failed",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "error": output[-4000:],
                "rollback_ref": base_commit,
            },
        )

        raise WorkerError(f"Rollback health check failed: {output}")

    final_ref = _normalise_commit_sha(
        run(
            [
                "git",
                "rev-parse",
                "HEAD",
            ]
        ).stdout.strip(),
        label="manual rollback final HEAD",
    )

    if final_ref != base_commit:
        error = (
            "Manual rollback runtime verification passed "
            "but Git HEAD no longer matches the exact "
            "stored base."
        )

        update_candidate(
            candidate_id,
            status="recovery_required",
            deploy_phase="manual_rollback_final_head_changed",
            error=error,
        )

        raise WorkerError(error)

    update_candidate(
        candidate_id,
        status="rolled_back",
        deploy_phase="rolled_back",
        rolled_back_at=utc_now(),
        error=None,
    )

    update_failure(
        failure_id,
        status="recorded",
    )

    audit(
        "candidate_rolled_back",
        failure_id=failure_id,
        candidate_id=candidate_id,
        details={
            "rollback_ref": base_commit,
            "candidate_commit": candidate_commit,
            "head_state": action,
        },
    )

    notify_aaron(
        f"Improvement {candidate_id} was rolled back successfully to its exact validated base.",
        title="Jarvis rollback completed",
        config=config,
        env_values=env_values,
    )


def run_once(
    config: WorkerConfig,
    env_values: dict[str, str],
) -> bool:
    update_setting(
        "worker_heartbeat",
        utc_now(),
    )

    if not improvement_enabled():
        return False

    if not config.proposal_only:
        deploying = fetch_candidate(("deploying",))

        if deploying:
            if deployment_lease_is_expired(deploying):
                recover_interrupted_deployment(
                    deploying,
                    config,
                    env_values,
                )

                return True

            # A non-expired deployment lease represents an
            # in-flight deployment transaction. Do not begin
            # unrelated improvement work until it completes
            # or the lease expires.
            return False

        rollback = fetch_manual_rollback_candidate()

        if rollback:
            rollback_candidate(
                rollback,
                config,
                env_values,
            )

            return True

        deploy = fetch_candidate(("deploy_requested",))

        if deploy:
            deploy_candidate(
                deploy,
                config,
                env_values,
            )

            return True

    queued = fetch_candidate(("queued",))

    if queued:
        process_queued_candidate(
            queued,
            config,
            env_values,
        )

        return True

    return False


@contextlib.contextmanager
def worker_lock() -> Any:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("w", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkerError("Another Jarvis improvement worker is already running.") from exc
        handle.write(str(os.getpid()))
        handle.flush()
        yield


def print_status() -> None:
    with connect() as connection:
        failures = connection.execute(
            "SELECT status, COUNT(*) AS count FROM improvement_failures GROUP BY status"
        ).fetchall()
        candidates = connection.execute(
            "SELECT status, COUNT(*) AS count FROM improvement_candidates GROUP BY status"
        ).fetchall()
    print(
        json.dumps(
            {
                "enabled": improvement_enabled(),
                "proposal_only": load_config()[0].proposal_only,
                "worker_heartbeat": setting("worker_heartbeat", "") or None,
                "failures": {str(row["status"]): int(row["count"]) for row in failures},
                "candidates": {str(row["status"]): int(row["count"]) for row in candidates},
                "database": str(DB_PATH),
            },
            indent=2,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis supervised self-improvement worker")
    parser.add_argument(
        "command", choices=("daemon", "run-once", "status"), nargs="?", default="daemon"
    )
    args = parser.parse_args()
    config, env_values = load_config()

    if args.command == "status":
        print_status()
        return 0

    with worker_lock():
        if args.command == "run-once":
            try:
                worked = run_once(config, env_values)
                print("processed" if worked else "idle")
                return 0
            except Exception as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1

        while True:
            try:
                worked = run_once(config, env_values)
                if not worked:
                    time.sleep(config.poll_seconds)
            except KeyboardInterrupt:
                return 0
            except Exception as exc:
                audit("worker_error", details={"error": str(exc)[-4000:]})
                print(f"Jarvis improvement worker error: {exc}", file=sys.stderr, flush=True)
                time.sleep(max(config.poll_seconds, 30))


if __name__ == "__main__":
    raise SystemExit(main())
