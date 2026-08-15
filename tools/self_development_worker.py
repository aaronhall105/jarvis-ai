#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import self_improvement_worker as base

STATE_VERSION = 5
DEFAULT_MAX_REPAIRS = 4
DEFAULT_MAX_STEP_RETRIES = 3
DEVELOPMENT_FEEDBACK_LIMIT = 24000


class ExternalDependencyBlocked(base.WorkerError):
    """A development dependency is unavailable; this must not consume repair budget."""


def _is_external_credit_block(exc: BaseException | str) -> bool:
    text = str(exc or "").casefold()
    markers = (
        "credit_balance_exhausted",
        "insufficient_quota",
        "no credits remaining",
        "hit your billing quota",
    )
    return any(marker in text for marker in markers)


def _external_dependency_feedback(exc: BaseException | str) -> str:
    return _clean_development_feedback(
        "OpenAI API credit/billing dependency is unavailable. Development is paused; "
        "no development attempt was consumed.\n" + str(exc or "")
    )


def _env_int(values: dict[str, str], name: str, default: int, low: int, high: int) -> int:
    raw = values.get(name, os.getenv(name, ""))
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(low, min(high, value))


def _state(candidate: dict[str, Any]) -> dict[str, Any]:
    raw = candidate.get("usage_json")
    if isinstance(raw, str):
        payload = base.json_load(raw, {})
    elif isinstance(raw, dict):
        payload = raw
    else:
        payload = {}
    state = payload.get("development") if isinstance(payload, dict) else None
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", STATE_VERSION)
    state.setdefault("attempts", [])
    state.setdefault("acceptance_criteria", [])
    state.setdefault("test_strategy", [])
    state.setdefault("nonbudget_events", [])
    state.setdefault("step_retries", [])
    return state


def _save_state(candidate_id: int, state: dict[str, Any], **fields: Any) -> None:
    base.update_candidate(candidate_id, usage_json={"development": state}, **fields)


def _redact_development_feedback(text: str) -> str:
    """Redact credential-shaped values without destroying pytest node IDs or symbols.

    The legacy generic scrubber intentionally hides any long identifier-like token.
    That is appropriate for broad logs, but pytest node IDs and long function names are
    critical repair evidence. Development feedback therefore uses narrower secret
    patterns while preserving ordinary source/test identifiers.
    """
    patterns = (
        re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"),
        re.compile(
            r"(?i)((?:api[_ -]?key|x-api-key|token|password|secret|pin)\s*[:=]\s*)\S+"
        ),
        re.compile(r"(?i)(https?://[^\s:@/]+:)[^@\s/]+@"),
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    )
    output = str(text or "")
    for pattern in patterns:
        if pattern.groups:
            output = pattern.sub(lambda match: match.group(1) + "[REDACTED]", output)
        else:
            output = pattern.sub("[REDACTED]", output)
    return output


def _clean_development_feedback(text: str) -> str:
    return _redact_development_feedback(text)[-DEVELOPMENT_FEEDBACK_LIMIT:]


