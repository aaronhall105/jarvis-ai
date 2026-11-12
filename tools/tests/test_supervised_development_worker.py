from __future__ import annotations

import subprocess
import sys
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_worker import BoundedDevelopmentWorker


class RecordingLedger:
    """Accounting test double; admission is exercised by the pipeline suite."""

    def __init__(self):
        self.calls = {}
        self.deadline_monotonic = time.monotonic() + 120

    def reserve(self, call_id, worst_case_usd):
        assert call_id not in self.calls
        self.calls[call_id] = {"outcome": "reserved", "worst_case_usd": worst_case_usd}

    def completed(self, call_id, actual_usd):
        assert self.calls[call_id]["outcome"] == "reserved"
        self.calls[call_id].update(outcome="completed", actual_usd=actual_usd)

    def uncertain(self, call_id):
        self.calls[call_id]["outcome"] = "uncertain"


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    (root / "diagnostic.py").write_text("VALUE = 1\n")
    (root / "test_diagnostic.py").write_text(
        "from diagnostic import VALUE\n\n\ndef test_value():\n    assert VALUE == 1\n"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return root, sha


def _task(sha: str) -> SimpleNamespace:
    return SimpleNamespace(
        objective="Improve the diagnostic warning.",
        source_sha=sha,
        allowed_paths=("diagnostic.py", "test_diagnostic.py"),
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=1.0,
        deadline_seconds=120,
        request_ref="request-1",
    )


def test_bounded_worker_makes_one_call_and_never_integrates(tmp_path):
    root, sha = _repo(tmp_path)
    git_metadata_before = sorted((root / ".git").rglob("*"))
    calls = []

    def provider(prompt, model, timeout):
        calls.append((prompt, model, timeout))
        return (
            '{"protocol_version":1,"explanation":"Bound warning.","edits":'
            '[{"path":"diagnostic.py","old_text":"VALUE = 1\\n",'
            '"new_text":"VALUE = 2\\n"},{"path":"test_diagnostic.py",'
            '"old_text":"assert VALUE == 1","new_text":"assert VALUE == 2"}]}',
            {"input_tokens": 100, "output_tokens": 40},
        )

    worker = BoundedDevelopmentWorker(
        root,
        worktree_root=tmp_path / "worktrees",
        provider_call=provider,
        input_price_per_million=10,
        output_price_per_million=10,
    )
    result = worker.run(_task(sha), call_ledger=RecordingLedger())
    assert len(calls) == 1
    assert worker.actual_provider_calls == 1
    assert result["changed_files"] == ["diagnostic.py", "test_diagnostic.py"]
    assert result["tests"]["passed"] is True
    assert (
        subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        == sha
    )
    assert not (root / ".git" / "refs" / "heads" / "jarvis").exists()
    assert sorted((root / ".git").rglob("*")) == git_metadata_before


def test_bounded_worker_rejects_invalid_output_without_partial_edit(tmp_path):
    root, sha = _repo(tmp_path)

    def provider(prompt, model, timeout):
        return (
            '{"protocol_version":1,"explanation":"bad","edits":[{"path":"other.py","old_text":"x","new_text":"y"}]}',
            {"input_tokens": 1, "output_tokens": 1},
        )

    worker = BoundedDevelopmentWorker(
        root,
        worktree_root=tmp_path / "worktrees",
        provider_call=provider,
        input_price_per_million=10,
        output_price_per_million=10,
    )
    with pytest.raises(LifecycleError, match="outside|target"):
        worker.run(_task(sha), call_ledger=RecordingLedger())
    workspace = next((tmp_path / "worktrees").iterdir())
    assert (workspace / "diagnostic.py").read_text() == "VALUE = 1\n"


def test_bounded_worker_fails_closed_when_cost_cannot_be_bounded(tmp_path):
    root, sha = _repo(tmp_path)
    called = False

    def provider(prompt, model, timeout):
        nonlocal called
        called = True
        return "{}", {}

    worker = BoundedDevelopmentWorker(
        root, worktree_root=tmp_path / "worktrees", provider_call=provider
    )
    with pytest.raises(LifecycleError, match="pricing"):
        worker.run(_task(sha), call_ledger=RecordingLedger())
    assert called is False


def test_openai_transport_disables_sdk_retries_and_makes_one_request(monkeypatch):
    calls = []

    class FakeResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output_text='{"protocol_version":1,"explanation":"ok","edits":[]}',
                usage=SimpleNamespace(input_tokens=3, output_tokens=2, reasoning_tokens=0),
            )

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            assert kwargs["timeout"] == 7
            self.responses = FakeResponses()

    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    def client_options(**kwargs):
        assert kwargs == {"follow_redirects": False, "trust_env": False}
        return None

    monkeypatch.setitem(
        sys.modules, "openai", SimpleNamespace(OpenAI=FakeClient, DefaultHttpxClient=client_options)
    )
    worker = BoundedDevelopmentWorker(
        Path("."), worktree_root=Path("."), input_price_per_million=1, output_price_per_million=1
    )
    text, usage = worker._openai_call("{}", "gpt-5.3-codex", 7)
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-5.3-codex"
    assert calls[0]["store"] is False
    assert usage["input_tokens"] == 3
    assert '"edits":[]' in text


