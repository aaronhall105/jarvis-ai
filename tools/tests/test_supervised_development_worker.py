from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.supervised_development_controller import LifecycleError
from tools.supervised_development_worker import BoundedDevelopmentWorker


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
    result = worker.run(_task(sha))
    assert len(calls) == 1
    assert worker.actual_provider_calls == 1
    assert result["changed_files"] == ["diagnostic.py", "test_diagnostic.py"]
    assert result["tests"]["passed"] is True
    assert (
        subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        == sha
    )
    assert not (root / ".git" / "refs" / "heads" / "jarvis").exists()


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
        worker.run(_task(sha))
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
        worker.run(_task(sha))
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
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeClient))
    worker = BoundedDevelopmentWorker(
        Path("."), worktree_root=Path("."), input_price_per_million=1, output_price_per_million=1
    )
    text, usage = worker._openai_call("{}", "gpt-5.3-codex", 7)
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-5.3-codex"
    assert calls[0]["store"] is False
    assert usage["input_tokens"] == 3
    assert '"edits":[]' in text