def _call_tool(
    *,
    name: str,
    tool: dict[str, Any],
    instructions: str,
    prompt: str,
    config: base.WorkerConfig,
    env_values: dict[str, str],
    max_output_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    api_key = env_values.get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise base.WorkerError("OPENAI_API_KEY is unavailable to the development worker.")
    if base.OpenAI is None:
        raise base.WorkerError("The OpenAI package is missing from the improvement worker environment.")

    request_timeout = float(
        os.getenv("JARVIS_DEVELOPMENT_MODEL_TIMEOUT_SECONDS", "90")
    )
    request_timeout = max(15.0, min(request_timeout, 300.0))
    client = base.OpenAI(
        api_key=api_key,
        timeout=request_timeout,
        max_retries=0,
    )
    kwargs: dict[str, Any] = {
        "model": config.model,
        "instructions": instructions,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        "tools": [tool],
        "tool_choice": {"type": "function", "name": name},
        "parallel_tool_calls": False,
        "store": False,
        "max_output_tokens": max_output_tokens,
    }
    if config.model.lower().startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": "high"}
        kwargs["text"] = {"verbosity": "medium"}

    try:
        response = client.responses.create(**kwargs)
    except Exception as exc:
        if _is_external_credit_block(exc):
            raise ExternalDependencyBlocked(
                _external_dependency_feedback(exc)
            ) from exc

        timeout_names = {
            "APITimeoutError",
            "TimeoutException",
            "ReadTimeout",
            "WriteTimeout",
            "ConnectTimeout",
            "PoolTimeout",
        }
        error_text = str(exc).lower()
        if (
            exc.__class__.__name__ in timeout_names
            or "timed out" in error_text
            or "timeout" in error_text
        ):
            raise ExternalDependencyBlocked(
                "Development model request timed out. "
                "Candidate state was preserved and no internal "
                "development retry was consumed."
            ) from exc

        raise
    usage = base._require_completed_response(response, purpose=f"Development {name}")
    return base.parse_tool_arguments(response, name), usage


def _request_text(failure: dict[str, Any]) -> str:
    evidence = failure.get("evidence") or {}
    source = evidence.get("source") or {}
    return str(
        source.get("raw_text")
        or evidence.get("correction")
        or failure.get("summary")
        or ""
    ).strip()


def _context_paths(
    failure: dict[str, Any], workspace: Path, policy: dict[str, Any], feedback: str
) -> list[str]:
    selected = list(base.infer_context_files(failure, policy))
    changed = base.run(
        ["git", "diff", "--name-only", "HEAD", "--"], cwd=workspace, check=False
    ).stdout.splitlines()
    for raw in changed:
        path = raw.strip()
        if path and path not in selected:
            selected.append(path)
    for path in re.findall(r"(bridge/(?:app|tests)/[A-Za-z0-9_./-]+\.py)", feedback):
        if path not in selected:
            selected.append(path)

    allowed = policy.get("allowed_context_paths", ["bridge/app/*.py", "bridge/tests/*.py"])
    limit = max(int(policy.get("max_context_files", 6)), 8)
    result: list[str] = []
    for path in selected:
        target = workspace / path
        if (
            path not in result
            and base.path_matches(path, allowed)
            and target.is_file()
            and not target.is_symlink()
        ):
            result.append(path)
        if len(result) >= limit:
            break
    return result


def _workspace_context(
    failure: dict[str, Any], workspace: Path, policy: dict[str, Any], feedback: str = ""
) -> tuple[str, list[str]]:
    paths = _context_paths(failure, workspace, policy, feedback)
    if not paths:
        return "", []

    max_chars = int(policy.get("max_context_characters", 180000))
    terms = base._context_search_terms(failure)
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", feedback):
        folded = token.casefold()
        if folded not in terms:
            terms.append(folded)
        if len(terms) >= 64:
            break

    per_file = max(6000, (max_chars // len(paths)) - 256)
    sections: list[str] = []
    included: list[str] = []
    for path in paths:
        content = (workspace / path).read_text(encoding="utf-8", errors="replace")
        excerpt = base._relevant_context_excerpt(_redact_development_feedback(content), terms, per_file)
        section = f"\n===== CURRENT WORKSPACE FILE: {path} =====\n{excerpt}"
        if sum(len(item) for item in sections) + len(section) > max_chars:
            continue
        sections.append(section)
        included.append(path)
    return "".join(sections), included


def _plan(
    failure: dict[str, Any],
    workspace: Path,
    policy: dict[str, Any],
    config: base.WorkerConfig,
    env_values: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    context, files = _workspace_context(failure, workspace, policy)
    prompt = f"""
You are planning one autonomous Jarvis self-development session.

User goal:
{base.redact(_request_text(failure))}

Relevant repository files: {files}

Authoritative current source:
{context}

Create observable acceptance criteria and a concrete test strategy. The coding
agent will work iteratively in one isolated Git worktree until these criteria
are satisfied or its bounded repair budget is exhausted. Preserve all existing
security, Admin Mode, identity, device/tool safety, patch policy, validation,
review, human approval, deployment and rollback controls. Do not write code in
this planning pass.
""".strip()
    tool = {
        "type": "function",
        "name": "submit_development_plan",
        "description": "Submit a bounded self-development plan.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "investigation": {"type": "array", "items": {"type": "string"}},
                "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                "likely_files": {"type": "array", "items": {"type": "string"}},
                "test_strategy": {"type": "array", "items": {"type": "string"}},
                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": [
                "summary", "investigation", "acceptance_criteria",
                "likely_files", "test_strategy", "risk"
            ],
            "additionalProperties": False,
        },
    }
    return _call_tool(
        name="submit_development_plan",
        tool=tool,
        instructions=(
            "Act as a senior software architect. Convert the user's goal into a "
            "testable acceptance contract. Do not weaken Jarvis safety controls."
        ),
        prompt=prompt,
        config=config,
        env_values=env_values,
        max_output_tokens=12000,
    )


def _development_step(
    failure: dict[str, Any],
    state: dict[str, Any],
    workspace: Path,
    policy: dict[str, Any],
    config: base.WorkerConfig,
    env_values: dict[str, str],
    feedback: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    context, files = _workspace_context(failure, workspace, policy, feedback)
    current_diff = base.run(
        ["git", "diff", "--no-ext-diff", "--unified=3", "HEAD", "--"],
        cwd=workspace,
        check=False,
    ).stdout

    current_changed_lines = 0
    numstat = base.run(
        ["git", "diff", "--numstat", "HEAD", "--"],
        cwd=workspace,
        check=False,
    ).stdout.splitlines()
    for line in numstat:
        parts = line.split("\t", 2)
        if (
            len(parts) >= 2
            and parts[0].isdigit()
            and parts[1].isdigit()
        ):
            current_changed_lines += int(parts[0]) + int(parts[1])

    max_patch_lines = int(config.max_patch_lines)
    remaining_patch_lines = max_patch_lines - current_changed_lines
    safe_target_lines = max(0, max_patch_lines - 10)
    reduction_to_target = max(
        0,
        current_changed_lines - safe_target_lines,
    )

    prompt = f"""
You are the implementation agent inside one persistent Jarvis development task.
Do not start over. Inspect the CURRENT worktree, preserve good changes already
present, and make only the next exact edits required to satisfy the acceptance
contract.

User goal:
{base.redact(_request_text(failure))}

Plan summary:
{base.redact(str(state.get('summary') or ''))}

Acceptance criteria:
{json.dumps(state.get('acceptance_criteria', []), ensure_ascii=False, indent=2)}

Test strategy:
{json.dumps(state.get('test_strategy', []), ensure_ascii=False, indent=2)}

Latest feedback from tests/review:
{_redact_development_feedback(feedback)[-DEVELOPMENT_FEEDBACK_LIMIT:] if feedback else 'Initial implementation pass.'}

CURRENT PATCH BUDGET:
- Current complete candidate patch: {current_changed_lines} changed lines.
- Maximum permitted patch: {max_patch_lines} changed lines.
- Remaining additive budget: {remaining_patch_lines} changed lines.
- Safe target: <= {safe_target_lines} changed lines.
- Reduction required to reach safe target: {reduction_to_target} changed lines.

PATCH-BUDGET RULES:
- The limit applies to the COMPLETE resulting Git diff, additions plus deletions,
  not merely to the edits returned in this response.
- Recalculate your solution around the existing work rather than appending a
  second implementation.
- If remaining additive budget is small or negative, reduce or consolidate
  existing candidate code/tests before adding anything.
- Any new changed lines must be offset by removing or consolidating at least
  the same number of existing changed lines when necessary.
- Prefer modifying existing regression tests over adding duplicate tests.
- Prefer simplifying existing candidate code over introducing another helper
  or parallel code path.
- Aim for the safe target rather than merely landing one line below the hard
  policy ceiling.
- Do not delete meaningful coverage or weaken acceptance criteria merely to
  satisfy the size limit.

Current changed diff (changes already made in this SAME worktree):
{_redact_development_feedback(current_diff)[-30000:] if current_diff.strip() else '(no changes yet)'}

Authoritative CURRENT source files: {files}
{context}

Return exact structured source edits against the CURRENT source above. Each
old_text must exist exactly once in the current worktree. Do not generate Git
diff syntax. Add or improve focused regression tests where needed. Do not undo
working prior changes merely to regenerate a complete solution. When feedback
requires a repair, you MUST return at least one concrete structured edit that
addresses that feedback; an empty edits list is invalid and will be retried
without consuming the development repair budget. Never weaken authentication,
Admin Mode, identity separation, device/tool safety, security, patch policy,
independent review, human approval, deployment or rollback.
""".strip()
    tool = {
        "type": "function",
        "name": "submit_development_step",
        "description": "Submit the next bounded exact edits for the current development worktree.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "reasoning_summary": {"type": "string"},
                "risk": {"type": "string", "enum": ["low", "medium", "high"]},
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_text": {"type": "string"},
                            "new_text": {"type": "string"},
                        },
                        "required": ["path", "old_text", "new_text"],
                        "additionalProperties": False,
                    },
                },
                "tests_added": {"type": "array", "items": {"type": "string"}},
                "remaining_concerns": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "summary", "reasoning_summary", "risk", "edits",
                "tests_added", "remaining_concerns"
            ],
            "additionalProperties": False,
        },
    }
    return _call_tool(
        name="submit_development_step",
        tool=tool,
        instructions=(
            "Act as a careful senior Python implementation agent. Work incrementally "
            "against the supplied current worktree. Use only exact structured edits."
        ),
        prompt=prompt,
        config=config,
        env_values=env_values,
        max_output_tokens=32000,
    )