def _edit(path, old, new):
    return {"path": path, "old_text": old, "new_text": new}


def _reply(*edits):
    return json.dumps(
        {"protocol_version": 1, "explanation": "Evidence-based correction.", "edits": list(edits)}
    ), {"input_tokens": 100, "output_tokens": 40}


def _worker(tmp_path, root, provider):
    return BoundedDevelopmentWorker(
        root,
        worktree_root=tmp_path / "worktrees",
        provider_call=provider,
        input_price_per_million=1,
        output_price_per_million=1,
    )


def test_no_accounting_or_failed_reservation_prevents_transport(tmp_path):
    root, sha = _repo(tmp_path)
    calls = []
    worker = _worker(tmp_path, root, lambda *args: calls.append(args))
    with pytest.raises(LifecycleError, match="durable_provider_ledger_required"):
        worker.run(_task(sha))

    class DeniedLedger(RecordingLedger):
        def reserve(self, call_id, worst_case_usd):
            raise LifecycleError("provider_budget_exhausted")

    with pytest.raises(LifecycleError, match="provider_budget_exhausted"):
        worker.run(_task(sha), call_ledger=DeniedLedger())
    assert calls == []
    assert worker.actual_provider_calls == 0


def test_real_failed_test_is_repaired_in_second_approved_call(tmp_path):
    from tools.supervised_development_review import TrustedReviewRunner

    root, sha = _repo(tmp_path)
    calls = []
    ledger = RecordingLedger()

    def provider(prompt, model, timeout):
        assert list(ledger.calls.values())[-1]["outcome"] == "reserved"
        calls.append(json.loads(prompt))
        if len(calls) == 1:
            return _reply(_edit("diagnostic.py", "VALUE = 1", "VALUE = 2"))
        assert "assert 2 == 1" in calls[-1]["local_verification_failure"]
        return _reply(_edit("test_diagnostic.py", "assert VALUE == 1", "assert VALUE == 2"))

    worker = _worker(tmp_path, root, provider)
    task = _task(sha)
    task.max_calls = 2
    evidence = worker.run(task, call_ledger=ledger)
    assert len(calls) == worker.actual_provider_calls == 2
    assert all(call["outcome"] == "completed" for call in ledger.calls.values())
    assert evidence["provider_usage"]["actual_provider_calls"] == 2
    review = TrustedReviewRunner(
        Path(evidence["worktree"]), base_sha=sha, allowed_paths=task.allowed_paths
    ).review(task, evidence)
    assert review["verified"] is True
    assert "isolated_execution_receipt" in review["checks"]


