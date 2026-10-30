import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend, ProviderUnavailable
from developer_gateway.backends import DeveloperBackends
from developer_gateway.policy import credential, redact
from developer_gateway.state import State
from developer_gateway.tools import ToolRunner, validate


def response(*calls, text="Grounded result"):
    output = list(calls) or [
        {
            "type": "message",
            "content": [
                {"type": "output_text", "text": text},
            ],
        }
    ]
    return {
        "id": "resp_test",
        "status": "completed",
        "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


def call(name="repository", args=None, identifier="call_one"):
    return {
        "type": "function_call",
        "name": name,
        "call_id": identifier,
        "arguments": json.dumps({"view": "status"} if args is None else args),
    }


class Stream:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        async def events():
            yield SimpleNamespace(type="response.output_text.delta", delta="Working\n")
            yield SimpleNamespace(
                type="response.completed", response=SimpleNamespace(model_dump=lambda: self.value)
            )

        return events()


class Client:
    def __init__(self, *values):
        self.values = list(values)
        self.requests = []
        self.responses = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return Stream(value)

    async def close(self):
        pass


@pytest.fixture
def backend(tmp_path, backend_factory):
    return backend_factory(
        tmp_path, tmp_path / "state", client=Client(response()), key="private-fixture-key"
    )


async def start(backend, text="Inspect only"):
    result = await backend.request("thread/start", {"cwd": str(backend.runner.root)})
    thread = result["result"]["thread"]
    await backend.request(
        "turn/start", {"threadId": thread["id"], "input": [{"type": "text", "text": text}]}
    )
    await backend.jobs[thread["id"]]
    return backend.state.get(thread["id"])


def status_error(code):
    return APIStatusError(
        "private-fixture-key private body",
        response=httpx.Response(code, request=httpx.Request("POST", "https://example.test")),
        body=None,
    )


@pytest.mark.asyncio
async def test_thread_stream_final_and_stateful_continuation(backend):
    events = []

    async def sink(event):
        events.append(event)

    backend.sinks.add(sink)
    thread = await start(backend)
    assert thread["status"] == "completed"
    assert any(e["method"] == "item/agentMessage/delta" for e in events)
    assert thread["turns"][-1]["items"][-1]["text"] == "Grounded result"
    backend.client.values.append(response(text="Next"))
    await backend.request(
        "turn/start", {"threadId": thread["id"], "input": [{"type": "text", "text": "Continue"}]}
    )
    await backend.jobs[thread["id"]]
    assert backend.client.requests[-1]["previous_response_id"] == "resp_test"
    assert backend.client.requests[-1]["instructions"]


@pytest.mark.asyncio
async def test_missing_credential_fails_closed(tmp_path, backend_factory):
    backend = backend_factory(tmp_path, tmp_path / "state", key="")
    with pytest.raises(ProviderUnavailable, match="authentication"):
        await backend.request("thread/start", {"cwd": str(tmp_path)})
    assert not backend.status()["api_backend_configured"]


@pytest.mark.parametrize("code", [429, 500, 503])
@pytest.mark.asyncio
async def test_transient_status_retry_then_success(backend, code):
    backend.retry_delays = (0, 0, 0)
    backend.client = Client(status_error(code), response())
    thread = await start(backend)
    assert thread["status"] == "completed"
    assert len(backend.client.requests) == 2


@pytest.mark.asyncio
async def test_network_retry(backend):
    backend.retry_delays = (0,)
    backend.client = Client(
        APIConnectionError(request=httpx.Request("POST", "https://example.test")), response()
    )
    assert (await start(backend))["status"] == "completed"


@pytest.mark.asyncio
async def test_exhaustion_preserves_task_and_no_false_final(backend):
    backend.retry_delays = (0,)
    backend.client = Client(status_error(429), status_error(429))
    thread = await start(backend)
    assert thread["status"] == "failed"
    assert not any(i["type"] == "agentMessage" for i in thread["turns"][-1]["items"])
    assert thread["turns"][-1]["items"][-1]["status"] == "failed"
    assert "private-fixture-key" not in thread["error"]
    backend.client.values.append(response())
    await backend.request("turn/retry", {"threadId": thread["id"]})
    await backend.jobs[thread["id"]]
    assert backend.state.get(thread["id"])["status"] == "completed"


@pytest.mark.asyncio
async def test_tool_result_continuation_and_exact_once(backend):
    invocations = []

    async def execute(thread, name, args):
        invocations.append(name)
        return {"ok": True, "output": "evidence"}

    backend.runner.execute = execute
    backend.client = Client(response(call()), response())
    thread = await start(backend)
    assert invocations == ["repository"]
    assert backend.client.requests[1]["input"][0]["type"] == "function_call_output"
    assert await backend.tool(thread, call()) == {"ok": True, "output": "evidence"}
    assert len(invocations) == 1
    with pytest.raises(ValueError, match="identity conflict"):
        await backend.tool(thread, call(args={"view": "files"}))


@pytest.mark.asyncio
async def test_retry_after_write_does_not_repeat_effect(backend):
    invocations = []

    async def execute(*args):
        invocations.append(1)
        return {"ok": True}

    backend.runner.execute = execute
    backend.retry_delays = ()
    write = call("patch_file", {"path": "file.py", "expected_sha256": "", "content": "x"})
    backend.client = Client(response(write), status_error(429))
    thread = await start(backend)
    assert thread["status"] == "failed"
    backend.client.values.append(response())
    await backend.request("turn/retry", {"threadId": thread["id"]})
    await backend.jobs[thread["id"]]
    assert len(invocations) == 1


@pytest.mark.asyncio
async def test_disconnect_bad_sink_does_not_cancel_job(backend):
    async def disconnected(event):
        raise ConnectionError("closed")

    backend.sinks.add(disconnected)
    thread = await start(backend)
    assert thread["status"] == "completed"
    restored = (await backend.request("thread/resume", {"threadId": thread["id"]}))["result"][
        "thread"
    ]
    assert restored["turns"] == thread["turns"]
    assert "pending_response" not in restored and "input" not in restored


def test_restart_marks_uncertain_and_preserves_completed(tmp_path):
    path = tmp_path / "state.sqlite3"
    state = State(path)
    thread = state.create(str(tmp_path))
    thread["status"] = "running"
    state.save(thread)
    first = state.claim(thread["id"], "turn", "one", "patch_file", {})
    second = state.claim(thread["id"], "turn", "two", "patch_file", {})
    state.update_call(first["id"], state="completed", result='{"ok":true}')
    state.update_call(second["id"], state="executing")
    state.close()
    restored = State(path)
    try:
        assert restored.get(thread["id"])["status"] == "paused"
        assert restored.call(first["id"])["state"] == "completed"
        assert restored.call(second["id"])["state"] == "uncertain"
    finally:
        restored.close()


@pytest.mark.parametrize("decision", ["accept", "decline", "expire"])
@pytest.mark.asyncio
async def test_approval_exact_operation_required_denied_expired(backend, decision):
    thread = backend.state.create(str(backend.runner.root))
    thread.update(turn_id="turn", status="running")
    backend.state.save(thread)
    invocations = []

    async def execute(*args):
        invocations.append(1)
        return {"ok": True}

    async def approve(event):
        if "id" in event and decision != "expire":
            await backend.respond(-event["id"], {"decision": decision}, {thread["id"]})

    backend.runner.execute = execute
    backend.sinks.add(approve)
    backend.approval_seconds = 1 if decision != "expire" else 0.01
    result = await backend.tool(thread, call("git_push", {}))
    assert result["ok"] == (decision == "accept")
    assert len(invocations) == (1 if decision == "accept" else 0)


@pytest.mark.parametrize("path", ["../escape", "/etc/passwd", ".git/config", ".env", "data/secret"])
def test_unsafe_source_paths_rejected(backend, path):
    with pytest.raises(ValueError):
        backend.runner.source(backend.runner.root, path)


def test_symlink_and_hardlink_rejected(backend, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("private")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "link").symlink_to(outside)
    (root / "hardlink").hardlink_to(outside)
    for name in ("link", "hardlink"):
        with pytest.raises(ValueError):
            backend.runner.source(root, name)


@pytest.mark.parametrize(
    "name,args",
    [
        ("shell", {"command": "echo nope"}),
        ("repository", {"view": "files", "extra": True}),
        ("repository", {"view": "reset"}),
        ("read_file", {"path": "x", "start": True}),
    ],
)
def test_unsupported_tools_and_schema_fail_closed(name, args):
    with pytest.raises(ValueError):
        validate(name, args)


def test_production_write_rejected(backend):
    thread = backend.state.create(str(backend.runner.root))
    with pytest.raises(ValueError):
        backend.runner.workspace(thread, True)
    thread["worktree"] = str(backend.runner.root)
    with pytest.raises(ValueError):
        backend.runner.workspace(thread, True)


def test_secret_redaction_and_credential_permissions(tmp_path, monkeypatch):
    path = tmp_path / "key"
    path.write_text("private-fixture-key")
    path.chmod(0o600)
    monkeypatch.setenv("JARVIS_DEVELOPER_API_KEY_FILE", str(path))
    assert credential() == "private-fixture-key"
    assert "private-fixture-key" not in redact("private-fixture-key", (credential(),))
    path.chmod(0o644)
    assert credential() == ""
    assert "sk-" not in redact("sk-" + "a" * 40)


@pytest.mark.asyncio
async def test_no_key_in_health_events_errors_or_history(backend, caplog):
    events = []

    async def sink(event):
        events.append(event)

    backend.sinks.add(sink)
    backend.client = Client(response(text="private-fixture-key"))
    thread = await start(backend)
    assert "private-fixture-key" not in json.dumps(
        [
            backend.status(),
            events,
            backend.public_thread(thread),
            caplog.text,
        ]
    )


@pytest.mark.asyncio
async def test_empty_final_is_failure(backend):
    backend.client = Client(response(text=""))
    assert (await start(backend))["status"] == "failed"


@pytest.mark.asyncio
async def test_cancellation(backend):
    started = asyncio.Event()

    async def provider(thread):
        started.set()
        await asyncio.Event().wait()

    backend.provider = provider
    thread = backend.state.create(str(backend.runner.root))
    await backend.request(
        "turn/start",
        {
            "threadId": thread["id"],
            "input": [
                {"type": "text", "text": "inspect"},
            ],
        },
    )
    await started.wait()
    current = backend.state.get(thread["id"])
    await backend.request(
        "turn/interrupt", {"threadId": thread["id"], "turnId": current["turn_id"]}
    )
    assert backend.state.get(thread["id"])["status"] == "cancelled"


@pytest.mark.asyncio
async def test_loop_guard(backend):
    async def execute(*args):
        return {"ok": True}

    backend.runner.execute = execute
    backend.client = Client(*(response(call(identifier=f"call{i}")) for i in range(4)))
    thread = await start(backend)
    assert thread["status"] == "failed"
    assert "guard" in thread["error"]


@pytest.mark.asyncio
async def test_thread_isolation_and_delete(backend):
    first = await start(backend)
    second = backend.state.create(str(backend.runner.root))
    assert first["id"] != second["id"]
    with pytest.raises(ValueError):
        backend.thread(first["id"], "/another")
    with pytest.raises(ValueError):
        backend.thread("codex_thread")
    await backend.request("thread/delete", {"threadId": first["id"]})
    with pytest.raises(ValueError):
        backend.thread(first["id"])
    assert backend.thread(second["id"])["turns"] == []
    await backend.stop()  # Deleted completed jobs must not break service shutdown.


def test_journal_excludes_second_owner_and_expires_pending_approval(tmp_path):
    state = State(tmp_path / "agent.sqlite3")
    with pytest.raises(RuntimeError, match="already owned"):
        State(tmp_path / "agent.sqlite3")
    thread = state.create(str(tmp_path))
    row = state.claim(thread["id"], "turn", "approval", "git_push", {})
    state.update_call(row["id"], approval="pending", expires=time.time() - 1)
    assert state.pending(thread["id"]) == []
    assert state.call(row["id"])["approval"] == "expired"
    state.close()


@pytest.mark.asyncio
async def test_cancelled_approval_cannot_be_granted(backend):
    thread = backend.state.create(str(backend.runner.root))
    thread.update(turn_id="turn", status="waiting_approval")
    backend.state.save(thread)
    row = backend.state.claim(thread["id"], "turn", "approval", "git_push", {})
    backend.state.update_call(row["id"], approval="pending", expires=time.time() + 60)
    await backend.request("turn/interrupt", {"threadId": thread["id"], "turnId": "turn"})
    with pytest.raises(ValueError):
        await backend.respond(row["id"], {"decision": "accept"}, {thread["id"]})
    assert backend.state.pending(thread["id"]) == []


@pytest.mark.asyncio
async def test_codex_adapter_keeps_read_only_canonical_and_hides_reasoning(tmp_path, monkeypatch):
    from developer_gateway.backends import CodexAppServerDeveloperBackend
    from developer_gateway.codex_client import CodexAppServer

    async def legacy_request(self, method, params=None):
        assert params["sandbox"] == "read-only"
        return {
            "result": {
                "thread": {
                    "id": "codex_old",
                    "turns": [
                        {
                            "items": [
                                {"type": "reasoning", "text": "hidden"},
                                {"type": "agentMessage", "text": "visible"},
                            ]
                        }
                    ],
                }
            }
        }

    monkeypatch.setattr(CodexAppServer, "request", legacy_request)
    result = await CodexAppServerDeveloperBackend().request("thread/resume", {"cwd": str(tmp_path)})
    assert result["result"]["thread"]["turns"][0]["items"] == [
        {"type": "agentMessage", "text": "visible"}
    ]


@pytest.mark.parametrize(
    "mode,configured,unavailable,expected",
    [
        ("api", True, False, "api"),
        ("codex", True, False, "codex"),
        ("auto", True, False, "api"),
        ("auto", False, False, "codex"),
        ("auto", True, True, "codex"),
    ],
)
@pytest.mark.asyncio
async def test_backend_selection_and_thread_pinning(
    tmp_path, monkeypatch, mode, configured, unavailable, expected
):
    monkeypatch.setenv("JARVIS_DEVELOPER_BACKEND", mode)
    router = DeveloperBackends(tmp_path)

    class Backend:
        def __init__(self, name):
            self.name = name
            self.configured = configured
            self.provider_unavailable = unavailable

        async def request(self, method, params):
            return {"result": {"thread": {"id": self.name + "_thread"}}}

    router.api = Backend("api")
    router.codex = Backend("codex")
    created = await router.request("thread/start", {"cwd": str(tmp_path)})
    assert created["result"]["thread"]["id"] == expected + "_thread"
    monkeypatch.setenv("JARVIS_DEVELOPER_BACKEND", "codex" if expected == "api" else "api")
    resumed = await router.request("thread/resume", {"threadId": expected + "_thread"})
    assert resumed["result"]["thread"]["id"] == expected + "_thread"