def _semantic_review(
    failure: dict[str, Any],
    state: dict[str, Any],
    workspace: Path,
    config: base.WorkerConfig,
    env_values: dict[str, str],
    validation: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    diff = base.run(
        ["git", "diff", "--no-ext-diff", "--unified=5", "HEAD", "--"],
        cwd=workspace,
    ).stdout
    review_terms = " ".join(str(item) for item in state.get("acceptance_criteria", []))
    current_context, current_files = _workspace_context(
        failure,
        workspace,
        base.load_policy(),
        review_terms,
    )
    prompt = f"""
Perform a semantic acceptance review of the CURRENT Jarvis development
worktree before it is allowed into the formal candidate pipeline.

User goal:
{base.redact(_request_text(failure))}

Acceptance criteria:
{json.dumps(state.get('acceptance_criteria', []), ensure_ascii=False, indent=2)}

Test strategy:
{json.dumps(state.get('test_strategy', []), ensure_ascii=False, indent=2)}

Current diff:
{_redact_development_feedback(diff)[-50000:]}

Relevant CURRENT production/test source files: {current_files}
{current_context}

Development validation:
{json.dumps(validation, ensure_ascii=False, default=str)[-20000:]}

Reject as repair_required if any acceptance criterion is not actually proven by
production code and tests. Look specifically for missed call sites, duplicate
or shadowed definitions, stale state transitions, tests that seed state instead
of exercising production creation/registration, incorrect mocks, or a fallback
path that bypasses the intended fix. This is not the independent final safety
review; it is a correctness gate used to drive another repair in the SAME
worktree.
""".strip()
    tool = {
        "type": "function",
        "name": "submit_semantic_review",
        "description": "Decide whether the development task satisfies its acceptance contract.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "repair_required"]},
                "summary": {"type": "string"},
                "criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion": {"type": "string"},
                            "satisfied": {"type": "boolean"},
                            "evidence": {"type": "string"},
                        },
                        "required": ["criterion", "satisfied", "evidence"],
                        "additionalProperties": False,
                    },
                },
                "required_repairs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "summary", "criteria", "required_repairs"],
            "additionalProperties": False,
        },
    }
    return _call_tool(
        name="submit_semantic_review",
        tool=tool,
        instructions=(
            "Act as a sceptical senior engineer checking semantic completeness. "
            "Do not edit code; identify concrete missing behaviour for repair."
        ),
        prompt=prompt,
        config=config,
        env_values=env_values,
        max_output_tokens=16000,
    )


def _apply_incremental_edits(
    workspace: Path,
    payload: dict[str, Any],
    policy: dict[str, Any],
    config: base.WorkerConfig,
) -> tuple[str, list[str], str]:
    edits = payload.get("edits")
    if not isinstance(edits, list) or not edits:
        raise base.WorkerError("Development step returned no structured edits.")
    if len(edits) > config.max_patch_lines:
        raise base.WorkerError("Development step contains too many structured edits.")

    grouped: dict[str, list[tuple[str, str]]] = {}
    for index, edit in enumerate(edits, start=1):
        if not isinstance(edit, dict):
            raise base.WorkerError(f"Development edit {index} is not an object.")
        path_value = edit.get("path")
        old_text = edit.get("old_text")
        new_text = edit.get("new_text")
        if not isinstance(path_value, str):
            raise base.WorkerError(f"Development edit {index} has an invalid path.")
        path = base._validate_structured_edit_path(path_value, policy)
        if not isinstance(old_text, str) or not old_text:
            raise base.WorkerError(f"Development edit {index} has invalid old_text.")
        if not isinstance(new_text, str):
            raise base.WorkerError(f"Development edit {index} has invalid new_text.")
        if old_text == new_text:
            raise base.WorkerError(f"Development edit {index} would make no change.")
        grouped.setdefault(path, []).append((old_text, new_text))

    workspace_root = workspace.resolve()
    originals: dict[Path, str] = {}
    updated_files: dict[Path, str] = {}

    try:
        for path, replacements in grouped.items():
            target = workspace / path
            if target.is_symlink() or not target.is_file():
                raise base.WorkerError(f"Development target is not a safe regular file: {path}")
            resolved = target.resolve()
            if not resolved.is_relative_to(workspace_root):
                raise base.WorkerError(f"Development edit escaped the worktree: {path}")
            original = target.read_text(encoding="utf-8")
            originals[target] = original
            spans: list[tuple[int, int, str]] = []
            for old_text, new_text in replacements:
                occurrences = original.count(old_text)
                if occurrences != 1:
                    raise base.WorkerError(
                        f"Development old_text for {path} occurs {occurrences} times; exactly one is required."
                    )
                start = original.index(old_text)
                end = start + len(old_text)
                if any(start < other_end and end > other_start for other_start, other_end, _ in spans):
                    raise base.WorkerError(f"Development edits overlap in {path}.")
                spans.append((start, end, new_text))
            updated = original
            for start, end, new_text in sorted(spans, key=lambda item: item[0], reverse=True):
                updated = updated[:start] + new_text + updated[end:]
            updated_files[target] = updated

        for target, updated in updated_files.items():
            target.write_text(updated, encoding="utf-8")

        patch = base.run(
            ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--"],
            cwd=workspace,
        ).stdout
        if not patch.strip():
            raise base.WorkerError("Development edits produced no Git diff.")
        paths, patch_hash = base.validate_patch_policy(patch, policy, config)
        return patch, paths, patch_hash
    except Exception:
        for target, original in originals.items():
            target.write_text(original, encoding="utf-8")
        raise


def _write_patch(candidate_id: int, patch: str) -> Path:
    base.ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = base.ARTIFACTS / f"candidate-{candidate_id}.patch"
    path.write_text(patch, encoding="utf-8")
    return path


def _strict_reapply_check(
    candidate_id: int,
    base_commit: str,
    patch_path: Path,
) -> dict[str, Any]:
    verify_path = base.WORKTREES / f"verify-{candidate_id}-{secrets.token_hex(4)}"
    try:
        base.run(
            ["git", "worktree", "add", "--detach", str(verify_path), base_commit],
            cwd=base.ROOT,
            timeout=120,
        )
        check = base.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=verify_path,
            timeout=120,
            check=False,
        )
        if check.returncode != 0:
            return {
                "passed": False,
                "stage": "git_apply_check",
                "output": check.stdout[-12000:],
            }
        applied = base.run(
            ["git", "apply", str(patch_path)],
            cwd=verify_path,
            timeout=120,
            check=False,
        )
        if applied.returncode != 0:
            return {
                "passed": False,
                "stage": "git_apply",
                "output": applied.stdout[-12000:],
            }
        diff_check = base.run(
            ["git", "diff", "--check"], cwd=verify_path, timeout=60, check=False
        )
        return {
            "passed": diff_check.returncode == 0,
            "stage": "complete" if diff_check.returncode == 0 else "git_diff_check",
            "output": diff_check.stdout[-12000:],
        }
    finally:
        base.run(
            ["git", "worktree", "remove", "--force", str(verify_path)],
            cwd=base.ROOT,
            timeout=120,
            check=False,
        )
        shutil.rmtree(verify_path, ignore_errors=True)
        base.run(["git", "worktree", "prune"], cwd=base.ROOT, check=False)


