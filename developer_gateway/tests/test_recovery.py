"""Deterministic regressions for the preserved Android self-repair incident."""

import asyncio
import hashlib
import json
import shutil
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI, APIStatusError

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend, ProviderUnavailable
from developer_gateway.policy import redact, redact_value
from developer_gateway.state import READ_ONLY_TOOLS, State
from developer_gateway.tools import READ, SPECS
from developer_gateway.tests.test_api_backend import Client, call, response, start
from developer_gateway.tests.test_tools_isolation import repository as repository


def active(state, root):
    thread = state.create(str(root))
    thread.update(turn_id="turn", status="running", turns=[{"id": "turn", "items": []}])
    state.save(thread)
    return thread


def test_only_declared_read_effects_are_recoverable():
    assert READ_ONLY_TOOLS == {name for name, spec in SPECS.items() if spec[0] == READ}
    assert "verify" not in READ_ONLY_TOOLS


@pytest.mark.asyncio
async def test_legacy_completed_tool_history_reconstructed_without_outputs(tmp_path):
    backend = OpenAIResponsesDeveloperBackend(tmp_path, tmp_path / "agent", client=Client(), key="")
    thread = active(backend.state, tmp_path)
    thread["status"] = "failed"
    backend.state.save(thread)
    row = backend.state.claim(thread["id"], "turn", "done", "read_file", {"path": "x", "start": 1})
    backend.state.update_call(row["id"], state="completed", result='{"text":"private source"}')
    history = backend.public_thread(thread)
    assert history["status"] == "failed"
    assert history["turns"][-1]["items"] == [
        {"id": "done", "type": "mcpToolCall", "tool": "read_file", "status": "completed"}
    ]
    assert "private source" not in json.dumps(history)
    assert thread["turns"][-1]["items"] == []
    await backend.stop()


@pytest.mark.asyncio
async def test_gateway_stop_during_read_allows_retry_and_releases_journal(tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        tmp_path, tmp_path / "agent", client=Client(response(call("pr_status", {}))), key=""
    )
    started = asyncio.Event()

    async def waiting(*args):
        started.set()
        await asyncio.Event().wait()

    backend.runner.execute = waiting
    thread = backend.state.create(str(tmp_path))
    await backend.request(
        "turn/start", {"threadId": thread["id"], "input": [{"type": "text", "text": "Inspect"}]}
    )
    await started.wait()
    await backend.stop()
    restored = OpenAIResponsesDeveloperBackend(
        tmp_path, tmp_path / "agent", client=Client(response()), key=""
    )
    paused = restored.state.get(thread["id"])
    assert paused["status"] == paused["turns"][-1]["status"] == "paused"
    assert restored.state.db.execute("select state from calls").fetchone()[0] == "recoverable"
    restored.runner.execute = AsyncMock(return_value={"ok": True})
    await restored.request("turn/retry", {"threadId": thread["id"]})
    await restored.jobs[thread["id"]]
    assert restored.state.get(thread["id"])["status"] == "completed"
    restored.runner.execute.assert_awaited_once()
    await restored.stop()


@pytest.mark.parametrize(
    "tool",
    [
        "pr_status",
        "repository",
        "read_file",
        "search",
        "verify",
        "patch_file",
        "git_push",
        "unknown",
    ],
)
@pytest.mark.parametrize("previous", ["executing", "uncertain", "completed"])
def test_restart_reconciles_only_reads_preserves_effects(tmp_path, tool, previous):
    path = tmp_path / "state.sqlite3"
    state = State(path)
    thread = active(state, tmp_path)
    row = state.claim(thread["id"], "turn", "call", tool, {})
    state.update_call(row["id"], state=previous, result='{"ok": true}')
    state.close()
    restored = State(path)
    expected = (
        "completed"
        if previous == "completed"
        else ("recoverable" if tool in READ_ONLY_TOOLS else "uncertain")
    )
    assert restored.call(row["id"])["state"] == expected
    assert restored.get(thread["id"])["status"] == "paused"
    assert restored.get(thread["id"])["turns"][-1]["status"] == "paused"
    restored.close()