def test_one_call_default_never_repairs_automatically(tmp_path):
    root, sha = _repo(tmp_path)
    ledger = RecordingLedger()
    worker = _worker(
        tmp_path, root, lambda *args: _reply(_edit("diagnostic.py", "VALUE = 1", "VALUE = 2"))
    )
    with pytest.raises(LifecycleError, match="worker_validation_failed"):
        worker.run(_task(sha), call_ledger=ledger)
    assert worker.actual_provider_calls == len(ledger.calls) == 1


def test_transport_uncertainty_never_replayed(tmp_path):
    root, sha = _repo(tmp_path)
    ledger = RecordingLedger()

    def broken_transport(*args):
        raise TimeoutError("fixture transport interrupted")

    worker = _worker(tmp_path, root, broken_transport)
    task = _task(sha)
    task.max_calls = 4
    with pytest.raises(TimeoutError):
        worker.run(task, call_ledger=ledger)
    assert [call["outcome"] for call in ledger.calls.values()] == ["uncertain"]
    with pytest.raises(LifecycleError, match="already_used"):
        worker.run(task, call_ledger=ledger)


@pytest.mark.parametrize(
    "usage",
    [
        {},
        {"input_tokens": -1, "output_tokens": 2},
        {"input_tokens": True, "output_tokens": 2},
        {"input_tokens": 13000, "output_tokens": 0},
    ],
)
def test_invalid_or_over_limit_actual_usage_requires_reconciliation(tmp_path, usage):
    root, sha = _repo(tmp_path)
    ledger = RecordingLedger()
    worker = _worker(tmp_path, root, lambda *args: ("{}", usage))
    with pytest.raises(LifecycleError, match="usage|cost"):
        worker.run(_task(sha), call_ledger=ledger)
    call = next(iter(ledger.calls.values()))
    if usage.get("input_tokens") == 13000:
        assert call["outcome"] == "completed"
        assert call["actual_usd"] == 0.013
    else:
        assert call["outcome"] == "uncertain"
    workspace = next((tmp_path / "worktrees").iterdir())
    assert (workspace / "diagnostic.py").read_text() == "VALUE = 1\n"


def test_actual_namespace_hides_credentials_filesystem_git_and_network(tmp_path, monkeypatch):
    root, _ = _repo(tmp_path)
    outside = tmp_path / "private-fixture"
    outside.write_text("private fixture")
    monkeypatch.setenv("UNRELATED_DATABASE_PASSWORD", "test-only-secret")
    worker = _worker(tmp_path, root, lambda *args: None)
    probe = (
        "import os,pathlib,socket,subprocess; "
        "assert 'UNRELATED_DATABASE_PASSWORD' not in os.environ; "
        f"assert not pathlib.Path({str(outside)!r}).exists(); "
        "assert not pathlib.Path('/workspace/.git/HEAD').exists(); "
        "assert all(n == 'lo' for _, n in socket.if_nameindex()); "
        "assert subprocess.run(['/usr/bin/git','commit','-am','forbidden'],capture_output=True).returncode != 0; "
        "assert subprocess.run(['/usr/bin/git','push'],capture_output=True).returncode != 0; "
        "assert subprocess.run(['/usr/bin/touch','/workspace/forbidden'],capture_output=True).returncode != 0; "
        "assert not pathlib.Path('/var/run/docker.sock').exists()"
    )
    code, output = worker._sandbox_run(root, [sys.executable, "-c", probe], time.monotonic() + 10)
    assert code == 0, output
    assert not (root / "forbidden").exists()
    assert outside.read_text() == "private fixture"


def test_missing_sandbox_denies_before_provider(tmp_path, monkeypatch):
    root, sha = _repo(tmp_path)
    calls = []
    worker = _worker(tmp_path, root, lambda *args: calls.append(args))
    monkeypatch.setattr(
        worker, "_sandbox_run", lambda *args: (1, "namespace denied by host policy")
    )
    with pytest.raises(LifecycleError, match="sandbox_unavailable"):
        worker.run(_task(sha), call_ledger=RecordingLedger())
    assert calls == []