def _development_validation(
    workspace: Path,
    config: base.WorkerConfig,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    commands = [
        ("git_diff_check", ["git", "diff", "--check"], 60),
        (
            "compileall",
            [str(base.VENV_PYTHON), "-m", "compileall", "-q", "bridge/app"],
            180,
        ),
        (
            "ruff",
            [str(base.VENV_PYTHON), "-m", "ruff", "check", "bridge/app", "bridge/tests"],
            240,
        ),
    ]
    for name, command, timeout in commands:
        result = base.run(command, cwd=workspace, timeout=timeout, check=False)
        checks.append(base.command_result(name, result, True))
        if result.returncode != 0:
            return {"passed": False, "checks": checks}

    pytest_result = base.pytest_baseline_result(workspace, config.candidate_timeout_seconds)
    checks.append(pytest_result)
    return {
        "passed": all(bool(item.get("passed")) for item in checks),
        "checks": checks,
    }


def _prepare_workspace(
    candidate: dict[str, Any],
    state: dict[str, Any],
) -> tuple[Path, str, str]:
    candidate_id = int(candidate["candidate_id"])
    base_commit = base._normalise_commit_sha(
        candidate.get("base_commit") or state.get("base_commit"),
        label="development base",
    )
    branch = str(candidate.get("branch_name") or f"jarvis/improvement-{candidate_id}")
    expected_branch = f"jarvis/improvement-{candidate_id}"
    if branch != expected_branch:
        raise base.WorkerError("Development branch binding is invalid.")

    base.ensure_repo()
    live_head = base._normalise_commit_sha(
        base.run(["git", "rev-parse", "HEAD"], cwd=base.ROOT).stdout.strip(),
        label="live development HEAD",
    )
    if live_head != base_commit:
        raise base.WorkerError(
            "Live Jarvis HEAD changed while the development task was in progress. "
            "The task must be restarted from the new base instead of rebasing silently."
        )

    expected = (base.WORKTREES / str(candidate_id)).resolve()
    workspace_raw = str(candidate.get("workspace_path") or "").strip()
    workspace = Path(workspace_raw) if workspace_raw else expected

    if workspace.exists():
        if workspace.is_symlink() or workspace.resolve() != expected:
            raise base.WorkerError("Development workspace binding is unsafe.")
        head = base._normalise_commit_sha(
            base.run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip(),
            label="development worktree HEAD",
        )
        if head != base_commit:
            raise base.WorkerError(
                "Development worktree HEAD moved away from its captured base."
            )
        return workspace, base_commit, branch

    workspace = base.create_worktree(candidate_id, branch, base_commit)
    patch_path_raw = str(candidate.get("patch_path") or "").strip()
    patch_path = Path(patch_path_raw) if patch_path_raw else base.ARTIFACTS / f"candidate-{candidate_id}.patch"
    if patch_path.is_file() and patch_path.stat().st_size > 0:
        check = base.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=workspace,
            timeout=120,
            check=False,
        )
        if check.returncode != 0:
            raise base.WorkerError(
                "Saved development patch cannot be restored on its captured base.\n"
                + check.stdout[-8000:]
            )
        base.run(["git", "apply", str(patch_path)], cwd=workspace, timeout=120)
    return workspace, base_commit, branch


def _record_attempt(
    candidate_id: int,
    failure_id: int,
    state: dict[str, Any],
    *,
    number: int,
    stage: str,
    outcome: str,
    summary: str,
    feedback: str = "",
) -> None:
    attempts = state.setdefault("attempts", [])
    clean_feedback = _clean_development_feedback(feedback)
    attempts.append(
        {
            "number": number,
            "stage": stage,
            "outcome": outcome,
            "summary": base.redact(summary)[:1200],
            "feedback": clean_feedback,
        }
    )
    state["last_feedback"] = clean_feedback
    state["phase"] = "repairing" if outcome != "passed" else stage
    _save_state(candidate_id, state)
    base.audit(
        "development_iteration",
        failure_id=failure_id,
        candidate_id=candidate_id,
        details={
            "attempt": number,
            "stage": stage,
            "outcome": outcome,
            "summary": base.redact(summary)[:1200],
        },
    )


def _migrate_legacy_attempt_state(state: dict[str, Any]) -> bool:
    """Move proposal/edit errors out of the repair budget and normalise numbering."""
    attempts = state.setdefault("attempts", [])
    kept: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []

    for raw in attempts:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        if (
            str(item.get("stage") or "") == "edit"
            and str(item.get("outcome") or "") != "passed"
        ):
            item["migrated_nonbudget"] = True
            moved.append(item)
        else:
            kept.append(item)

    renumbered = False
    for index, item in enumerate(kept, start=1):
        previous = item.get("number")
        if previous != index:
            item.setdefault("original_number", previous)
            item["number"] = index
            renumbered = True

    changed = (
        bool(moved)
        or renumbered
        or int(state.get("version") or 0) < STATE_VERSION
    )
    state["attempts"] = kept
    if moved:
        state.setdefault("nonbudget_events", []).extend(moved)
    state["version"] = STATE_VERSION
    return changed


def _development_validation_feedback(validation: dict[str, Any]) -> str:
    return (
        "Development validation failed. Inspect the exact failures and repair "
        "the CURRENT worktree without discarding working changes.\n"
        + json.dumps(validation, ensure_ascii=False, default=str)[-DEVELOPMENT_FEEDBACK_LIMIT:]
    )


def _restore_missing_feedback(
    state: dict[str, Any],
    workspace: Path,
    config: base.WorkerConfig,
) -> tuple[str, bool]:
    """Recover v2.0 validation evidence that was overwritten by an edit error."""
    attempts = state.get("attempts") or []
    if not attempts:
        return str(state.get("last_feedback") or ""), False

    latest = attempts[-1]
    if not isinstance(latest, dict):
        return str(state.get("last_feedback") or ""), False

    stored = str(latest.get("feedback") or "").strip()
    if stored:
        state["last_feedback"] = stored
        return stored, False

    if str(latest.get("stage") or "") != "development_validation":
        return str(state.get("last_feedback") or ""), False

    validation = _development_validation(workspace, config)
    if bool(validation.get("passed")):
        return str(state.get("last_feedback") or ""), False

    feedback = _development_validation_feedback(validation)
    clean = _clean_development_feedback(feedback)
    latest["feedback"] = clean
    state["last_feedback"] = clean
    return clean, True


