"""Durably bounded coding/repair worker; generated checks run in an isolated namespace."""

from __future__ import annotations
import hashlib
import json
import math
import os
import resource
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Protocol
from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_edits import StructuredEditError, apply_incremental_edits
from tools.supervised_development_environment import CACHE_DIRECTORY, worker_python_arguments
from tools.supervised_development_scope import editable_path

ProviderCall = Callable[[str, str, int], tuple[str, dict[str, Any]]]
CHECK_NAMES = ("pytest", "ruff", "bandit", "compile")
PYTEST_RESULT_PREFIX = "JARVIS_EXECUTED_TESTS="
PYTEST_BOOTSTRAP = """import importlib.metadata,json,sys,pytest
completion_marker = sys.argv.pop(1)
# Load verifier and installed plugins before application import paths.
plugins = [entry.load() for entry in importlib.metadata.entry_points(group='pytest11')]
class Results:
    executed = failed = skipped = collected = 0
    def pytest_runtest_logreport(self, report):
        if report.skipped:
            self.skipped += 1
        if report.when == 'call' and not report.skipped:
            self.executed += 1
            self.failed += int(report.failed)
    def pytest_sessionfinish(self, session, exitstatus):
        self.collected = session.testscollected
record = Results()
sys.path[:0] = ['/workspace', '/workspace/bridge']
code = pytest.main(sys.argv[1:], plugins=[*plugins, record])
print(completion_marker + json.dumps({name: getattr(record,name) for name in ('executed','failed','skipped','collected')}, sort_keys=True))
raise SystemExit(code)
"""


class CallLedger(Protocol):
    @property
    def deadline_monotonic(self) -> float: ...

    def reserve(self, call_id: str, worst_case_usd: float) -> None: ...
    def completed(self, call_id: str, actual_usd: float) -> None: ...
    def uncertain(self, call_id: str) -> None: ...


def verification_receipt(workspace: Path) -> Path:
    """Outside the only candidate mount accessible to generated code."""
    return workspace.parent / (workspace.name + ".verification.json")