@pytest.mark.asyncio
async def test_pr_status_without_task_branch_is_a_result_not_uncertainty(repository, tmp_path):
    backend = OpenAIResponsesDeveloperBackend(
        repository.root,
        tmp_path / "agent",
        client=Client(response(call("pr_status", {})), response()),
        key="",
    )
    backend.runner = repository
    thread = await start(backend)
    assert thread["status"] == "completed"
    result = json.loads(backend.client.requests[-1]["input"][0]["output"])
    assert not result["ok"] and "no branch" in result["error"]
    assert backend.state.db.execute("select state from calls").fetchone()[0] == "completed"
    await backend.stop()


@pytest.mark.asyncio
async def test_multi_call_restart_recovers_read_without_replaying_completed_patch(tmp_path):
    patch = call(
        "patch_file", {"path": "example.py", "expected_sha256": "", "content": "x"}, "write"
    )
    read = call("pr_status", {}, "read")
    backend = OpenAIResponsesDeveloperBackend(
        tmp_path, tmp_path / "agent", client=Client(response(patch, read)), key=""
    )
    writes, reads = [], []

    async def execute(thread, name, args):
        if name == "patch_file":
            writes.append(1)
            return {"ok": True}
        reads.append(1)
        if len(reads) == 1:
            raise TimeoutError("private diagnostic")
        return {"ok": True, "output": "checks pending"}

    backend.runner.execute = execute
    thread = await start(backend)
    assert thread["status"] == "failed"
    assert "read-only" in thread["error"].lower()
    assert thread["diagnostic"] == {"phase": "tools", "category": "provider_unavailable"}
    await backend.stop()
    restored = OpenAIResponsesDeveloperBackend(
        tmp_path, tmp_path / "agent", client=Client(response()), key=""
    )
    restored.runner.execute = execute
    await restored.request("turn/retry", {"threadId": thread["id"]})
    await restored.jobs[thread["id"]]
    result = restored.state.get(thread["id"])
    assert result["status"] == "completed" and len(writes) == 1 and len(reads) == 2
    outputs = restored.client.requests[0]["input"]
    assert [item["call_id"] for item in outputs] == ["write", "read"]
    activity = [i for i in result["turns"][-1]["items"] if i["type"] == "mcpToolCall"]
    assert [i["id"] for i in activity] == ["write", "read"]
    assert all(i["status"] == "completed" for i in activity)
    assert result["accounted_calls"] == ["write", "read"]
    assert len(result["seen_calls"]) == 2
    await restored.stop()
    await restored.stop()  # Idempotent close, journal lock released.
    journal = State(tmp_path / "agent/tasks.sqlite3")
    assert journal.get(thread["id"])["status"] == "completed"
    journal.close()


@pytest.mark.parametrize(
    "tool,args",
    [
        ("patch_file", {"path": "x", "expected_sha256": "", "content": "x"}),
        ("verify", {"profile": "format", "path": "x"}),
        ("git_push", {}),
    ],
)
@pytest.mark.asyncio
async def test_uncertain_effect_and_forged_recoverable_effect_never_replayed(tmp_path, tool, args):
    backend = OpenAIResponsesDeveloperBackend(tmp_path, tmp_path / "agent", client=Client(), key="")
    thread = active(backend.state, tmp_path)
    row = backend.state.claim(thread["id"], "turn", "effect", tool, args)
    backend.runner.execute = AsyncMock()
    for status in ("uncertain", "recoverable", "executing"):
        backend.state.update_call(row["id"], state=status, approval="accepted")
        with pytest.raises(ProviderUnavailable, match="reconciliation"):
            await backend.tool(thread, call(tool, args, "effect"))
    backend.runner.execute.assert_not_awaited()
    await backend.stop()