def _apply_development_step_with_retries(
    *,
    candidate_id: int,
    attempt_number: int,
    failure: dict[str, Any],
    state: dict[str, Any],
    workspace: Path,
    policy: dict[str, Any],
    config: base.WorkerConfig,
    env_values: dict[str, str],
    feedback: str,
) -> tuple[dict[str, Any], dict[str, Any], str, list[str], str]:
    """Get and apply one valid edit proposal without spending repair attempts on malformed proposals."""
    max_step_retries = _env_int(
        env_values,
        "JARVIS_DEVELOPMENT_STEP_RETRIES",
        DEFAULT_MAX_STEP_RETRIES,
        1,
        5,
    )
    original_feedback = str(feedback or "")

    retry_events = state.setdefault(
        "step_retries",
        [],
    )
    if not isinstance(retry_events, list):
        raise base.WorkerError(
            "Development step retry history is invalid."
        )

    current_retry_events = [
        item
        for item in retry_events
        if (
            isinstance(item, dict)
            and item.get("attempt") == attempt_number
        )
    ]

    retries_used = min(
        len(current_retry_events),
        max_step_retries,
    )

    last_error = ""

    if current_retry_events:
        last_error = _clean_development_feedback(
            str(
                current_retry_events[-1].get("error")
                or ""
            )
        )[-12000:]

    proposal_feedback = original_feedback

    if last_error:
        proposal_feedback = (
            original_feedback
            + "\n\nThe previous edit proposal was invalid and was NOT "
            "counted as a development attempt. Correct the proposal against "
            "the CURRENT worktree and return at least one concrete exact edit.\n"
            + last_error
        )[-20000:]

    if retries_used >= max_step_retries:
        raise base.WorkerError(
            "Development step could not produce a valid structured edit after "
            f"{max_step_retries} internal retries.\n{last_error}"
        )

    for retry_number in range(
        retries_used + 1,
        max_step_retries + 1,
    ):
        try:
            payload, usage = _development_step(
                failure,
                state,
                workspace,
                policy,
                config,
                env_values,
                proposal_feedback,
            )
            edits = payload.get("edits")
            if not isinstance(edits, list) or not edits:
                raise base.WorkerError(
                    "Development step returned no structured edits."
                )
            patch, paths, patch_hash = _apply_incremental_edits(
                workspace,
                payload,
                policy,
                config,
            )
            state["last_step_usage"] = usage
            state.pop("last_step_error", None)
            return payload, usage, patch, paths, patch_hash
        except ExternalDependencyBlocked:
            raise
        except Exception as exc:
            if _is_external_credit_block(exc):
                raise ExternalDependencyBlocked(_external_dependency_feedback(exc)) from exc
            last_error = _clean_development_feedback(str(exc))[-12000:]
            event = {
                "attempt": attempt_number,
                "retry": retry_number,
                "error": last_error,
                "created_at": base.utc_now(),
            }
            state.setdefault("step_retries", []).append(event)
            state["last_step_error"] = last_error
            _save_state(candidate_id, state, status="developing")

            base.audit(
                "development_step_retry",
                candidate_id=candidate_id,
                details={
                    "attempt": attempt_number,
                    "retry": retry_number,
                    "error": last_error[-2000:],
                },
            )

            if retry_number >= max_step_retries:
                break

            proposal_feedback = (
                original_feedback
                + "\n\nThe previous edit proposal was invalid and was NOT "
                "counted as a development attempt. Correct the proposal against "
                "the CURRENT worktree and return at least one concrete exact edit.\n"
                + last_error
            )[-20000:]

    raise base.WorkerError(
        "Development step could not produce a valid structured edit after "
        f"{max_step_retries} internal retries.\n{last_error}"
    )