class BoundedDevelopmentWorker:
    """Execute only signed call/cost/deadline limits; never integrate Git."""

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
        max_patch_lines: int = 400,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.worktree_root = Path(worktree_root).resolve()
        self.provider_call = provider_call or self._openai_call
        self.input_price, self.output_price = input_price_per_million, output_price_per_million
        self.max_input_tokens, self.max_output_tokens = max_input_tokens, max_output_tokens
        self.max_patch_lines, self.actual_provider_calls = max_patch_lines, 0
        self._failure_feedback = ""

    def _pricing(self) -> tuple[float, float]:
        prices: list[float] = []
        for value in (self.input_price, self.output_price):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise LifecycleError("provider_pricing_unverified")
            prices.append(float(value))
        return prices[0], prices[1]

    def _openai_call(self, prompt: str, model: str, timeout: int) -> tuple[str, dict[str, Any]]:
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            raise LifecycleError("provider_credential_unavailable")
        try:
            from openai import DefaultHttpxClient, OpenAI
        except ImportError as exc:
            raise LifecycleError("provider_sdk_unavailable") from exc
        client = OpenAI(
            api_key=key,
            timeout=max(1, timeout),
            max_retries=0,
            http_client=DefaultHttpxClient(follow_redirects=False, trust_env=False),
        )
        response = client.responses.create(
            model=model,
            instructions="Return strict JSON only with exact structured edits. Never emit commands, a Git patch, credentials, or prose outside JSON.",
            input=prompt,
            max_output_tokens=self.max_output_tokens,
            store=False,
        )
        usage = getattr(response, "usage", None)
        # Missing usage is not zero cost: it requires reconciliation.
        return str(getattr(response, "output_text", "")), {
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        }

    def _run_git(self, *args: str, cwd: Path) -> str:
        return subprocess.run(
            [
                "/usr/bin/git",
                "-c",
                "safe.directory=" + str(cwd),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.attributesFile=/dev/null",
                *args,
            ],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
            },
        ).stdout.strip()

    def _prompt(self, task: Any, files: dict[str, str]) -> str:
        encoded = json.dumps(
            {
                "protocol_version": 1,
                "objective": task.objective,
                "base_source_sha": task.source_sha,
                "allowed_paths": list(task.allowed_paths),
                "provider": task.provider,
                "model": task.model,
                "acceptance": [
                    "Implement only the approved objective, preserving public contracts and security.",
                    "Repair actual local verification failures; no commands, commits, pushes, PRs or deployments.",
                    "Return exact old_text/new_text edits. Empty old_text is permitted only for an explicitly approved new path.",
                ],
                "source": files,
                "local_verification_failure": self._failure_feedback,
                "response_schema": {
                    "protocol_version": 1,
                    "explanation": "string",
                    "edits": [{"path": "string", "old_text": "string", "new_text": "string"}],
                },
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        # UTF-8 bytes conservatively bound tokens, unlike chars/4. Reserve framing.
        if len(encoded.encode()) + 1024 > self.max_input_tokens:
            raise LifecycleError("provider_prompt_too_large")
        return encoded

    def _apply(
        self, workspace: Path, payload: dict[str, Any], task: Any
    ) -> tuple[str, list[str], str]:
        try:
            return apply_incremental_edits(
                workspace,
                payload,
                {"allowed_edit_paths": list(task.allowed_paths), "forbidden_paths": []},
                SimpleNamespace(
                    max_patch_lines=self.max_patch_lines, max_changed_files=len(task.allowed_paths)
                ),
                allow_additions=True,
            )
        except StructuredEditError as exc:
            raise LifecycleError(str(exc)) from exc

    @staticmethod
    def _sandbox_command(workspace: Path, command: list[str]) -> list[str]:
        executable = Path("/usr/bin/bwrap")
        if not executable.is_file() or executable.is_symlink():
            raise LifecycleError("verification_sandbox_unavailable")
        st = executable.stat()
        if st.st_uid != 0 or st.st_mode & 0o022:
            raise LifecycleError("verification_sandbox_untrusted")
        args = [
            str(executable),
            "--unshare-user",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "--new-session",
            "--cap-drop",
            "ALL",
            "--ro-bind",
            "/usr/bin",
            "/usr/bin",
            "--ro-bind",
            "/usr/lib",
            "/usr/lib",
            "--ro-bind",
            "/usr/lib64",
            "/usr/lib64",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            # A fresh proc mount is denied below systemd's locked /proc
            # submounts (ProtectKernelTunables/ProtectControlGroups). Verifiers
            # need no process inventory: keep proc empty and read-only, never
            # bind host proc or relax the parent's restrictions.
            "--tmpfs",
            "/proc",
            "--remount-ro",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
        ]
        prefix = Path(sys.prefix).resolve()
        if command and command[0] == sys.executable:
            # -B alone prevents writes, but still reads timestamp-based .pyc
            # files. Every fresh verifier interpreter must use the same empty,
            # read-only cache prefix as the sealed worker. Local test virtual
            # environments may omit it: their entire prefix is mounted RO, so
            # the absent cache cannot be populated from inside this namespace.
            cache = prefix / CACHE_DIRECTORY
            try:
                if cache.is_symlink() or (
                    cache.exists() and (not cache.is_dir() or any(cache.iterdir()))
                ):
                    raise LifecycleError("verification_bytecode_cache_untrusted")
            except OSError as exc:
                raise LifecycleError("verification_bytecode_cache_untrusted") from exc
            command = [
                command[0],
                *worker_python_arguments(prefix / "bin" / "python"),
                *command[1:],
            ]
        if prefix != Path("/usr") and not any(
            prefix.is_relative_to(root) for root in ("/usr/bin", "/usr/lib", "/usr/lib64")
        ):
            args += ["--ro-bind", str(prefix), str(prefix)]
        return args + [
            "--ro-bind",
            str(workspace),
            "/workspace",
            "--tmpfs",
            "/workspace/.git",
            "--remount-ro",
            "/workspace/.git",
            "--chdir",
            "/workspace",
            "--clearenv",
            "--setenv",
            "PATH",
            str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/nonexistent",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--setenv",
            "PYTHONPATH",
            "/workspace:/workspace/bridge",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
            "--setenv",
            "PYTHONNOUSERSITE",
            "1",
            "--setenv",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
            "1",
            "--setenv",
            "GIT_CONFIG_NOSYSTEM",
            "1",
            "--setenv",
            "GIT_CONFIG_GLOBAL",
            "/dev/null",
            "--",
            *command,
        ]

    def _sandbox_run(self, workspace: Path, command: list[str], deadline: float) -> tuple[int, str]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LifecycleError("deadline_expired")

        def limits() -> None:
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_FSIZE, (1_048_576, 1_048_576))
            resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
            resource.setrlimit(resource.RLIMIT_NPROC, (512, 512))
            resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(remaining), math.ceil(remaining)))

        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                self._sandbox_command(workspace, command),
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                env={"PATH": "/usr/bin:/bin"},
                start_new_session=True,
                preexec_fn=limits,
            )
            try:
                process.wait(timeout=remaining)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
            output.seek(0)
            return process.returncode, output.read(1_048_576).decode("utf-8", "replace")

    def _sandbox_preflight(self, workspace: Path, deadline: float) -> None:
        try:
            code, _ = self._sandbox_run(
                workspace,
                [
                    sys.executable,
                    "-I",
                    "-c",
                    "import pytest,ruff,bandit,socket; assert all(n == 'lo' for _,n in socket.if_nameindex())",
                ],
                deadline,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LifecycleError("verification_sandbox_unavailable") from exc
        if code:
            raise LifecycleError("verification_sandbox_unavailable")

    def _test_targets(self, workspace: Path, task: Any, changed: list[str]) -> list[str]:
        tracked = self._run_git(
            "ls-tree", "-r", "--name-only", task.source_sha, cwd=workspace
        ).splitlines()
        existing = [p for p in tracked if Path(p).name.startswith("test_") and p.endswith(".py")]
        targets = {p for p in changed if p.endswith(".py") and Path(p).name.startswith("test_")}
        for path in changed:
            if path in targets:
                continue
            matches = {
                p for p in [*existing, *targets] if Path(p).name == f"test_{Path(path).stem}.py"
            }
            if "/" in path:
                matches.update(
                    p for p in existing if p.startswith(path.split("/", 1)[0] + "/tests/")
                )
            if not matches:
                raise LifecycleError("no_relevant_tests_discovered")
            targets.update(matches)
        if not targets or any(not (workspace / p).is_file() for p in targets):
            raise LifecycleError("no_relevant_tests_discovered")
        return sorted(targets)

    def _verify(self, workspace: Path, task: Any, deadline: float) -> dict[str, Any]:
        changed = self._run_git("diff", "--name-only", "HEAD", cwd=workspace).splitlines()
        targets = self._test_targets(workspace, task, changed)
        python_paths = [p for p in changed if p.endswith((".py", ".pyi"))]
        if not python_paths:
            raise LifecycleError("verification_language_unsupported")
        # Correlate completion to this invocation; a canned success line plus
        # os._exit(0) is not completion. This is NOT a secret/authentication
        # boundary: arbitrary in-process Python can inspect interpreter state.
        # Receipts establish actual isolated command outcomes, not semantic
        # adequacy or adversarial integrity of modified test assertions. Those
        # require the separately controlled independent human review gate.
        completion_marker = PYTEST_RESULT_PREFIX + uuid.uuid4().hex + ":"
        commands = {
            "pytest": [
                sys.executable,
                "-I",
                "-c",
                PYTEST_BOOTSTRAP,
                completion_marker,
                "-p",
                "no:cacheprovider",
                *targets,
            ],
            "ruff": [sys.executable, "-I", "-m", "ruff", "check", "--no-cache", *python_paths],
            "bandit": [
                sys.executable,
                "-I",
                "-m",
                "bandit",
                "-q",
                "-lll",
                "--ignore-nosec",
                *python_paths,
            ],
            "compile": [
                sys.executable,
                "-I",
                "-c",
                "import ast,pathlib,sys; [ast.parse(pathlib.Path(p).read_text()) for p in sys.argv[1:]]",
                *python_paths,
            ],
        }
        checks = []
        for name, command in commands.items():
            started = time.monotonic()
            try:
                code, output = self._sandbox_run(workspace, command, deadline)
            except (OSError, subprocess.SubprocessError) as exc:
                raise LifecycleError("verification_interrupted") from exc
            checks.append(
                {
                    "name": name,
                    "command": command,
                    "returncode": code,
                    "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                    "output_sha256": hashlib.sha256(output.encode()).hexdigest(),
                }
            )
            if name == "pytest":
                reports = [
                    line[len(completion_marker) :]
                    for line in output.splitlines()
                    if line.startswith(completion_marker)
                ]
                try:
                    counts = json.loads(reports[0]) if len(reports) == 1 else None
                except ValueError:
                    counts = None
                if (
                    not isinstance(counts, dict)
                    or any(
                        type(counts.get(key)) is not int or counts[key] < 0
                        for key in ("executed", "failed", "skipped", "collected")
                    )
                    or counts["executed"] <= 0
                    or counts["collected"] < counts["executed"]
                    or counts["failed"] != 0
                ):
                    self._failure_feedback = (
                        "pytest did not complete real passing test cases:\n" + output[-8192:]
                    )
                    return {"passed": False, "checks": checks, "error": "executed_tests_unverified"}
                checks[-1]["test_counts"] = counts
            if code:
                self._failure_feedback = f"{name} failed:\n" + output[-8192:]
                return {"passed": False, "checks": checks}
        return {"passed": True, "checks": checks}

    @staticmethod
    def _payload(raw: str) -> dict[str, Any]:
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LifecycleError("structured_response_invalid") from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"protocol_version", "explanation", "edits"}
            or payload.get("protocol_version") != 1
            or not isinstance(payload.get("explanation"), str)
            or not payload["explanation"].strip()
            or not isinstance(payload.get("edits"), list)
        ):
            raise LifecycleError("structured_response_schema")
        if any(
            not isinstance(e, dict) or set(e) != {"path", "old_text", "new_text"}
            for e in payload["edits"]
        ):
            raise LifecycleError("structured_response_edit_schema")
        return payload

    def run(self, task: Any, *, call_ledger: CallLedger | None = None) -> dict[str, Any]:
        if self.actual_provider_calls:
            raise LifecycleError("provider_call_already_used")
        if call_ledger is None:
            raise LifecycleError("durable_provider_ledger_required")
        if task.provider != "openai" or not isinstance(task.model, str) or not task.model.strip():
            raise LifecycleError("worker_provider_not_approved")
        if (
            type(task.max_calls) is not int
            or not 1 <= task.max_calls <= 4
            or type(task.deadline_seconds) is not int
            or not 0 < task.deadline_seconds <= 120
            or type(task.max_cost_usd) not in {int, float}
            or not math.isfinite(task.max_cost_usd)
            or not 0 < task.max_cost_usd <= 1
        ):
            raise LifecycleError("worker_limits_invalid")
        deadline = min(time.monotonic() + task.deadline_seconds, call_ledger.deadline_monotonic)
        if not math.isfinite(deadline) or deadline <= time.monotonic():
            raise LifecycleError("deadline_expired")
        if (
            any(
                type(v) is not int or v <= 0
                for v in (self.max_input_tokens, self.max_output_tokens, self.max_patch_lines)
            )
            or self.max_patch_lines > 2000
            or self.max_input_tokens > 1_000_000
            or self.max_output_tokens > 65_536
        ):
            raise LifecycleError("worker_patch_policy_invalid")
        if (
            not task.allowed_paths
            or len(set(task.allowed_paths)) != len(task.allowed_paths)
            or any(not editable_path(p) for p in task.allowed_paths)
        ):
            raise LifecycleError("worker_source_path_invalid")
        prices = self._pricing()
        worst = (self.max_input_tokens * prices[0] + self.max_output_tokens * prices[1]) / 1_000_000
        if worst > task.max_cost_usd:
            raise LifecycleError("provider_budget_preflight_failed")
        if self._run_git("rev-parse", "HEAD", cwd=self.source_root) != task.source_sha:
            raise LifecycleError("worker_source_drift")
        if self._run_git("status", "--porcelain", cwd=self.source_root):
            raise LifecycleError("worker_source_dirty")
        self.worktree_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace = self.worktree_root / ("task-" + uuid.uuid4().hex)
        self._run_git(
            "clone",
            "--no-local",
            "--no-hardlinks",
            "--no-checkout",
            str(self.source_root),
            str(workspace),
            cwd=self.worktree_root,
        )
        self._run_git("checkout", "--detach", task.source_sha, cwd=workspace)
        self._sandbox_preflight(workspace, deadline)
        total_cost, totals, attempts = 0.0, {"input_tokens": 0, "output_tokens": 0}, []
        for _ in range(task.max_calls):
            if total_cost + worst > task.max_cost_usd:
                raise LifecycleError("provider_budget_preflight_failed")
            files = {}
            for relative in task.allowed_paths:
                target = workspace / relative
                if (
                    target.is_symlink()
                    or not target.resolve().is_relative_to(workspace)
                    or (target.exists() and not target.is_file())
                ):
                    raise LifecycleError("worker_source_path_invalid")
                files[relative] = target.read_text(encoding="utf-8") if target.exists() else ""
            prompt = self._prompt(task, files)
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                raise LifecycleError("deadline_expired")
            call_id = str(uuid.uuid4())
            call_ledger.reserve(call_id, worst)
            self.actual_provider_calls += 1
            try:
                raw, usage = self.provider_call(prompt, task.model, remaining)
                if not isinstance(usage, dict) or any(
                    type(usage.get(n)) is not int or usage[n] < 0 for n in totals
                ):
                    raise LifecycleError("provider_usage_unavailable")
                actual = (
                    usage["input_tokens"] * prices[0] + usage["output_tokens"] * prices[1]
                ) / 1_000_000
            except BaseException:
                call_ledger.uncertain(call_id)
                raise
            # A measured charge remains evidence even when it violates policy.
            # Complete outside the network-exception handler: a committed cost
            # or an uncertain DB commit must not be overwritten as a second
            # provider outcome. Controller recovery handles persistence errors.
            call_ledger.completed(call_id, actual)
            if (
                usage["input_tokens"] > self.max_input_tokens
                or usage["output_tokens"] > self.max_output_tokens
                or total_cost + actual > task.max_cost_usd
            ):
                raise LifecycleError("provider_cost_exceeded")
            total_cost += actual
            for name in totals:
                totals[name] += usage[name]
            if time.monotonic() >= deadline:
                raise LifecycleError("deadline_expired")
            payload = self._payload(raw)
            patch, paths, patch_hash = self._apply(workspace, payload, task)
            tests = self._verify(workspace, task, deadline)
            attempts.append(
                {
                    "call_id": call_id,
                    "diff_sha256": patch_hash,
                    "checks": tests["checks"],
                    "passed": tests["passed"],
                }
            )
            if not tests["passed"]:
                continue
            if time.monotonic() >= deadline:
                raise LifecycleError("deadline_expired")
            if self._run_git(
                "rev-parse", "HEAD", cwd=workspace
            ) != task.source_sha or self._run_git("diff", "--cached", "--name-only", cwd=workspace):
                raise LifecycleError("worker_git_integration_detected")
            if self._run_git("ls-files", "--others", "--exclude-standard", cwd=workspace):
                raise LifecycleError("worker_generated_untracked_files")
            receipt = {
                "version": 1,
                "base_sha": task.source_sha,
                "diff_sha256": patch_hash,
                "changed_files": paths,
                "sandbox": "bubblewrap-read-only-v1",
                "checks": tests["checks"],
                "attempts": attempts,
            }
            encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
            fd = os.open(
                verification_receipt(workspace),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            return {
                "proposal": payload["explanation"],
                "diff_sha256": patch_hash,
                "diff": patch,
                "tests": tests,
                "security": {
                    "passed": True,
                    "checks": [
                        "allow_list",
                        "source_sha",
                        "network_and_filesystem_isolation",
                        "ruff",
                        "bandit",
                        "compile",
                    ],
                },
                "changed_files": paths,
                "provider_usage": {
                    **totals,
                    "actual_provider_calls": self.actual_provider_calls,
                    "estimated_cost_usd": total_cost,
                    "worst_case_cost_usd": worst,
                },
                "verification_receipt_sha256": hashlib.sha256(encoded).hexdigest(),
                "worktree": str(workspace),
            }
        raise LifecycleError("worker_validation_failed")
