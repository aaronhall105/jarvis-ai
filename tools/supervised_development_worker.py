"""Single-call, proposal-only worker for the supervised development trial.

This worker intentionally does not use the legacy self-development loop.  It
creates one detached worktree, performs one Responses API request with retries
disabled, applies the existing exact structured-edit validator atomically, and
only runs local verification.  It never commits, pushes, creates a PR, or
invokes a second model review.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from tools.supervised_development_controller import LifecycleError


ProviderCall = Callable[[str, str, int], tuple[str, dict[str, Any]]]


class BoundedDevelopmentWorker:
    """Execute one immutable task without any Git integration side effects."""

    def __init__(
        self,
        source_root: Path,
        *,
        worktree_root: Path,
        provider_call: ProviderCall | None = None,
        input_price_per_million: float | None = None,
        output_price_per_million: float | None = None,
        max_input_tokens: int = 12_000,
        max_output_tokens: int = 4_000,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.worktree_root = Path(worktree_root).resolve()
        self.provider_call = provider_call or self._openai_call
        self.input_price = input_price_per_million
        self.output_price = output_price_per_million
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.actual_provider_calls = 0

    def _pricing(self) -> tuple[float, float]:
        input_price = self.input_price
        output_price = self.output_price
        if input_price is None or output_price is None or input_price <= 0 or output_price <= 0:
            raise LifecycleError("provider_pricing_unverified")
        return float(input_price), float(output_price)

    def _openai_call(self, prompt: str, model: str, timeout: int) -> tuple[str, dict[str, Any]]:
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise LifecycleError("provider_credential_unavailable")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise LifecycleError("provider_sdk_unavailable") from exc
        client = OpenAI(api_key=key, timeout=max(1, timeout), max_retries=0)
        response = client.responses.create(
            model=model,
            instructions=(
                "Return strict JSON only. Never emit a Git patch, commands, credentials, "
                "or prose outside the JSON object."
            ),
            input=prompt,
            max_output_tokens=self.max_output_tokens,
            store=False,
        )
        text = getattr(response, "output_text", "")
        usage_obj = getattr(response, "usage", None)
        usage = {
            "input_tokens": int(getattr(usage_obj, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage_obj, "output_tokens", 0) or 0),
            "reasoning_tokens": int(getattr(usage_obj, "reasoning_tokens", 0) or 0),
        }
        return str(text), usage

    def _run_git(self, *args: str, cwd: Path) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        return result.stdout.strip()

    def _prompt(self, task: Any, files: dict[str, str]) -> str:
        payload = {
            "protocol_version": 1,
            "objective": task.objective,
            "base_source_sha": task.source_sha,
            "allowed_paths": list(task.allowed_paths),
            "provider": task.provider,
            "model": task.model,
            "acceptance": [
                "Preserve existing diagnostics classifications and public output contracts.",
                "Malformed results retain the existing failed count; warnings are additive and bounded.",
                "Change only the listed files and return exact old_text/new_text edits.",
            ],
            "source": files,
            "response_schema": {
                "protocol_version": 1,
                "explanation": "string",
                "edits": [{"path": "string", "old_text": "string", "new_text": "string"}],
            },
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if len(encoded) > self.max_input_tokens * 4:
            raise LifecycleError("provider_prompt_too_large")
        return encoded

    def _apply(
        self, workspace: Path, payload: dict[str, Any], task: Any
    ) -> tuple[str, list[str], str]:
        from tools import self_development_worker as legacy

        config = SimpleNamespace(max_patch_lines=8, max_changed_files=len(task.allowed_paths))
        policy = {"allowed_edit_paths": list(task.allowed_paths), "forbidden_paths": []}
        return legacy._apply_incremental_edits(workspace, payload, policy, config)

    def _verify(self, workspace: Path, task: Any, deadline: float) -> dict[str, Any]:
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            return {"passed": False, "checks": [], "error": "deadline_expired"}
        commands = [
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *task.allowed_paths[1:]],
            [sys.executable, "-m", "ruff", "check", "--no-cache", *task.allowed_paths],
        ]
        results: list[dict[str, Any]] = []
        for command in commands:
            try:
                result = subprocess.run(
                    command,
                    cwd=workspace,
                    env={**os.environ, "PYTHONPATH": str(workspace)},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=remaining,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return {"passed": False, "checks": results, "error": type(exc).__name__}
            results.append({"command": command[:4], "returncode": result.returncode})
            if result.returncode != 0:
                return {"passed": False, "checks": results}
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                return {"passed": False, "checks": results, "error": "deadline_expired"}
        return {"passed": True, "checks": results}

    def run(self, task: Any) -> dict[str, Any]:
        if self.actual_provider_calls:
            raise LifecycleError("provider_call_already_used")
        if task.provider != "openai" or task.model != "gpt-5.3-codex":
            raise LifecycleError("worker_provider_not_approved")
        if (
            task.max_calls != 1
            or task.deadline_seconds <= 0
            or task.deadline_seconds > 120
            or task.max_cost_usd <= 0
            or task.max_cost_usd > 1
        ):
            raise LifecycleError("worker_limits_invalid")
        input_price, output_price = self._pricing()
        worst_cost = (
            self.max_input_tokens * input_price + self.max_output_tokens * output_price
        ) / 1_000_000
        if worst_cost > task.max_cost_usd:
            raise LifecycleError("provider_budget_preflight_failed")
        if self._run_git("rev-parse", "HEAD", cwd=self.source_root) != task.source_sha:
            raise LifecycleError("worker_source_drift")
        if self._run_git("status", "--porcelain", cwd=self.source_root):
            raise LifecycleError("worker_source_dirty")
        deadline = time.monotonic() + task.deadline_seconds
        self.worktree_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace = self.worktree_root / f"task-{task.request_ref}-{uuid.uuid4().hex[:8]}"
        self._run_git(
            "worktree", "add", "--detach", str(workspace), task.source_sha, cwd=self.source_root
        )
        try:
            files = {}
            for relative in task.allowed_paths:
                target = workspace / relative
                if (
                    target.is_symlink()
                    or not target.is_file()
                    or not target.resolve().is_relative_to(workspace)
                ):
                    raise LifecycleError("worker_source_path_invalid")
                files[relative] = target.read_text(encoding="utf-8")
            prompt = self._prompt(task, files)
            self.actual_provider_calls += 1
            raw, usage = self.provider_call(
                prompt, task.model, max(1, int(deadline - time.monotonic()))
            )
            if self.actual_provider_calls != 1:
                raise LifecycleError("provider_call_limit_exceeded")
            try:
                payload = json.loads(raw)
            except (TypeError, json.JSONDecodeError) as exc:
                raise LifecycleError("structured_response_invalid") from exc
            if (
                not isinstance(payload, dict)
                or payload.get("protocol_version") != 1
                or not isinstance(payload.get("explanation"), str)
                or not isinstance(payload.get("edits"), list)
                or set(payload) != {"protocol_version", "explanation", "edits"}
            ):
                raise LifecycleError("structured_response_schema")
            for edit in payload["edits"]:
                if not isinstance(edit, dict) or set(edit) != {"path", "old_text", "new_text"}:
                    raise LifecycleError("structured_response_edit_schema")
            try:
                patch, paths, patch_hash = self._apply(workspace, payload, task)
            except Exception as exc:
                raise LifecycleError(str(exc)) from exc
            tests = self._verify(workspace, task, deadline)
            if not tests["passed"]:
                raise LifecycleError("worker_validation_failed")
            if not isinstance(usage, dict) or any(
                type(usage.get(name)) is not int or usage[name] < 0
                for name in ("input_tokens", "output_tokens")
            ):
                raise LifecycleError("provider_usage_unavailable")
            actual = usage["input_tokens"] * input_price
            actual += usage["output_tokens"] * output_price
            actual /= 1_000_000
            if actual > task.max_cost_usd:
                raise LifecycleError("provider_cost_exceeded")
            for generated in workspace.rglob("__pycache__"):
                shutil.rmtree(generated, ignore_errors=True)
            for generated in (workspace / ".pytest_cache", workspace / ".ruff_cache"):
                shutil.rmtree(generated, ignore_errors=True)
            status_lines = self._run_git("status", "--porcelain", cwd=workspace).splitlines()
            if any(line[:2] not in {" M", "M ", "MM"} for line in status_lines):
                raise LifecycleError("worker_generated_untracked_files")
            return {
                "proposal": payload["explanation"],
                "diff_sha256": patch_hash,
                "diff": patch,
                "tests": tests,
                "security": {"passed": True, "checks": ["allow_list", "source_sha", "ruff"]},
                "changed_files": paths,
                "provider_usage": {
                    **usage,
                    "estimated_cost_usd": actual,
                    "worst_case_cost_usd": worst_cost,
                },
                "worktree": str(workspace),
            }
        except BaseException:
            # The worktree is intentionally retained for forensic evidence, but
            # no Git integration operation is ever attempted.
            raise
