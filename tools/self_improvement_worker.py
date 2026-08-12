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
from dataclasses import dataclass
from datetime import datetime, timezone
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
    branch_result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    current_branch = branch_result.stdout.strip() or "main"
    return (
        WorkerConfig(
            model=model,
            poll_seconds=env_int(values, "JARVIS_IMPROVEMENT_POLL_SECONDS", 15, 5, 300),
            max_attempts_per_day=env_int(values, "JARVIS_IMPROVEMENT_MAX_ATTEMPTS_PER_DAY", 3, 1, 20),
            max_patch_lines=env_int(values, "JARVIS_IMPROVEMENT_MAX_PATCH_LINES", 450, 40, 3000),
            max_changed_files=env_int(values, "JARVIS_IMPROVEMENT_MAX_CHANGED_FILES", 5, 1, 20),
            github_enabled=env_bool(values, "JARVIS_IMPROVEMENT_GITHUB_ENABLED", False),
            ai_review_enabled=env_bool(values, "JARVIS_IMPROVEMENT_AI_REVIEW_ENABLED", True),
            notify_enabled=env_bool(values, "JARVIS_IMPROVEMENT_NOTIFY_ENABLED", True),
            notify_service=values.get("JARVIS_IMPROVEMENT_NOTIFY_SERVICE", "notify.mobile_app_aaron_s_phone").strip(),
            auto_deploy_low_risk=env_bool(values, "JARVIS_IMPROVEMENT_AUTO_DEPLOY_LOW_RISK", False),
            proposal_only=env_bool(
                values,
                "JARVIS_IMPROVEMENT_PROPOSAL_ONLY",
                True,
            ),
            candidate_timeout_seconds=env_int(values, "JARVIS_IMPROVEMENT_CANDIDATE_TIMEOUT_SECONDS", 600, 60, 3600),
            deploy_health_timeout_seconds=env_int(values, "JARVIS_IMPROVEMENT_DEPLOY_HEALTH_TIMEOUT_SECONDS", 90, 20, 600),
            base_branch=values.get("JARVIS_IMPROVEMENT_BASE_BRANCH", current_branch).strip() or current_branch,
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
    today = datetime.now(timezone.utc).date().isoformat()
    with connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count FROM improvement_candidates
            WHERE substr(created_at, 1, 10) = ?
              AND status NOT IN ('queued')
            """,
            (today,),
        ).fetchone()
    return int(row["count"] if row else 0)


def infer_context_files(failure: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    category = str(failure.get("category") or "general")
    category_map = policy.get("context_files_by_category", {})
    selected = category_map.get(category) or category_map.get("general") or []
    allowed = policy.get("allowed_context_paths", ["bridge/app/*.py", "bridge/tests/*.py"])
    result: list[str] = []
    for value in selected:
        path = str(value)
        if path_matches(path, allowed) and (ROOT / path).is_file():
            result.append(path)
    return result[: int(policy.get("max_context_files", 6))]


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


def build_context(failure: dict[str, Any], policy: dict[str, Any]) -> tuple[str, list[str]]:
    files = infer_context_files(failure, policy)
    max_chars = int(policy.get("max_context_characters", 180000))
    sections: list[str] = []
    used = 0
    for path in files:
        content = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        content = redact(content)
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n# [TRUNCATED BY JARVIS IMPROVER]\n"
        sections.append(f"\n===== FILE: {path} =====\n{content}")
        used += len(content)
    return "".join(sections), files


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
    evidence = redact(json.dumps(failure.get("evidence", {}), ensure_ascii=False, indent=2, default=str))
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
                "required_changes": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["verdict", "risk", "summary", "findings", "required_changes"],
            "additionalProperties": False
        }
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
        "max_output_tokens": 4000
    }
    if config.model.lower().startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": "high"}
        kwargs["text"] = {"verbosity": "low"}
    response = client.responses.create(**kwargs)
    review = parse_tool_arguments(response, "submit_review")
    review["enabled"] = True
    review["response_id"] = str(getattr(response, "id", "") or "")
    return review


def parse_patch_arguments(response: Any) -> dict[str, Any]:
    return parse_tool_arguments(response, "submit_patch")


def request_patch(
    *,
    failure: dict[str, Any],
    context: str,
    context_files: list[str],
    policy: dict[str, Any],
    config: WorkerConfig,
    env_values: dict[str, str],
    previous_error: str | None = None,
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
Generate the smallest safe patch that fixes the recorded failure and adds a regression test.

Failure ID: {failure['failure_id']}
Category: {failure.get('category')}
Severity: {failure.get('severity')}
Occurrences: {failure.get('occurrences')}
Summary: {failure.get('summary')}

Redacted evidence:
{failure_json}

Files supplied for context: {context_files}
Allowed edit path patterns: {allowed_edit_paths}
Forbidden paths: {forbidden_paths}

Requirements:
- Preserve all existing working Jarvis features.
- Never add credentials, tokens, network backdoors, shell execution, eval/exec, or Docker socket access.
- Never edit .env, data, logs, authentication, systemd, Docker daemon settings, or Home Assistant tokens.
- Never weaken room/device safety, Admin Mode confirmation, user identity separation, or tool verification.
- Add or update a focused pytest regression test.
- Keep the patch under {config.max_patch_lines} changed lines and {config.max_changed_files} files.
- Return a standard unified Git diff rooted at the repository, using paths such as a/bridge/app/file.py and b/bridge/app/file.py.
- Do not include prose inside the patch.
""".strip()
    if previous_error:
        prompt += f"\n\nThe previous patch attempt failed validation:\n{redact(previous_error)[-5000:]}\nRepair it."

    instructions = """
Act as a conservative senior Python engineer and safety reviewer. You may only submit a patch through the submit_patch tool. Prefer deterministic fixes over prompt-only changes. A candidate must include a regression test and must not modify forbidden paths. Do not claim tests passed; the local worker will verify them.
""".strip()

    tool = {
        "type": "function",
        "name": "submit_patch",
        "description": "Submit one bounded Jarvis code patch for local validation.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "root_cause": {"type": "string"},
                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "patch": {"type": "string"},
                "tests_added": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "notes": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["summary", "root_cause", "risk", "patch", "tests_added", "notes"],
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
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": prompt + context}]}
        ],
        "tools": [tool],
        "tool_choice": {"type": "function", "name": "submit_patch"},
        "parallel_tool_calls": False,
        "store": False,
        "max_output_tokens": 16000,
    }
    if config.model.lower().startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": "high"}
        kwargs["text"] = {"verbosity": "medium"}
    response = client.responses.create(**kwargs)
    payload = parse_patch_arguments(response)
    usage_obj = getattr(response, "usage", None)
    usage = {
        "input_tokens": int(getattr(usage_obj, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage_obj, "output_tokens", 0) or 0),
        "response_id": str(getattr(response, "id", "") or ""),
    }
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


def validate_patch_policy(patch: str, policy: dict[str, Any], config: WorkerConfig) -> tuple[list[str], str]:
    paths = patch_paths(patch)
    if not paths:
        raise WorkerError("The model did not return a valid unified diff.")
    if len(paths) > config.max_changed_files:
        raise WorkerError(f"Patch changes {len(paths)} files; policy allows {config.max_changed_files}.")
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
        re.compile(pattern, re.I)
        for pattern in policy.get("forbidden_added_patterns", [])
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


def create_worktree(candidate_id: int, branch_name: str) -> Path:
    workspace = WORKTREES / str(candidate_id)
    if workspace.exists():
        run(["git", "worktree", "remove", "--force", str(workspace)], check=False)
        shutil.rmtree(workspace, ignore_errors=True)
    run(["git", "branch", "-D", branch_name], check=False)
    WORKTREES.mkdir(parents=True, exist_ok=True)
    run(["git", "worktree", "add", "-b", branch_name, str(workspace), "HEAD"])
    return workspace


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


def command_result(name: str, completed: subprocess.CompletedProcess[str], blocking: bool = True) -> dict[str, Any]:
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
    filename = str(
        value or ""
    ).replace(
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
        _normalise_bandit_filename(
            issue.get(
                "filename"
            )
        ),
        str(
            issue.get(
                "test_id"
            )
            or ""
        ),
        str(
            issue.get(
                "issue_text"
            )
            or ""
        ),
        str(
            issue.get(
                "issue_severity"
            )
            or ""
        ).upper(),
        str(
            issue.get(
                "issue_confidence"
            )
            or ""
        ).upper(),
    )


def _parse_bandit_json(
    output: str,
) -> list[dict[str, Any]] | None:
    text = str(
        output or ""
    ).strip()

    candidates = [
        text,
    ]

    first = text.find(
        "{"
    )

    last = text.rfind(
        "}"
    )

    if (
        first >= 0
        and last >= first
    ):
        candidates.append(
            text[
                first : last + 1
            ]
        )

    for candidate in candidates:
        try:
            payload = json.loads(
                candidate
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        results = payload.get(
            "results"
        )

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
            str(
                VENV_PYTHON
            ),
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
        _parse_bandit_json(
            completed.stdout
        ),
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

    baseline_completed, baseline_issues = (
        _run_bandit_json(
            ROOT
        )
    )

    candidate_completed, candidate_issues = (
        _run_bandit_json(
            workspace
        )
    )

    if baseline_issues is None:
        return {
            "name": "bandit_baseline",
            "passed": False,
            "blocking": True,
            "returncode": (
                baseline_completed.returncode
            ),
            "baseline_findings": None,
            "candidate_findings": None,
            "new_findings": [],
            "fixed_findings": None,
            "output": (
                "Unable to parse or execute the "
                "production Bandit baseline.\n"
                + baseline_completed.stdout[
                    -8000:
                ]
            ),
        }

    if candidate_issues is None:
        return {
            "name": "bandit_baseline",
            "passed": False,
            "blocking": True,
            "returncode": (
                candidate_completed.returncode
            ),
            "baseline_findings": len(
                baseline_issues
            ),
            "candidate_findings": None,
            "new_findings": [],
            "fixed_findings": None,
            "output": (
                "Unable to parse or execute the "
                "candidate Bandit scan.\n"
                + candidate_completed.stdout[
                    -8000:
                ]
            ),
        }

    baseline_counts = Counter(
        _bandit_issue_key(
            issue
        )
        for issue in baseline_issues
    )

    candidate_counts = Counter(
        _bandit_issue_key(
            issue
        )
        for issue in candidate_issues
    )

    new_counts = (
        candidate_counts
        - baseline_counts
    )

    fixed_counts = (
        baseline_counts
        - candidate_counts
    )

    remaining = Counter(
        new_counts
    )

    new_findings: list[
        dict[str, Any]
    ] = []

    for issue in candidate_issues:
        key = _bandit_issue_key(
            issue
        )

        if remaining.get(
            key,
            0,
        ) <= 0:
            continue

        new_findings.append(
            {
                "filename": (
                    _normalise_bandit_filename(
                        issue.get(
                            "filename"
                        )
                    )
                ),
                "test_id": issue.get(
                    "test_id"
                ),
                "issue_text": issue.get(
                    "issue_text"
                ),
                "severity": issue.get(
                    "issue_severity"
                ),
                "confidence": issue.get(
                    "issue_confidence"
                ),
                "line_number": issue.get(
                    "line_number"
                ),
            }
        )

        remaining[
            key
        ] -= 1

    new_count = sum(
        new_counts.values()
    )

    fixed_count = sum(
        fixed_counts.values()
    )

    passed = (
        new_count == 0
    )

    return {
        "name": "bandit_baseline",
        "passed": passed,
        "blocking": True,
        "returncode": (
            0
            if passed
            else 1
        ),
        "baseline_findings": len(
            baseline_issues
        ),
        "candidate_findings": len(
            candidate_issues
        ),
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


def run_validation(workspace: Path, policy: dict[str, Any], config: WorkerConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    results: list[dict[str, Any]] = []

    commands: list[tuple[str, list[str], bool, int]] = [
        ("git_diff_check", ["git", "diff", "--check"], True, 60),
        ("compileall", [str(VENV_PYTHON), "-m", "compileall", "-q", "bridge/app"], True, 180),
        ("pytest", [str(VENV_PYTHON), "-m", "pytest", "-q", "bridge/tests"], True, config.candidate_timeout_seconds),
        ("ruff", [str(VENV_PYTHON), "-m", "ruff", "check", "bridge/app", "bridge/tests"], True, 240),
    ]
    for name, command, blocking, timeout in commands:
        completed = run(command, cwd=workspace, timeout=timeout, check=False)
        results.append(command_result(name, completed, blocking))

    results.append(
        bandit_baseline_result(
            workspace
        )
    )

    if (workspace / "bridge/requirements.txt").exists():
        completed = run(
            [str(VENV_PYTHON), "-m", "pip_audit", "-r", "bridge/requirements.txt", "--progress-spinner", "off"],
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


def docker_smoke_test(workspace: Path, candidate_id: int, timeout: int) -> dict[str, Any]:
    image = f"jarvis-candidate:{candidate_id}"
    container = f"jarvis-candidate-{candidate_id}"
    temp_root = Path(tempfile.mkdtemp(prefix=f"jarvis-candidate-{candidate_id}-", dir=WORK_ROOT))
    for name in ("data", "logs", "config", "tmp"):
        (temp_root / name).mkdir(parents=True, exist_ok=True)
    try:
        build = run(["docker", "build", "-t", image, "bridge"], cwd=workspace, timeout=timeout, check=False)
        if build.returncode != 0:
            return {"passed": False, "stage": "build", "output": build.stdout[-12000:]}

        run_command = [
            "docker", "run", "-d", "--rm",
            "--name", container,
            "--network", "none",
            "--read-only",
            "--security-opt", "no-new-privileges:true",
            "--cap-drop", "ALL",
            "--memory", "512m",
            "--cpus", "1.0",
            "--pids-limit", "128",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "-v", f"{temp_root / 'data'}:/app/data:rw",
            "-v", f"{temp_root / 'logs'}:/app/logs:rw",
            "-v", f"{temp_root / 'config'}:/app/config:rw",
            "-e", "OPENAI_API_KEY=dummy",
            "-e", "OPENAI_MODEL=gpt-5-mini",
            "-e", "HOME_ASSISTANT_URL=http://127.0.0.1:9",
            "-e", "HOME_ASSISTANT_TOKEN=dummy",
            "-e", "JARVIS_ADMIN_MODE_ENABLED=false",
            "-e", "JARVIS_AWARENESS_ENABLED=false",
            "-e", "JARVIS_SELF_IMPROVEMENT_ENABLED=false",
            image,
        ]
        started = run(run_command, cwd=workspace, timeout=60, check=False)
        if started.returncode != 0:
            return {"passed": False, "stage": "start", "output": started.stdout[-12000:]}

        deadline = time.monotonic() + min(timeout, 120)
        last_output = ""
        while time.monotonic() < deadline:
            check = run(
                [
                    "docker", "exec", container, "python", "-c",
                    "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read().decode())",
                ],
                cwd=workspace,
                timeout=10,
                check=False,
            )
            last_output = check.stdout
            if check.returncode == 0 and '"status":"healthy"' in check.stdout.replace(" ", ""):
                return {"passed": True, "stage": "health", "output": check.stdout[-4000:]}
            time.sleep(2)
        logs = run(["docker", "logs", container], cwd=workspace, timeout=20, check=False).stdout
        return {"passed": False, "stage": "health", "output": (last_output + "\n" + logs)[-12000:]}
    finally:
        run(["docker", "rm", "-f", container], cwd=workspace, timeout=30, check=False)
        shutil.rmtree(temp_root, ignore_errors=True)


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
    message = f"Jarvis improvement {candidate_id}: {summary[:72]}\n\nFixes recorded failure {failure_id}."
    run(["git", "commit", "-m", message], cwd=workspace)
    return run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()


def maybe_create_pr(workspace: Path, branch: str, candidate_id: int, summary: str, config: WorkerConfig) -> str | None:
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
            "gh", "pr", "create", "--draft", "--base", config.base_branch,
            "--head", branch, "--title", f"Jarvis improvement {candidate_id}: {summary[:60]}",
            "--body", body,
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


def process_queued_candidate(candidate: dict[str, Any], config: WorkerConfig, env_values: dict[str, str]) -> None:
    candidate_id = int(candidate["candidate_id"])
    failure_id = int(candidate["failure_id"])
    if attempts_today() >= config.max_attempts_per_day:
        return
    if not improvement_enabled():
        return

    policy = load_policy()
    ensure_repo()
    failure = fetch_failure(failure_id)
    branch = f"jarvis/improvement-{candidate_id}"
    update_candidate(candidate_id, status="generating", model=config.model, branch_name=branch, error=None)
    audit("candidate_generation_started", failure_id=failure_id, candidate_id=candidate_id, details={"model": config.model})

    context, context_files = build_context(failure, policy)
    generation_error: str | None = None
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
            )
            patch = str(payload.get("patch") or "")
            try:
                paths, patch_hash = validate_patch_policy(patch, policy, config)
                workspace = create_worktree(candidate_id, branch)
                patch_path = apply_patch(workspace, patch, candidate_id)
                break
            except Exception as exc:
                generation_error = str(exc)
                if workspace is not None:
                    run(["git", "worktree", "remove", "--force", str(workspace)], check=False)
                    workspace = None
                if attempt >= 2:
                    raise
        if payload is None or workspace is None:
            raise WorkerError("No candidate patch was generated.")

        tests, security = run_validation(workspace, policy, config)
        smoke = docker_smoke_test(workspace, candidate_id, config.candidate_timeout_seconds)
        tests["candidate_container"] = smoke
        tests["passed"] = bool(tests.get("passed")) and bool(smoke.get("passed"))
        if not tests["passed"] or not security.get("passed"):
            raise WorkerError(
                "Candidate validation failed.\n"
                + json.dumps({"tests": tests, "security": security}, ensure_ascii=False, indent=2)[-12000:]
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
        commit_sha = commit_candidate(workspace, candidate_id, failure_id, summary)
        pr_url = maybe_create_pr(workspace, branch, candidate_id, summary, config)
        approval_code = f"{secrets.randbelow(900000) + 100000:06d}"
        diff_stats = {
            "changed_files": len(paths),
            "changed_lines": patch_line_count(str(payload.get("patch") or "")),
            "patch_sha256": patch_hash,
            "commit_sha": commit_sha,
            "context_files": context_files,
            "tests_added": payload.get("tests_added", []),
            "notes": payload.get("notes", []),
        }
        next_status = "awaiting_approval"

        if (
            not config.proposal_only
            and risk == "low"
            and config.auto_deploy_low_risk
            and policy.get(
                "allow_auto_deploy_low_risk",
                False,
            )
        ):
            next_status = "deploy_requested"
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
            pr_url=pr_url,
            error=None,
            deploy_requested_at=utc_now() if next_status == "deploy_requested" else None,
        )
        update_failure(failure_id, status="candidate_ready")
        audit(
            "candidate_ready",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "summary": summary,
                "risk": risk,
                "approval_code": approval_code,
                "pr_url": pr_url,
            },
        )
        notify_aaron(
            f"Improvement {candidate_id} passed isolated testing ({risk} risk). "
            f"{summary} To deploy, say: Deploy improvement {candidate_id} code {approval_code}.",
            title="Jarvis improvement ready",
            config=config,
            env_values=env_values,
        )
    except Exception as exc:
        update_candidate(candidate_id, status="failed", error=str(exc)[-12000:])
        update_failure(failure_id, status="recorded")
        audit("candidate_failed", failure_id=failure_id, candidate_id=candidate_id, details={"error": str(exc)[-4000:]})
        notify_aaron(
            f"Improvement {candidate_id} failed isolated validation and was not deployed.",
            title="Jarvis improvement failed",
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
    logs = run(["docker", "compose", "logs", "--since", f"{seconds}s", "--tail", "300", "jarvis-core"], timeout=60, check=False).stdout
    bad = re.search(r"(?i)(traceback|syntaxerror|importerror|critical|application startup failed)", logs)
    return bad is None, logs[-12000:]


def deploy_candidate(candidate: dict[str, Any], config: WorkerConfig, env_values: dict[str, str]) -> None:
    if config.proposal_only:
        raise WorkerError(
            "Deployment is disabled while Proposal Mode is active."
        )

    candidate_id = int(candidate["candidate_id"])
    failure_id = int(candidate["failure_id"])
    workspace = Path(str(candidate.get("workspace_path") or ""))
    branch = str(candidate.get("branch_name") or "")
    if not workspace.exists() or not branch:
        raise WorkerError("Candidate workspace or branch is missing.")
    ensure_repo()
    current_ref = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    branch_ref = run(["git", "rev-parse", branch]).stdout.strip()
    merge_base = run(["git", "merge-base", "HEAD", branch]).stdout.strip()
    if merge_base != current_ref:
        raise WorkerError(
            "The live branch changed after this candidate was created. Regenerate the candidate against the current code."
        )

    update_candidate(candidate_id, status="deploying", rollback_ref=current_ref)
    audit("candidate_deploying", failure_id=failure_id, candidate_id=candidate_id, details={"rollback_ref": current_ref})
    try:
        run(["git", "merge", "--ff-only", branch])
        run(["docker", "compose", "up", "-d", "--build"], timeout=600)
        healthy, health_output = health_check(config.deploy_health_timeout_seconds)
        logs_ok, logs = monitor_logs(30)
        if not healthy or not logs_ok:
            raise WorkerError(
                "Deployment health verification failed.\n"
                + health_output
                + "\n"
                + logs
            )
        update_candidate(candidate_id, status="deployed", deployed_at=utc_now(), error=None)
        update_failure(failure_id, status="deployed")
        audit("candidate_deployed", failure_id=failure_id, candidate_id=candidate_id, details={"commit": branch_ref})
        notify_aaron(
            f"Improvement {candidate_id} deployed successfully and passed health checks.",
            title="Jarvis updated",
            config=config,
            env_values=env_values,
        )
    except Exception as exc:
        run(["git", "reset", "--hard", current_ref], check=False)
        run(["docker", "compose", "up", "-d", "--build"], timeout=600, check=False)
        update_candidate(candidate_id, status="rolled_back", rolled_back_at=utc_now(), error=str(exc)[-12000:])
        update_failure(failure_id, status="recorded")
        audit("candidate_auto_rolled_back", failure_id=failure_id, candidate_id=candidate_id, details={"error": str(exc)[-4000:]})
        notify_aaron(
            f"Improvement {candidate_id} failed deployment checks and was rolled back automatically.",
            title="Jarvis rollback completed",
            config=config,
            env_values=env_values,
        )
        raise


def rollback_candidate(candidate: dict[str, Any], config: WorkerConfig, env_values: dict[str, str]) -> None:
    if config.proposal_only:
        raise WorkerError(
            "Rollback execution is disabled while Proposal Mode is active."
        )

    candidate_id = int(candidate["candidate_id"])
    failure_id = int(candidate["failure_id"])
    rollback_ref = str(candidate.get("rollback_ref") or "")
    if not rollback_ref:
        raise WorkerError("No rollback reference is stored for this candidate.")
    ensure_repo()
    update_candidate(candidate_id, status="rolling_back")
    audit("candidate_rolling_back", failure_id=failure_id, candidate_id=candidate_id)
    run(["git", "reset", "--hard", rollback_ref])
    run(["docker", "compose", "up", "-d", "--build"], timeout=600)
    healthy, output = health_check(config.deploy_health_timeout_seconds)
    if not healthy:
        update_candidate(candidate_id, status="rollback_failed", error=output)
        raise WorkerError(f"Rollback health check failed: {output}")
    update_candidate(candidate_id, status="rolled_back", rolled_back_at=utc_now(), error=None)
    update_failure(failure_id, status="recorded")
    audit("candidate_rolled_back", failure_id=failure_id, candidate_id=candidate_id)
    notify_aaron(
        f"Improvement {candidate_id} was rolled back successfully.",
        title="Jarvis rollback completed",
        config=config,
        env_values=env_values,
    )


def run_once(config: WorkerConfig, env_values: dict[str, str]) -> bool:
    update_setting("worker_heartbeat", utc_now())
    if not improvement_enabled():
        return False

    # Proposal Mode is deliberately one-way: it may inspect,
    # generate, patch and validate isolated worktrees, but it may
    # never alter the live branch or restart production services.
    if not config.proposal_only:
        rollback = fetch_candidate(
            ("rollback_requested",)
        )

        if rollback:
            rollback_candidate(
                rollback,
                config,
                env_values,
            )
            return True

        deploy = fetch_candidate(
            ("deploy_requested",)
        )

        if deploy:
            deploy_candidate(
                deploy,
                config,
                env_values,
            )
            return True

    queued = fetch_candidate(("queued",))
    if queued:
        process_queued_candidate(queued, config, env_values)
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
    print(json.dumps({
        "enabled": improvement_enabled(),
        "proposal_only": load_config()[0].proposal_only,
        "worker_heartbeat": setting("worker_heartbeat", "") or None,
        "failures": {str(row["status"]): int(row["count"]) for row in failures},
        "candidates": {str(row["status"]): int(row["count"]) for row in candidates},
        "database": str(DB_PATH),
    }, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="Jarvis supervised self-improvement worker")
    parser.add_argument("command", choices=("daemon", "run-once", "status"), nargs="?", default="daemon")
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