def test_new_signed_file_passes_scope_and_actual_review(tmp_path):
    from tools.supervised_development_review import TrustedReviewRunner

    root, sha = _repo(tmp_path)
    worker = _worker(
        tmp_path,
        root,
        lambda *args: _reply(
            _edit("new_feature.py", "", "VALUE = 3\n"),
            _edit(
                "test_new_feature.py",
                "",
                "from new_feature import VALUE\n\n\ndef test_value():\n    assert VALUE == 3\n",
            ),
        ),
    )
    task = _task(sha)
    task.allowed_paths = ("new_feature.py", "test_new_feature.py")
    evidence = worker.run(task, call_ledger=RecordingLedger())
    workspace = Path(evidence["worktree"])
    assert TrustedReviewRunner(workspace, base_sha=sha, allowed_paths=task.allowed_paths).review(
        task, evidence
    )["verified"]
    assert (
        subprocess.check_output(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True
        ).strip()
        == sha
    )
    assert (
        subprocess.check_output(
            ["git", "-C", str(workspace), "diff", "--cached", "--name-only"], text=True
        )
        == ""
    )


def test_test_discovery_is_not_positional_and_unrelated_tests_do_not_suffice(tmp_path):
    root, sha = _repo(tmp_path)
    worker = _worker(tmp_path, root, lambda *args: None)
    task = _task(sha)
    task.allowed_paths = tuple(reversed(task.allowed_paths))
    assert worker._test_targets(root, task, ["diagnostic.py"]) == ["test_diagnostic.py"]
    with pytest.raises(LifecycleError, match="no_relevant_tests"):
        worker._test_targets(root, task, ["uncovered.py", "test_diagnostic.py"])


def test_sandbox_timeout_kills_execution_and_leaves_source_unchanged(tmp_path):
    root, _ = _repo(tmp_path)
    worker = _worker(tmp_path, root, lambda *args: None)
    with pytest.raises(subprocess.TimeoutExpired):
        worker._sandbox_run(
            root, [sys.executable, "-c", "import time; time.sleep(10)"], time.monotonic() + 0.1
        )
    assert (root / "diagnostic.py").read_text() == "VALUE = 1\n"


@pytest.mark.parametrize("price", [float("inf"), float("nan"), -1, 0, True])
def test_invalid_pricing_is_not_an_authorisation_to_spend(tmp_path, price):
    root, sha = _repo(tmp_path)
    worker = _worker(tmp_path, root, lambda *args: pytest.fail("provider must not run"))
    worker.input_price = price
    with pytest.raises(LifecycleError, match="pricing"):
        worker.run(_task(sha), call_ledger=RecordingLedger())


def test_prompt_bound_is_conservative_for_non_ascii(tmp_path):
    root, sha = _repo(tmp_path)
    worker = _worker(tmp_path, root, lambda *args: None)
    with pytest.raises(LifecycleError, match="prompt_too_large"):
        worker._prompt(_task(sha), {"diagnostic.py": "🛑" * 2000})


