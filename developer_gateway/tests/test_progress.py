"""Progress-aware orchestration with disposable Git, journal and provider fixtures."""

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend
from developer_gateway.tests.test_api_backend import Client, call, response, start
from developer_gateway.tests.test_tools_isolation import repository as repository
from developer_gateway.tests.test_recovery import active
from developer_gateway.api_backend import ProviderUnavailable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("read_file", {"path": "example.py", "start": 1}),
        ("search", {"text": "value"}),
    ],
)
async def test_four_revisits_with_intervening_new_evidence_are_not_a_loop(
    repository, tmp_path, tool, args
):
    responses = []
    for n in range(4):
        responses.append(response(call(tool, args, f"repeat{n}")))
        (repository.root / f"evidence{n}.py").write_text(f"evidence = {n}\n")
        await repository.git(repository.root, "add", f"evidence{n}.py")
        responses.append(
            response(call("read_file", {"path": f"evidence{n}.py", "start": 1}, f"new{n}"))
        )
    backend = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=Client(*responses, response()), key=""
    )
    result = await start(backend)
    assert result["status"] == "completed"
    assert (
        backend.state.db.execute("SELECT count(*) FROM calls WHERE state='completed'").fetchone()[0]
        == 8
    )
    await backend.stop()


@pytest.mark.asyncio
async def test_four_reads_after_real_patches_are_progress(repository, tmp_path):
    responses = [response(call("create_worktree", {}, "create"))]
    old = "value = 1\n"
    for n in range(4):
        new = f"value = {n + 2}\n"
        responses.append(
            response(
                call(
                    "patch_file",
                    {
                        "path": "example.py",
                        "expected_sha256": hashlib.sha256(old.encode()).hexdigest(),
                        "content": new,
                    },
                    f"patch{n}",
                )
            )
        )
        responses.append(
            response(call("read_file", {"path": "example.py", "start": 1}, f"read{n}"))
        )
        old = new
    backend = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=Client(*responses, response()), key=""
    )
    result = await start(backend)
    assert result["status"] == "completed"
    assert (Path(result["worktree"]) / "example.py").read_text() == old
    await backend.stop()


@pytest.mark.asyncio
async def test_unchanged_read_loop_is_not_a_provider_outage(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        tmp_path / "agent",
        client=Client(
            *[
                response(call("read_file", {"path": "example.py", "start": 1}, f"read{n}"))
                for n in range(4)
            ]
        ),
        key="",
    )
    result = await start(backend)
    assert result["status"] == "failed"
    assert result["diagnostic"]["category"] == "tool_loop"
    assert all(row[0] == "completed" for row in backend.state.db.execute("SELECT state FROM calls"))
    assert result["failures"] == 0
    await backend.stop()


@pytest.mark.asyncio
async def test_read_cache_revalidates_content_and_permissions_after_restart(repository, tmp_path):
    directory = tmp_path / "agent"
    backend = OpenAIResponsesDeveloperBackend(repository.root, directory, client=Client(), key="")
    thread = active(backend.state, repository.root)
    args = {"path": "example.py", "start": 1}
    first = await backend.tool(thread, call("read_file", args, "one"))
    backend.state.close()
    restored = OpenAIResponsesDeveloperBackend(repository.root, directory, client=Client(), key="")
    thread = restored.state.get(thread["id"])
    thread["status"] = "running"
    restored.state.save(thread)
    execute = restored.runner.execute
    restored.runner.execute = AsyncMock(wraps=execute)
    assert await restored.tool(thread, call("read_file", args, "two")) == first
    restored.runner.execute.assert_not_awaited()
    assert restored.state.activity(thread["id"], "turn")[-1]["status"] == "completed"
    # Same size/mtime is insufficient: actual bytes must invalidate a cache hit.
    source = repository.root / "example.py"
    source.write_text("value = 2\n")
    changed = await restored.tool(thread, call("read_file", args, "three"))
    assert changed["sha256"] != first["sha256"] and "2" in changed["text"]
    restored.runner.execute.assert_awaited_once()
    source.unlink()
    source.symlink_to(tmp_path / "not-authorized")
    denied = await restored.tool(thread, call("read_file", args, "four"))
    assert denied["code"] == "file_policy_rejected"
    assert "text" not in denied
    await restored.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_id", [False, True])
async def test_unchanged_search_and_reused_call_ids_remain_bounded(repository, tmp_path, same_id):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        tmp_path / "agent",
        client=Client(
            *[
                response(call("search", {"text": "value"}, "same" if same_id else f"read{n}"))
                for n in range(8)
            ]
        ),
        key="",
    )
    result = await start(backend)
    assert result["status"] == "failed" and result["loop_blocked"]
    assert result["diagnostic"]["category"] == "tool_loop"
    assert len(backend.client.requests) == 4
    assert backend.state.db.execute("SELECT count(*) FROM calls").fetchone()[0] == (
        1 if same_id else 4
    )
    await backend.stop()