def _review_failure(
    failure: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    enriched = dict(failure)
    evidence = dict(failure.get("evidence") or {})
    evidence["development_plan"] = {
        "summary": state.get("summary"),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "test_strategy": state.get("test_strategy", []),
    }
    enriched["evidence"] = evidence
    return enriched


def _finalise_ready_candidate(
    *,
    candidate_id: int,
    failure_id: int,
    failure: dict[str, Any],
    state: dict[str, Any],
    workspace: Path,
    base_commit: str,
    branch: str,
    patch_path: Path,
    patch: str,
    paths: list[str],
    patch_hash: str,
    payload: dict[str, Any],
    tests: dict[str, Any],
    security: dict[str, Any],
    config: base.WorkerConfig,
) -> None:
    summary = str(
        payload.get("summary")
        or state.get("summary")
        or failure.get("summary")
        or "Jarvis development task"
    ).strip()
    model_risk = str(payload.get("risk") or state.get("risk") or "medium").lower()
    risk = base.determine_risk(paths, model_risk, base.load_policy())

    commit_sha = base._normalise_commit_sha(
        base.commit_candidate(workspace, candidate_id, failure_id, summary),
        label="candidate",
    )
    validated_patch_hash = base.candidate_diff_sha256(
        base_commit, commit_sha, cwd=workspace
    )
    pr_url = base.maybe_create_pr(workspace, branch, candidate_id, summary, config)
    approval_code = f"{secrets.randbelow(900000) + 100000:06d}"
    approval_expires = base.utc_after(24 * 60 * 60)

    state["phase"] = "awaiting_approval"
    state["completed_at"] = base.utc_now()
    state["final_changed_files"] = paths
    state["final_patch_sha256"] = validated_patch_hash

    diff_stats = {
        "changed_files": len(paths),
        "changed_lines": base.patch_line_count(patch),
        "patch_sha256": patch_hash,
        "source_patch_sha256": patch_hash,
        "validated_patch_sha256": validated_patch_hash,
        "base_commit": base_commit,
        "candidate_commit": commit_sha,
        "commit_sha": commit_sha,
        "context_files": state.get("context_files", []),
        "tests_added": payload.get("tests_added", []),
        "development_attempts": len(state.get("attempts", [])),
        "acceptance_criteria": state.get("acceptance_criteria", []),
    }

    base.update_candidate(
        candidate_id,
        status="awaiting_approval",
        workspace_path=str(workspace),
        summary=summary,
        root_cause=str(payload.get("reasoning_summary") or state.get("investigation") or ""),
        risk=risk,
        patch_path=str(patch_path),
        changed_files_json=paths,
        diff_stats_json=diff_stats,
        test_results_json=tests,
        security_results_json=security,
        usage_json={"development": state},
        approval_code=approval_code,
        approval_code_expires_at=approval_expires,
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
    base.update_failure(failure_id, status="candidate_ready")
    base.audit(
        "development_ready",
        failure_id=failure_id,
        candidate_id=candidate_id,
        details={
            "summary": summary,
            "risk": risk,
            "attempts": len(state.get("attempts", [])),
            "base_commit": base_commit,
            "candidate_commit": commit_sha,
            "validated_patch_sha256": validated_patch_hash,
        },
    )


def _pause_external_dependency(
    *,
    candidate_id: int,
    failure_id: int,
    state: dict[str, Any],
    workspace: Path,
    exc: BaseException | str,
) -> None:
    feedback = _external_dependency_feedback(exc)
    event = {
        "created_at": base.utc_now(),
        "kind": "openai_api_credit",
        "error": feedback,
        "attempts_preserved": len(state.get("attempts", [])),
    }
    state.setdefault("external_dependency_events", []).append(event)
    state["phase"] = "blocked_external"
    state["last_feedback"] = feedback
    state["blocked_external_at"] = event["created_at"]
    state["blocked_external_kind"] = event["kind"]
    _save_state(
        candidate_id,
        state,
        status="blocked_external",
        error=feedback[-12000:],
        workspace_path=str(workspace),
    )
    base.audit(
        "development_blocked_external",
        failure_id=failure_id,
        candidate_id=candidate_id,
        details={
            "kind": event["kind"],
            "attempts_preserved": event["attempts_preserved"],
        },
    )


def process_development_candidate(
    candidate: dict[str, Any],
    config: base.WorkerConfig,
    env_values: dict[str, str],
) -> None:
    candidate_id = int(candidate["candidate_id"])
    failure_id = int(candidate["failure_id"])
    failure = base.fetch_failure(failure_id)
    manual_request = not base.uses_autonomous_attempt_quota(failure)
    status = str(candidate.get("status") or "")

    if status == "queued":
        if not manual_request and base.attempts_today() >= config.max_attempts_per_day:
            return
        if not base.improvement_enabled():
            return
        policy = base.load_policy()
        base.ensure_repo()
        base.ensure_candidate_transaction_columns()
        base_commit = base._normalise_commit_sha(
            base.run(["git", "rev-parse", "HEAD"], cwd=base.ROOT).stdout.strip(),
            label="development base",
        )
        branch = f"jarvis/improvement-{candidate_id}"
        base.update_candidate(
            candidate_id,
            status="developing",
            model=config.model,
            branch_name=branch,
            base_commit=base_commit,
            error=None,
        )
        base.audit(
            "candidate_generation_started",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "model": config.model,
                "manual_request": manual_request,
                "development_v2": True,
            },
        )
        base.audit(
            "development_started",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={"base_commit": base_commit, "model": config.model},
        )
        candidate = base.fetch_candidate_by_id(candidate_id) or candidate
        state = _state(candidate)
        state.update(
            {
                "version": STATE_VERSION,
                "phase": "planning",
                "base_commit": base_commit,
                "started_at": base.utc_now(),
                "attempts": [],
                "last_feedback": "",
            }
        )
        workspace = base.create_worktree(candidate_id, branch, base_commit)
        _save_state(
            candidate_id,
            state,
            workspace_path=str(workspace),
            branch_name=branch,
            base_commit=base_commit,
            status="developing",
        )
        candidate = base.fetch_candidate_by_id(candidate_id) or candidate
    elif status != "developing":
        raise base.WorkerError(
            f"Candidate {candidate_id} is not available for development: {status}"
        )

    policy = base.load_policy()
    candidate = base.fetch_candidate_by_id(candidate_id) or candidate
    state = _state(candidate)
    workspace, base_commit, branch = _prepare_workspace(candidate, state)

    if not state.get("acceptance_criteria"):
        try:
            state["phase"] = "planning"
            _save_state(candidate_id, state, status="developing", error=None)
            plan, plan_usage = _plan(failure, workspace, policy, config, env_values)
            state.update(
                {
                    "summary": str(plan.get("summary") or ""),
                    "investigation": plan.get("investigation", []),
                    "acceptance_criteria": plan.get("acceptance_criteria", []),
                    "likely_files": plan.get("likely_files", []),
                    "test_strategy": plan.get("test_strategy", []),
                    "risk": str(plan.get("risk") or "medium"),
                    "planning_usage": plan_usage,
                    "phase": "implementing",
                }
            )
            _, context_files = _workspace_context(failure, workspace, policy)
            state["context_files"] = context_files
            _save_state(candidate_id, state, status="developing", error=None)
            base.audit(
                "development_planned",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={
                    "summary": base.redact(str(state.get("summary") or ""))[:1200],
                    "criteria_count": len(state.get("acceptance_criteria", [])),
                },
            )
        except ExternalDependencyBlocked as exc:
            _pause_external_dependency(
                candidate_id=candidate_id,
                failure_id=failure_id,
                state=state,
                workspace=workspace,
                exc=exc,
            )
            return
        except Exception as exc:
            if _is_external_credit_block(exc):
                _pause_external_dependency(
                    candidate_id=candidate_id,
                    failure_id=failure_id,
                    state=state,
                    workspace=workspace,
                    exc=exc,
                )
                return
            state["phase"] = "failed"
            state["failed_at"] = base.utc_now()
            state["last_feedback"] = base.redact(str(exc))[-16000:]
            _save_state(
                candidate_id,
                state,
                status="failed",
                error=("Development planning failed.\n" + str(exc))[-12000:],
                workspace_path=str(workspace),
            )
            base.update_failure(failure_id, status="recorded")
            base.audit(
                "development_failed",
                failure_id=failure_id,
                candidate_id=candidate_id,
                details={"stage": "planning", "error": str(exc)[-4000:]},
            )
            raise

    migrated = _migrate_legacy_attempt_state(state)
    feedback, feedback_restored = _restore_missing_feedback(
        state,
        workspace,
        config,
    )
    if migrated or feedback_restored:
        _save_state(candidate_id, state, status="developing", error=None)
        base.audit(
            "development_state_migrated",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "version": STATE_VERSION,
                "attempts": len(state.get("attempts", [])),
                "nonbudget_events": len(state.get("nonbudget_events", [])),
                "feedback_restored": feedback_restored,
            },
        )

    max_repairs = _env_int(
        env_values, "JARVIS_DEVELOPMENT_MAX_REPAIRS", DEFAULT_MAX_REPAIRS, 0, 8
    )
    max_attempts = 1 + max_repairs
    attempts_done = len(state.get("attempts", []))
    last_payload: dict[str, Any] = {}

    try:
        for number in range(attempts_done + 1, max_attempts + 1):
            state["phase"] = "implementing" if number == 1 else "repairing"
            _save_state(candidate_id, state, status="developing", error=None)
            (
                payload,
                step_usage,
                patch,
                paths,
                patch_hash,
            ) = _apply_development_step_with_retries(
                candidate_id=candidate_id,
                attempt_number=number,
                failure=failure,
                state=state,
                workspace=workspace,
                policy=policy,
                config=config,
                env_values=env_values,
                feedback=feedback,
            )
            last_payload = payload
            patch_path = _write_patch(candidate_id, patch)
            base.update_candidate(
                candidate_id,
                workspace_path=str(workspace),
                patch_path=str(patch_path),
                changed_files_json=paths,
                diff_stats_json={
                    "changed_files": len(paths),
                    "changed_lines": base.patch_line_count(patch),
                    "patch_sha256": patch_hash,
                    "development_attempt": number,
                    "step_retries": len(state.get("step_retries", [])),
                },
            )

            dev_validation = _development_validation(workspace, config)
            if not bool(dev_validation.get("passed")):
                feedback = _development_validation_feedback(dev_validation)
                _record_attempt(
                    candidate_id,
                    failure_id,
                    state,
                    number=number,
                    stage="development_validation",
                    outcome="repair_required",
                    summary="Development validation failed.",
                    feedback=feedback,
                )
                continue

            semantic, semantic_usage = _semantic_review(
                failure, state, workspace, config, env_values, dev_validation
            )
            state["last_semantic_usage"] = semantic_usage
            if str(semantic.get("verdict") or "repair_required") != "pass":
                feedback = (
                    "Semantic acceptance review requires repair:\n"
                    + json.dumps(semantic, ensure_ascii=False, default=str)[-16000:]
                )
                _record_attempt(
                    candidate_id,
                    failure_id,
                    state,
                    number=number,
                    stage="semantic_review",
                    outcome="repair_required",
                    summary=str(semantic.get("summary") or "Semantic review failed."),
                    feedback=feedback,
                )
                continue

            patch = base.run(
                ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--"],
                cwd=workspace,
            ).stdout
            paths, patch_hash = base.validate_patch_policy(patch, policy, config)
            patch_path = _write_patch(candidate_id, patch)
            reapply = _strict_reapply_check(candidate_id, base_commit, patch_path)
            if not bool(reapply.get("passed")):
                feedback = "Strict clean-base reapply failed:\n" + json.dumps(reapply)[-12000:]
                _record_attempt(
                    candidate_id,
                    failure_id,
                    state,
                    number=number,
                    stage="strict_reapply",
                    outcome="repair_required",
                    summary="Strict clean-base reapply failed.",
                    feedback=feedback,
                )
                continue

            tests, security = base.run_validation(workspace, policy, config)
            smoke = base.docker_smoke_test(
                workspace, candidate_id, config.candidate_timeout_seconds
            )
            tests["candidate_container"] = smoke
            tests["passed"] = bool(tests.get("passed")) and bool(smoke.get("passed"))
            if not tests["passed"] or not bool(security.get("passed")):
                feedback = (
                    "Formal validation failed. Repair the SAME worktree:\n"
                    + json.dumps(
                        {"tests": tests, "security": security},
                        ensure_ascii=False,
                        default=str,
                    )[-16000:]
                )
                _record_attempt(
                    candidate_id,
                    failure_id,
                    state,
                    number=number,
                    stage="formal_validation",
                    outcome="repair_required",
                    summary="Formal validation or candidate smoke failed.",
                    feedback=feedback,
                )
                continue

            review = base.request_independent_review(
                failure=_review_failure(failure, state),
                workspace=workspace,
                tests=tests,
                security=security,
                config=config,
                env_values=env_values,
            )
            security["independent_ai_review"] = review
            if str(review.get("verdict") or "reject") != "approve":
                feedback = (
                    "Independent final review rejected the current implementation. "
                    "Repair these findings in the SAME worktree:\n"
                    + json.dumps(review, ensure_ascii=False, default=str)[-16000:]
                )
                _record_attempt(
                    candidate_id,
                    failure_id,
                    state,
                    number=number,
                    stage="independent_review",
                    outcome="repair_required",
                    summary=str(review.get("summary") or "Independent review rejected."),
                    feedback=feedback,
                )
                continue

            _record_attempt(
                candidate_id,
                failure_id,
                state,
                number=number,
                stage="ready",
                outcome="passed",
                summary="Acceptance, validation, smoke and independent review passed.",
            )
            _finalise_ready_candidate(
                candidate_id=candidate_id,
                failure_id=failure_id,
                failure=failure,
                state=state,
                workspace=workspace,
                base_commit=base_commit,
                branch=branch,
                patch_path=patch_path,
                patch=patch,
                paths=paths,
                patch_hash=patch_hash,
                payload=last_payload,
                tests=tests,
                security=security,
                config=config,
            )
            base.notify_aaron(
                f"Jarvis finished developing improvement {candidate_id} after "
                f"{len(state.get('attempts', []))} internal iteration(s). It passed "
                "semantic acceptance, validation, security, container smoke and "
                "independent review. Nothing has been installed yet.",
                title="Jarvis development ready for review",
                config=config,
                env_values=env_values,
            )
            return

        feedback = str(state.get("last_feedback") or feedback or "Repair budget exhausted.")
        raise base.WorkerError(
            f"Development repair budget exhausted after {max_attempts} attempts.\n"
            + feedback[-12000:]
        )
    except ExternalDependencyBlocked as exc:
        _pause_external_dependency(
            candidate_id=candidate_id,
            failure_id=failure_id,
            state=state,
            workspace=workspace,
            exc=exc,
        )
        return
    except Exception as exc:
        if _is_external_credit_block(exc):
            _pause_external_dependency(
                candidate_id=candidate_id,
                failure_id=failure_id,
                state=state,
                workspace=workspace,
                exc=exc,
            )
            return
        state["phase"] = "failed"
        state["failed_at"] = base.utc_now()
        state["last_feedback"] = base.redact(str(exc))[-16000:]
        _save_state(
            candidate_id,
            state,
            status="failed",
            error=str(exc)[-12000:],
            workspace_path=str(workspace),
        )
        base.update_failure(failure_id, status="recorded")
        base.audit(
            "development_failed",
            failure_id=failure_id,
            candidate_id=candidate_id,
            details={
                "attempts": len(state.get("attempts", [])),
                "error": str(exc)[-4000:],
            },
        )
        base.notify_aaron(
            f"Jarvis could not complete development task {candidate_id} within its "
            "bounded repair budget. Nothing was installed. The worktree and failure "
            "history were preserved for diagnosis.",
            title="Jarvis development needs review",
            config=config,
            env_values=env_values,
        )
        raise