@pytest.mark.asyncio
async def test_read_identity_conflict_still_denied(tmp_path):
    backend = OpenAIResponsesDeveloperBackend(tmp_path, tmp_path / "agent", client=Client(), key="")
    thread = active(backend.state, tmp_path)
    row = backend.state.claim(thread["id"], "turn", "read", "repository", {"view": "status"})
    backend.state.update_call(row["id"], state="recoverable")
    backend.runner.execute = AsyncMock()
    with pytest.raises(ValueError, match="identity conflict"):
        await backend.tool(thread, call("repository", {"view": "diff"}, "read"))
    backend.runner.execute.assert_not_awaited()
    await backend.stop()


class Events(httpx.AsyncByteStream):
    def __init__(self, *events):
        self.events = events

    async def __aiter__(self):
        for event in self.events:
            if isinstance(event, Exception):
                raise event
            yield ("data: " + json.dumps(event) + "\n\n").encode()


@pytest.mark.parametrize(
    "interrupt", [httpx.ReadError("private"), httpx.RemoteProtocolError("private")]
)
@pytest.mark.asyncio
async def test_real_sdk_transport_failure_then_success_exact_once(tmp_path, interrupt):
    write = call("patch_file", {"path": "x", "expected_sha256": "", "content": "x"})
    streams = [
        Events({"type": "response.completed", "response": response(write)}),
        Events({"type": "response.function_call_arguments.delta", "delta": "partial"}, interrupt),
        Events({"type": "response.completed", "response": response(text="Verified final")}),
    ]
    requests = []

    async def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=streams.pop(0)
        )

    client = AsyncOpenAI(
        api_key="test-fixture",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    backend = OpenAIResponsesDeveloperBackend(
        tmp_path, tmp_path / "agent", client=client, key="test-fixture"
    )
    backend.retry_delays = (0,)
    backend.runner.execute = AsyncMock(return_value={"ok": True})
    result = await start(backend)
    assert result["status"] == "completed"
    backend.runner.execute.assert_awaited_once()
    assert len(requests) == 3 and requests[1]["input"] == requests[2]["input"]
    assert result["turns"][-1]["items"][-1]["text"] == "Verified final"
    await backend.stop()


@pytest.mark.asyncio
async def test_real_sdk_sse_error_has_safe_resumable_failure(tmp_path):
    async def transport(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Events(
                {"type": "error", "error": {"message": "private payload", "code": "server_error"}}
            ),
        )

    client = AsyncOpenAI(
        api_key="test-fixture",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    backend = OpenAIResponsesDeveloperBackend(
        tmp_path, tmp_path / "agent", client=client, key="test-fixture"
    )
    result = await start(backend)
    assert result["status"] == "failed" and "API stream failed" in result["error"]
    assert "private payload" not in json.dumps(backend.public_thread(result))
    assert result["diagnostic"]["phase"] == "provider"
    await backend.stop()


@pytest.mark.parametrize("billing", [True, False])
@pytest.mark.asyncio
async def test_sdk_error_after_patch_preserves_work_and_resumes(tmp_path, billing):
    write = call("patch_file", {"path": "x", "expected_sha256": "", "content": "x"})
    failure = (
        {"message": "You have no credits remaining. private-fixture"}
        if billing
        else {"code": "server_error", "message": "private-fixture"}
    )
    streams = [
        Events({"type": "response.completed", "response": response(write)}),
        Events({"type": "error", "error": failure}),
        Events({"type": "response.completed", "response": response(text="Continued after tools")}),
    ]
    requests = []

    async def transport(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=streams.pop(0)
        )

    client = AsyncOpenAI(
        api_key="test-fixture",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    backend = OpenAIResponsesDeveloperBackend(
        tmp_path, tmp_path / "agent", client=client, key="test-fixture"
    )
    backend.retry_delays = (0, 0, 0)
    backend.runner.execute = AsyncMock(return_value={"ok": True})
    result = await start(backend)
    if billing:
        assert result["status"] == "failed" and "credits/quota" in result["error"]
        assert result["provider_failure_code"] == "insufficient_quota"
        assert len(requests) == 2  # No automatic retry of exhausted credits.
        assert "private-fixture" not in json.dumps(backend.public_thread(result))
        await backend.request("turn/retry", {"threadId": result["id"]})
        await backend.jobs[result["id"]]
    assert backend.state.get(result["id"])["status"] == "completed"
    assert len(requests) == 3 and requests[1]["input"] == requests[2]["input"]
    backend.runner.execute.assert_awaited_once()
    await backend.stop()


@pytest.mark.asyncio
async def test_http_quota_not_misreported_as_transient_rate_limit(tmp_path):
    error = APIStatusError(
        "private",
        response=httpx.Response(429, request=httpx.Request("POST", "https://example.test")),
        body={"code": "insufficient_quota"},
    )
    client = Client(error)
    backend = OpenAIResponsesDeveloperBackend(tmp_path, tmp_path / "agent", client=client, key="")
    result = await start(backend)
    assert len(client.requests) == 1 and "credits/quota" in result["error"]
    assert result["status"] == "failed"
    await backend.stop()


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"input_tokens": None, "output_tokens": "unknown"},
        {"input_tokens": -1, "output_tokens": True},
    ],
)
@pytest.mark.asyncio
async def test_usage_metadata_cannot_destroy_final_response(tmp_path, usage):
    final = response()
    final["usage"] = usage
    backend = OpenAIResponsesDeveloperBackend(
        tmp_path, tmp_path / "agent", client=Client(final), key=""
    )
    result = await start(backend)
    assert result["status"] == "completed" and result["usage_incomplete"]
    assert result["turns"][-1]["items"][-1]["text"] == "Grounded result"
    await backend.stop()


