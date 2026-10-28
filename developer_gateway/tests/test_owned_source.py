"""Owned-source reads and terminal file-policy results use disposable Git/SQLite."""

from pathlib import Path
import json
import os
import errno
from unittest.mock import AsyncMock

import pytest

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend, ProviderUnavailable
from developer_gateway.tests.test_api_backend import Client, call, response, start
from developer_gateway.tests.test_tools_isolation import repository  # noqa: F401
from developer_gateway.tests.test_recovery import active
from developer_gateway.tools import ToolPolicyError


@pytest.mark.asyncio
async def test_original_samsung_create_then_read_without_git_add(repository):
    thread = {"id": "api_owned"}
    await repository.execute(thread, "create_worktree", {})
    for path in ("tools/developer_session_diagnostics.py", "tools/tests/test_new.py"):
        await repository.execute(
            thread,
            "patch_file",
            {"path": path, "expected_sha256": "", "content": "value = 'owned_source'\n"},
        )
        result = await repository.execute(thread, "read_file", {"path": path, "start": 1})
        assert "owned_source" in result["text"]
    assert len(await repository.execute(thread, "search", {"text": "owned_source"})) == 2
    assert (await repository.git(Path(thread["worktree"]), "diff", "--cached"))["output"] == ""


@pytest.mark.asyncio
async def test_policy_denial_finishes_turn_not_recoverable(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        tmp_path / "agent",
        client=Client(
            response(call("read_file", {"path": ".env", "start": 1})),
            response(text="That path is not permitted."),
        ),
        key="",
    )
    thread = await start(backend)
    assert thread["status"] == "completed"
    row = backend.state.db.execute("SELECT state,result FROM calls").fetchone()
    assert row["state"] == "completed"
    assert '"retryable": false' in row["result"]
    items = thread["turns"][-1]["items"]
    assert any(i.get("id") == "call_one" and i["status"] == "failed" for i in items)
    backend.state.close()


@pytest.mark.asyncio
async def test_canonical_remains_tracked_only(repository):
    (repository.root / "new.py").write_text("hidden_new_source = 1")
    assert (
        "value = 1"
        in (
            await repository.execute(
                {"id": "api_owned"}, "read_file", {"path": "example.py", "start": 1}
            )
        )["text"]
    )
    with pytest.raises(ToolPolicyError):
        await repository.execute({"id": "api_owned"}, "read_file", {"path": "new.py", "start": 1})
    assert not await repository.execute(
        {"id": "api_owned"}, "search", {"text": "hidden_new_source"}
    )


@pytest.mark.parametrize(
    "kind",
    [
        "escape",
        "absolute",
        "symlink",
        "parent_symlink",
        "hardlink",
        "fifo",
        "directory",
        "ignored",
        "protected",
        "sensitive",
        "oversized",
        "binary",
        "invalid_utf8",
        "missing",
        "nul_path",
        "wrong_branch",
        "foreign_workspace",
        "foreign_repository",
    ],
)
@pytest.mark.asyncio
async def test_unsafe_new_sources_rejected(repository, tmp_path, kind):
    thread = {"id": "api_owned"}
    await repository.execute(thread, "create_worktree", {})
    root = Path(thread["worktree"])
    path = "new.py"
    target = root / path
    target.write_text("untracked_marker = 1")
    if kind == "escape":
        path = "../new.py"
    elif kind == "absolute":
        path = str(target)
    elif kind in {"symlink", "hardlink", "fifo", "directory"}:
        target.unlink()
        if kind == "symlink":
            target.symlink_to(repository.root / "example.py")
        elif kind == "hardlink":
            os.link(repository.root / "example.py", target)
        elif kind == "fifo":
            os.mkfifo(target)
        else:
            target.mkdir()
    elif kind == "parent_symlink":
        (root / "linked").symlink_to(repository.root, target_is_directory=True)
        path = "linked/example.py"
    elif kind == "ignored":
        (root / ".gitignore").write_text("new.py\n")
    elif kind in {"protected", "sensitive"}:
        path = "secrets/new.py" if kind == "protected" else "credentials.json"
        (root / path).parent.mkdir(exist_ok=True)
        (root / path).write_text("untracked_marker")
    elif kind == "oversized":
        target.write_bytes(b"x" * 500001)
    elif kind == "binary":
        target.write_bytes(b"untracked_marker\0")
    elif kind == "invalid_utf8":
        target.write_bytes(b"\xff")
    elif kind == "missing":
        path = "missing.py"
    elif kind == "nul_path":
        path = "bad\0.py"
    elif kind == "wrong_branch":
        await repository.git(root, "checkout", "--detach")
    elif kind == "foreign_workspace":
        thread["worktree"] = str(repository.root)
    elif kind == "foreign_repository":
        # Preserve the linked worktree metadata; replace just this disposable
        # fixture's pointer with a separate repository at the expected location.
        (root / ".git").rename(root / "saved-git-pointer")
        await repository.git(root, "init", "-b", thread["branch"])
    with pytest.raises(ToolPolicyError):
        await repository.execute(thread, "read_file", {"path": path, "start": 1})


@pytest.mark.asyncio
async def test_search_filters_ignored_secret_binary_and_symlink(repository):
    thread = {"id": "api_owned"}
    await repository.execute(thread, "create_worktree", {})
    root = Path(thread["worktree"])
    for name in ("good.py", "ignored.py", "credentials.json", "binary.py"):
        (root / name).write_text("search_marker")
    (root / "binary.py").write_bytes(b"search_marker\0")
    (root / ".gitignore").write_text("ignored.py\n")
    (root / "link.py").symlink_to(root / "good.py")
    assert [
        m["path"] for m in await repository.execute(thread, "search", {"text": "search_marker"})
    ] == ["good.py"]