def block_failed_external_candidate(
    candidate_id: int,
    config: base.WorkerConfig,
) -> dict[str, Any]:
    """Convert a quota-failed preserved task into a non-budget external block."""
    candidate = base.fetch_candidate_by_id(candidate_id)
    if candidate is None:
        raise base.WorkerError(f"Candidate {candidate_id} was not found.")
    status = str(candidate.get("status") or "")
    if status != "failed":
        raise base.WorkerError(
            f"Candidate {candidate_id} is not failed; external-block migration refused: {status}"
        )
    error = str(candidate.get("error") or "")
    if not _is_external_credit_block(error):
        raise base.WorkerError(
            "Candidate failure is not an OpenAI API credit/billing block; migration refused."
        )
    failure_id = int(candidate.get("failure_id") or 0)
    if failure_id <= 0:
        raise base.WorkerError("Candidate failure binding is missing.")
    state = _state(candidate)
    workspace, base_commit, branch = _prepare_workspace(candidate, state)
    if not base.run(["git", "status", "--porcelain"], cwd=workspace, check=False).stdout.strip():
        raise base.WorkerError("Candidate worktree is clean; external-block migration requires preserved changes.")

    # Refresh local validation evidence without using the API. This preserves exact pytest node IDs.
    validation = _development_validation(workspace, config)
    if not bool(validation.get("passed")):
        feedback = _clean_development_feedback(_development_validation_feedback(validation))
        state["last_feedback"] = feedback
        attempts = state.get("attempts") or []
        if attempts and isinstance(attempts[-1], dict) and str(attempts[-1].get("stage") or "") == "development_validation":
            attempts[-1]["feedback"] = feedback
    else:
        feedback = _external_dependency_feedback(error)

    event = {
        "created_at": base.utc_now(),
        "kind": "openai_api_credit",
        "source_status": status,
        "attempts_preserved": len(state.get("attempts", [])),
        "base_commit": base_commit,
        "branch": branch,
    }
    state.setdefault("external_dependency_events", []).append(event)
    state.pop("failed_at", None)
    state["phase"] = "blocked_external"
    state["blocked_external_at"] = event["created_at"]
    state["blocked_external_kind"] = event["kind"]
    state["blocked_external_error"] = _external_dependency_feedback(error)
    _save_state(
        candidate_id,
        state,
        status="blocked_external",
        error=state["blocked_external_error"][-12000:],
        workspace_path=str(workspace),
        branch_name=branch,
        base_commit=base_commit,
    )
    base.audit(
        "development_external_block_migrated",
        failure_id=failure_id,
        candidate_id=candidate_id,
        details={
            "attempts_preserved": len(state.get("attempts", [])),
            "base_commit": base_commit,
        },
    )
    return {
        "candidate_id": candidate_id,
        "status": "blocked_external",
        "phase": "blocked_external",
        "attempts_preserved": len(state.get("attempts", [])),
        "validation_passed": bool(validation.get("passed")),
        "changed_files": base.run(["git", "diff", "--name-only", "HEAD", "--"], cwd=workspace, check=False).stdout.splitlines(),
    }