def test_source_references_survive_but_quoted_and_known_secrets_do_not():
    source = "client = AsyncOpenAI(api_key=self.key, max_retries=0)\n"
    assert redact(source) == source
    assert redact_value({"text": source}) == {"text": source}
    for text in (
        'api_key="self.private_value"',
        "# api_key=private_value",
        "api_key=self.key,token=private_value",
    ):
        assert "private_value" not in redact(text)
    assert "self.key" not in redact(source, ("self.key",))
    value = {"text": "token=private_value", "count": 1, "nested": ["private_value"]}
    clean = redact_value(value, ("private_value",))
    assert json.loads(json.dumps(clean)) == clean and clean["count"] == 1


@pytest.mark.asyncio
async def test_masked_replacement_refused_before_write(repository):
    thread = {"id": "api_" + "a" * 32}
    await repository.execute(thread, "create_worktree", {})
    original = await repository.execute(thread, "read_file", {"path": "example.py", "start": 1})
    with pytest.raises(ValueError, match="redaction placeholders"):
        await repository.execute(
            thread,
            "patch_file",
            {"path": "example.py", "expected_sha256": original["sha256"], "content": "[REDACTED]"},
        )
    assert (repository.root / "example.py").read_text() == "value = 1\n"


@pytest.mark.skipif(not shutil.which("bwrap"), reason="Restricted sandbox unavailable")
@pytest.mark.asyncio
async def test_fresh_multi_tool_task_real_worktree_patch_and_sandbox(repository, tmp_path):
    client = Client(
        response(
            call("create_worktree", {}, "worktree"),
            call("read_file", {"path": "example.py", "start": 1}, "read"),
            call(
                "patch_file",
                {
                    "path": "example.py",
                    "expected_sha256": hashlib.sha256(b"value = 1\n").hexdigest(),
                    "content": "value = 2\n",
                },
                "patch",
            ),
            call("verify", {"profile": "compile", "path": "example.py"}, "test"),
            call("repository", {"view": "diff"}, "diff"),
        ),
        response(text="Changed and verified"),
    )
    backend = OpenAIResponsesDeveloperBackend(
        repository.root, tmp_path / "agent", client=client, key=""
    )
    backend.runner = repository
    result = await start(backend)
    assert result["status"] == "completed"
    assert all(json.loads(item["output"]).get("ok", True) for item in client.requests[1]["input"])
    assert len(client.requests[1]["input"]) == 5
    assert (repository.root / "example.py").read_text() == "value = 1\n"
    assert (
        backend.state.db.execute("select count(*) from calls where state='completed'").fetchone()[0]
        == 5
    )
    await backend.stop()