@pytest.mark.asyncio
async def test_restart_retains_stagnation_and_rejects_loop_retry(repository, tmp_path):
    directory = tmp_path / "agent"
    read = {"path": "example.py", "start": 1}
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        directory,
        client=Client(
            *[response(call("read_file", read, f"read{i}")) for i in range(3)],
            TimeoutError("fixture transport interruption"),
        ),
        key="",
    )
    result = await start(backend)
    assert result["progress"]["stalled"] == 2
    await backend.stop()
    restored = OpenAIResponsesDeveloperBackend(
        repository.root, directory, client=Client(response(call("read_file", read, "four"))), key=""
    )
    await restored.request("turn/retry", {"threadId": result["id"]})
    await restored.jobs[result["id"]]
    terminal = restored.state.get(result["id"])
    assert terminal["diagnostic"]["category"] == "tool_loop"
    assert terminal["progress"]["stalled"] == 3
    await restored.stop()
    again = OpenAIResponsesDeveloperBackend(
        repository.root, directory, client=Client(response()), key=""
    )
    with pytest.raises(ValueError, match="changed objective"):
        await again.request("turn/retry", {"threadId": result["id"]})
    assert not again.client.requests
    await again.request(
        "turn/start",
        {"threadId": result["id"], "input": [{"type": "text", "text": "Report the findings"}]},
    )
    await again.jobs[result["id"]]
    assert again.state.get(result["id"])["status"] == "completed"
    assert len(again.state.get(result["id"])["turns"]) == 2
    await again.stop()


@pytest.mark.asyncio
async def test_pending_response_replay_does_not_double_count_progress(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=Client(), key=""
    )
    thread = active(backend.state, repository.root)
    c = call("read_file", {"path": "example.py", "start": 1})
    result = await backend.tool(thread, c)
    backend.observe_progress(thread, c, result)
    backend.observe_progress(thread, c, result)
    assert thread["progress"]["stalled"] == 0 and len(thread["progress"]["steps"]) == 1
    assert backend.state.db.execute("SELECT count(*) FROM calls").fetchone()[0] == 1
    await backend.stop()


@pytest.mark.asyncio
async def test_continuation_and_compaction_keep_bounded_progress_metadata(repository, tmp_path):
    first = response(call("read_file", {"path": "example.py", "start": 1}, "one"))
    first["id"] = "response_first"
    first["output"].insert(0, {"type": "compaction", "encrypted_content": "opaque-fixture"})
    backend = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=Client(first, response()), key=""
    )
    result = await start(backend)
    assert result["status"] == "completed"
    request = backend.client.requests[1]
    assert request["previous_response_id"] == "response_first"
    assert json.loads(request["input"][0]["output"])["text"] == "value = 1"
    assert '"completed_operations"' in request["instructions"]
    assert '"new_evidence": true' in request["instructions"]
    assert "value = 1" not in request["instructions"]
    assert request["context_management"] == [{"type": "compaction", "compact_threshold": 32000}]
    await backend.stop()


@pytest.mark.asyncio
async def test_read_cache_cannot_replay_or_approve_uncertain_effect(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=Client(), key=""
    )
    thread = active(backend.state, repository.root)
    args = {"path": "example.py", "expected_sha256": "", "content": "changed"}
    effect = call("patch_file", args, "effect")
    row = backend.state.claim(thread["id"], "turn", "effect", "patch_file", args)
    backend.state.update_call(row["id"], state="uncertain")
    backend.runner.execute = AsyncMock()
    with pytest.raises(ProviderUnavailable, match="uncertain"):
        await backend.tool(thread, effect)
    backend.runner.execute.assert_not_awaited()
    await backend.stop()


@pytest.mark.asyncio
async def test_search_observes_new_untracked_source_in_owned_worktree(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=Client(), key=""
    )
    thread = active(backend.state, repository.root)
    await backend.tool(thread, call("create_worktree", {}, "create"))
    args = {"text": "new_evidence"}
    before = await backend.tool(thread, call("search", args, "before"))
    await backend.tool(
        thread,
        call(
            "patch_file",
            {"path": "new.py", "expected_sha256": "", "content": "new_evidence = 1\n"},
            "patch",
        ),
    )
    after = await backend.tool(thread, call("search", args, "after"))
    assert before != after and "new.py" in json.dumps(after)
    backend.observe_progress(thread, call("search", args, "before"), before)
    backend.observe_progress(thread, call("search", args, "after"), after)
    assert thread["progress"]["stalled"] == 0
    await backend.stop()


@pytest.mark.asyncio
async def test_alternating_known_observations_do_not_evade_loop_guard(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        tmp_path / "agent",
        client=Client(
            *[
                response(call("read_file", {"path": "example.py", "start": 1 + i % 2}, f"r{i}"))
                for i in range(8)
            ]
        ),
        key="",
    )
    result = await start(backend)
    assert result["diagnostic"]["category"] == "tool_loop"
    assert len(backend.client.requests) == 7
    await backend.stop()


@pytest.mark.asyncio
async def test_call_identity_and_progress_are_scoped_to_turn(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        tmp_path / "agent",
        client=Client(response(call()), response(), response(call()), response()),
        key="",
    )
    result = await start(backend)
    await backend.request(
        "turn/start",
        {"threadId": result["id"], "input": [{"type": "text", "text": "Inspect again"}]},
    )
    await backend.jobs[result["id"]]
    result = backend.state.get(result["id"])
    assert result["status"] == "completed"
    assert all(turn["status"] == "completed" for turn in result["turns"])
    assert backend.state.db.execute("SELECT count(*) FROM calls").fetchone()[0] == 2
    assert result["progress"]["stalled"] == 0
    await backend.stop()


@pytest.mark.asyncio
async def test_finite_review_of_multiple_known_files_is_not_a_loop(repository, tmp_path):
    for i in range(3):
        (repository.root / f"review{i}.py").write_text(f"evidence = {i}\n")
        await repository.git(repository.root, "add", f"review{i}.py")
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        tmp_path / "agent",
        client=Client(
            *[
                response(call("read_file", {"path": f"review{i % 3}.py", "start": 1}, f"r{i}"))
                for i in range(6)
            ],
            response(),
        ),
        key="",
    )
    result = await start(backend)
    assert result["status"] == "completed"
    assert result["progress"]["stalled"] == 3
    await backend.stop()