def resume_external_candidate(
    candidate_id: int,
    config: base.WorkerConfig,
) -> dict[str, Any]:
    """Resume an externally blocked task without changing its repair-attempt count."""
    candidate = base.fetch_candidate_by_id(candidate_id)
    if candidate is None:
        raise base.WorkerError(f"Candidate {candidate_id} was not found.")
    status = str(candidate.get("status") or "")
    if status != "blocked_external":
        raise base.WorkerError(
            f"Candidate {candidate_id} is not blocked_external; resume refused: {status}"
        )
    failure_id = int(candidate.get("failure_id") or 0)
    if failure_id <= 0:
        raise base.WorkerError("Candidate failure binding is missing.")
    state = _state(candidate)
    workspace, base_commit, branch = _prepare_workspace(candidate, state)
    _, resume_env_values = base.load_config()
    max_repairs = _env_int(
        resume_env_values,
        "JARVIS_DEVELOPMENT_MAX_REPAIRS",
        DEFAULT_MAX_REPAIRS,
        0,
        8,
    )
    max_attempts = 1 + max_repairs
    if len(state.get("attempts", [])) >= max_attempts:
        raise base.WorkerError(
            "Candidate has no genuine development repair budget remaining."
        )
    state["phase"] = "repairing"
    state["resumed_external_at"] = base.utc_now()
    state.pop("blocked_external_at", None)
    state.pop("blocked_external_kind", None)
    state.pop("blocked_external_error", None)
    _save_state(
        candidate_id,
        state,
        status="developing",
        error=None,
        workspace_path=str(workspace),
        branch_name=branch,
        base_commit=base_commit,
    )
    base.audit(
        "development_external_block_resumed",
        failure_id=failure_id,
        candidate_id=candidate_id,
        details={
            "attempts_preserved": len(state.get("attempts", [])),
            "base_commit": base_commit,
        },
    )
    return {
        "candidate_id": candidate_id,
        "status": "developing",
        "phase": "repairing",
        "attempts_preserved": len(state.get("attempts", [])),
        "next_attempt": len(state.get("attempts", [])) + 1,
        "changed_files": base.run(["git", "diff", "--name-only", "HEAD", "--"], cwd=workspace, check=False).stdout.splitlines(),
    }


def refresh_candidate_validation_feedback(
    candidate_id: int,
    config: base.WorkerConfig,
) -> dict[str, Any]:
    """Refresh repair evidence for a preserved developing task without spending budget."""
    candidate = base.fetch_candidate_by_id(candidate_id)
    if candidate is None:
        raise base.WorkerError(f"Candidate {candidate_id} was not found.")

    status = str(candidate.get("status") or "")
    if status != "developing":
        raise base.WorkerError(
            f"Candidate {candidate_id} is not developing; feedback refresh refused: {status}"
        )

    state = _state(candidate)
    workspace, base_commit, branch = _prepare_workspace(candidate, state)
    if not base.run(
        ["git", "status", "--porcelain"], cwd=workspace, check=False
    ).stdout.strip():
        raise base.WorkerError(
            "Candidate worktree is clean; feedback refresh requires preserved changes."
        )

    validation = _development_validation(workspace, config)
    if bool(validation.get("passed")):
        raise base.WorkerError(
            "Development validation currently passes; refusing to overwrite repair feedback."
        )

    feedback = _development_validation_feedback(validation)
    clean = _clean_development_feedback(feedback)
    state["last_feedback"] = clean
    state["version"] = STATE_VERSION

    attempts = state.get("attempts") or []
    if attempts and isinstance(attempts[-1], dict):
        latest = attempts[-1]
        if str(latest.get("stage") or "") == "development_validation":
            latest["feedback"] = clean

    refresh_event = {
        "created_at": base.utc_now(),
        "attempts": len(attempts),
        "base_commit": base_commit,
        "branch": branch,
    }
    state.setdefault("feedback_refreshes", []).append(refresh_event)
    _save_state(candidate_id, state, status="developing", error=None)
    base.audit(
        "development_feedback_refreshed",
        candidate_id=candidate_id,
        details={
            "attempts": len(attempts),
            "base_commit": base_commit,
            "feedback_characters": len(clean),
        },
    )

    new_failures: list[dict[str, Any]] = []
    for check in validation.get("checks") or []:
        if isinstance(check, dict) and check.get("name") == "pytest_baseline":
            raw = check.get("new_failures") or []
            if isinstance(raw, list):
                new_failures = [dict(item) for item in raw if isinstance(item, dict)]
            break

    return {
        "candidate_id": candidate_id,
        "status": "developing",
        "phase": state.get("phase"),
        "attempts": len(attempts),
        "feedback_refreshed": True,
        "new_failures": new_failures,
        "feedback_contains_redacted_nodeid": any(
            str(item.get("test") or "") == "[REDACTED]" for item in new_failures
        ),
        "changed_files": base.run(
            ["git", "diff", "--name-only", "HEAD", "--"],
            cwd=workspace,
            check=False,
        ).stdout.splitlines(),
    }


def run_once(config: base.WorkerConfig, env_values: dict[str, str]) -> bool:
    base.update_setting("worker_heartbeat", base.utc_now())
    if not base.improvement_enabled():
        return False

    if not config.proposal_only:
        deploying = base.fetch_candidate(("deploying",))
        if deploying:
            if base.deployment_lease_is_expired(deploying):
                base.recover_interrupted_deployment(deploying, config, env_values)
                return True
            return False

        rollback = base.fetch_manual_rollback_candidate()
        if rollback:
            base.rollback_candidate(rollback, config, env_values)
            return True

        deploy = base.fetch_candidate(("deploy_requested",))
        if deploy:
            base.deploy_candidate(deploy, config, env_values)
            return True

    developing = base.fetch_candidate(("developing",))
    if developing:
        process_development_candidate(developing, config, env_values)
        return True

    queued = base.fetch_candidate(("queued",))
    if queued:
        process_development_candidate(queued, config, env_values)
        return True

    return False



def main() -> int:
    commands = {
        "block-external-candidate": block_failed_external_candidate,
        "resume-external-candidate": resume_external_candidate,
        "refresh-feedback": refresh_candidate_validation_feedback,
    }

    if len(sys.argv) >= 2 and sys.argv[1] in commands:
        command = sys.argv[1]
        if len(sys.argv) != 3:
            print(
                f"Usage: self_development_worker.py {command} <id>",
                file=sys.stderr,
            )
            return 2
        try:
            candidate_id = int(sys.argv[2])
            config, _ = base.load_config()
            result = commands[command](candidate_id, config)
            print(json.dumps(result, indent=2))
            return 0
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    base.run_once = run_once
    return base.main()

if __name__ == "__main__":
    raise SystemExit(main())