@pytest.mark.parametrize("status", [200, 307, 429, 500])
def test_actual_openai_http_transport_never_retries_or_follows_redirects(
    tmp_path, monkeypatch, status
):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    import openai

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(request)
            output, usage = _reply(_edit("diagnostic.py", "VALUE = 1", "VALUE = 2"))
            payload = {
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-5.3-codex",
                "output": [
                    {
                        "id": "msg_test",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": output, "annotations": []}],
                    }
                ],
                "usage": {**usage, "total_tokens": sum(usage.values())},
            }
            if status != 200:
                payload = {
                    "error": {"message": "disposable transport fixture", "type": "fixture_error"}
                }
            encoded = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            if status == 307:
                self.send_header("Location", "/redirect")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("OPENAI_API_KEY", "test-fixture-not-a-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
    try:
        worker = BoundedDevelopmentWorker(tmp_path, worktree_root=tmp_path)
        if status == 200:
            output, usage = worker._openai_call("{}", "gpt-5.3-codex", 5)
            assert usage["input_tokens"] == 100
            assert json.loads(output)["protocol_version"] == 1
        else:
            with pytest.raises(openai.APIStatusError):
                worker._openai_call("{}", "gpt-5.3-codex", 5)
        assert len(calls) == 1
        assert calls[0]["model"] == "gpt-5.3-codex"
        assert calls[0]["max_output_tokens"] == 4000
        assert calls[0]["store"] is False
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_durable_deadline_prevents_even_source_clone(tmp_path):
    root, sha = _repo(tmp_path)
    worker = _worker(tmp_path, root, lambda *args: pytest.fail("transport forbidden"))
    ledger = RecordingLedger()
    ledger.deadline_monotonic = time.monotonic() - 1
    with pytest.raises(LifecycleError, match="deadline_expired"):
        worker.run(_task(sha), call_ledger=ledger)
    assert not (tmp_path / "worktrees").exists()
    assert not ledger.calls


def test_addition_and_replacement_roll_back_together_when_patch_policy_fails(tmp_path):
    root, sha = _repo(tmp_path)
    worker = _worker(tmp_path, root, lambda *args: None)
    worker.max_patch_lines = 2
    task = _task(sha)
    task.allowed_paths += ("new_feature.py",)
    payload = json.loads(
        _reply(
            _edit("diagnostic.py", "VALUE = 1", "VALUE = 2"),
            _edit("new_feature.py", "", "VALUE = 3\n"),
        )[0]
    )
    with pytest.raises(LifecycleError, match="patch line limit"):
        worker._apply(root, payload, task)
    assert not (root / "new_feature.py").exists()
    assert (root / "diagnostic.py").read_text() == "VALUE = 1\n"
    assert (
        subprocess.check_output(["git", "-C", str(root), "status", "--porcelain"], text=True) == ""
    )


def test_application_modules_cannot_shadow_trusted_verifier_tools(tmp_path):
    root, _ = _repo(tmp_path)
    for name in ("pytest", "ruff", "bandit", "ast", "pathlib"):
        (root / f"{name}.py").write_text(
            "raise RuntimeError('untrusted verifier shadow executed')\n"
        )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-qm", "untrusted module fixtures"], check=True
    )
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    worker = _worker(
        tmp_path,
        root,
        lambda *args: _reply(
            _edit("diagnostic.py", "VALUE = 1", "VALUE = 2"),
            _edit("test_diagnostic.py", "assert VALUE == 1", "assert VALUE == 2"),
        ),
    )
    evidence = worker.run(_task(sha), call_ledger=RecordingLedger())
    assert evidence["tests"]["passed"]
    assert evidence["tests"]["checks"][0]["test_counts"]["executed"] == 1


@pytest.mark.parametrize(
    "body",
    [
        "import pytest\n\ndef test_skip():\n    pytest.skip('not validation')\n",
        "import os\n\ndef test_exit():\n    os._exit(0)\n",
        "import os\n\ndef test_canned_success():\n"
        "    os.write(1, b'JARVIS_EXECUTED_TESTS="
        '{"collected":1,"executed":1,"failed":0,"skipped":0}\\n'
        "')\n"
        "    os._exit(0)\n",
    ],
)
def test_zero_executed_tests_or_early_success_exit_is_not_verification(tmp_path, body):
    root, sha = _repo(tmp_path)
    original = (root / "test_diagnostic.py").read_text()
    worker = _worker(
        tmp_path, root, lambda *args: _reply(_edit("test_diagnostic.py", original, body))
    )
    with pytest.raises(LifecycleError, match="worker_validation_failed"):
        worker.run(_task(sha), call_ledger=RecordingLedger())
    assert worker.actual_provider_calls == 1