@pytest.mark.asyncio
async def test_policy_result_cached_across_restart_and_identity_preserved(repository, tmp_path):
    directory = tmp_path / "agent"
    backend = OpenAIResponsesDeveloperBackend(repository.root, directory, client=Client(), key="")
    thread = active(backend.state, repository.root)
    denied = call("read_file", {"path": ".env", "start": 1})
    result = await backend.tool(thread, denied)
    assert result["code"] == "file_policy_rejected" and not result["retryable"]
    backend.state.close()
    restored = OpenAIResponsesDeveloperBackend(repository.root, directory, client=Client(), key="")
    restored.runner.execute = AsyncMock(side_effect=AssertionError("Completed call replayed"))
    assert await restored.tool(thread, denied) == result
    with pytest.raises(ValueError, match="identity"):
        await restored.tool(thread, call("read_file", {"path": "example.py", "start": 1}))
    public = restored.public_thread(restored.state.get(thread["id"]))
    assert public["turns"][-1]["items"][-1]["status"] == "failed"
    assert "instead of retrying" in public["turns"][-1]["items"][-1]["message"]
    assert ".env" not in json.dumps(public)
    restored.state.close()


@pytest.mark.asyncio
async def test_repeated_policy_denials_are_bounded_across_restart(repository, tmp_path):
    directory = tmp_path / "agent"
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        directory,
        client=Client(
            *(
                response(call("read_file", {"path": ".env", "start": 1}, f"deny_{i}"))
                for i in range(3)
            )
        ),
        key="",
    )
    thread = await start(backend)
    assert thread["status"] == "failed" and thread["policy_blocked"]
    assert thread["diagnostic"]["category"] == "policy_rejected"
    assert (
        backend.state.db.execute("SELECT count(*) FROM calls WHERE state='completed'").fetchone()[0]
        == 3
    )
    backend.state.close()
    restored = OpenAIResponsesDeveloperBackend(
        repository.root, directory, client=Client(response()), key=""
    )
    with pytest.raises(ValueError, match="changed request"):
        await restored.request("turn/retry", {"threadId": thread["id"]})
    assert not restored.jobs and not restored.client.requests
    await restored.request(
        "turn/start",
        {"threadId": thread["id"], "input": [{"type": "text", "text": "Report the limitation"}]},
    )
    await restored.jobs[thread["id"]]
    assert restored.state.get(thread["id"])["status"] == "completed"
    assert len(restored.state.get(thread["id"])["turns"]) == 2
    await restored.stop()


@pytest.mark.asyncio
async def test_original_multi_tool_turn_persists_new_source_without_index_workaround(
    repository, tmp_path
):
    path = "tools/developer_session_diagnostics.py"
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        tmp_path / "agent",
        key="",
        client=Client(
            response(
                call("create_worktree", {}, "create"),
                call(
                    "patch_file",
                    {"path": path, "expected_sha256": "", "content": "owned_marker = True\n"},
                    "patch",
                ),
                call("read_file", {"path": path, "start": 1}, "read"),
                call("search", {"text": "owned_marker"}, "search"),
            ),
            response(text="Created and inspected the new source."),
        ),
    )
    events = []

    async def sink(event):
        events.append(event)

    backend.sinks.add(sink)
    thread = await start(backend)
    assert thread["status"] == "completed"
    outputs = [json.loads(item["output"]) for item in backend.client.requests[-1]["input"]]
    assert "owned_marker" in outputs[2]["text"]
    assert outputs[3][0]["path"] == path
    assert not (repository.root / path).exists()
    root = Path(thread["worktree"])
    assert (await backend.runner.git(root, "ls-files", "--", path))["output"] == ""
    assert all(row[0] == "completed" for row in backend.state.db.execute("SELECT state FROM calls"))
    assert any(e["method"] == "item/completed" for e in events)
    await backend.stop()
    restored = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=Client(), key=""
    )
    saved = restored.public_thread(restored.state.get(thread["id"]))
    assert saved["status"] == "completed"
    assert {i["id"] for i in saved["turns"][-1]["items"] if i["type"] == "mcpToolCall"} == {
        "create",
        "patch",
        "read",
        "search",
    }
    await restored.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("swap", ["leaf", "parent"])
async def test_descriptor_read_rejects_symlink_swap(repository, monkeypatch, swap):
    thread = {"id": "api_owned"}
    await repository.execute(thread, "create_worktree", {})
    root = Path(thread["worktree"])
    (root / "nested").mkdir()
    (root / "nested/example.py").write_text("owned = True")
    original = repository.source

    def swap_after_validation(*args, **kwargs):
        result = original(*args, **kwargs)
        if swap == "leaf":
            result.unlink()
            result.symlink_to(repository.root / "example.py")
        else:
            (root / "nested").rename(root / "saved")
            (root / "nested").symlink_to(repository.root, target_is_directory=True)
        return result

    monkeypatch.setattr(repository, "source", swap_after_validation)
    with pytest.raises(ToolPolicyError):
        repository.read_source(root, "nested/example.py", 500000)


@pytest.mark.asyncio
async def test_genuine_read_io_failure_is_still_recoverable(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=Client(), key=""
    )
    thread = active(backend.state, repository.root)
    backend.runner.execute = AsyncMock(side_effect=OSError(errno.EIO, "private I/O detail"))
    with pytest.raises(ProviderUnavailable, match="Read-only tool interrupted"):
        await backend.tool(thread, call("read_file", {"path": "example.py", "start": 1}))
    assert backend.state.db.execute("SELECT state FROM calls").fetchone()[0] == "recoverable"
    assert "private I/O detail" not in json.dumps(
        backend.public_thread(backend.state.get(thread["id"]))
    )
    await backend.stop()
