import hashlib
import json
import pytest

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["stale", "redacted", "missing_delete"])
async def test_prewrite_rejection_is_recorded_as_safe_negative(tmp_path, monkeypatch, scenario):
    root = tmp_path / "repo"
    root.mkdir()
    backend = OpenAIResponsesDeveloperBackend(
        root, tmp_path / "state", client=object(), key="fixture-no-provider-calls"
    )
    thread = backend.state.create(str(root))
    thread.update(turn_id="turn", status="running")
    owned = backend.runner.workspaces / thread["id"]
    owned.mkdir(parents=True)
    thread["worktree"] = str(owned)
    thread["branch"] = "jarvis/developer-" + thread["id"][4:]
    backend.state.save(thread)

    async def trusted_branch(_thread):
        return owned

    async def no_reconcile():
        pass

    monkeypatch.setattr(backend.runner, "assert_branch", trusted_branch)
    monkeypatch.setattr(backend.runner, "reconcile", no_reconcile)

    path = owned / "fixture.py"
    original = b"OK = 1\n"
    if scenario != "missing_delete":
        path.write_bytes(original)
    expected = hashlib.sha256(original).hexdigest() if path.exists() else ""
    arguments = {
        "path": "fixture.py",
        "expected_sha256": "0" * 64 if scenario == "stale" else expected,
        "content": None
        if scenario == "missing_delete"
        else ("OK = '[REDACTED]'\n" if scenario == "redacted" else "OK = 2\n"),
    }
    call = {
        "name": "patch_file",
        "call_id": "call_prewrite_test",
        "arguments": json.dumps(arguments),
    }
    result = await backend.tool(thread, call)
    assert result["ok"] is False
    assert result["code"] == "file_policy_rejected"
    assert result["retryable"] is False
    assert (path.read_bytes() == original) if path.exists() else scenario == "missing_delete"
    row = backend.state.db.execute(
        "SELECT state FROM calls WHERE thread=?", (thread["id"],)
    ).fetchone()
    assert row["state"] == "completed"
    assert await backend.tool(thread, call) == result
    backend.state.close()
