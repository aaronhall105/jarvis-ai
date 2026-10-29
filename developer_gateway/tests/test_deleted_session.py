"""Authenticated request errors cannot erase history or break other requests."""

import pytest
from fastapi.testclient import TestClient

from developer_gateway import app as gateway
from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend
from developer_gateway.state import ThreadNotFound
from developer_gateway.tests.test_api_backend import Client
from developer_gateway.tests.test_gateway import FakeCodex
from developer_gateway.backends import DeveloperBackends


@pytest.mark.parametrize(
    "failure,code",
    [
        (ThreadNotFound("Unknown API thread"), "THREAD_NOT_FOUND"),
        (ValueError("Thread workspace mismatch"), "REQUEST_FAILED"),
        (RuntimeError("fixture-private-provider-detail"), "REQUEST_FAILED"),
        (ValueError("Unknown API thread"), "REQUEST_FAILED"),
    ],
)
def test_correlated_errors_keep_authenticated_socket_and_redact_details(
    monkeypatch, tmp_path, failure, code
):
    class Backend(FakeCodex):
        async def request(self, method, params):
            if method == "thread/resume":
                raise failure
            return await super().request(method, params)

    monkeypatch.setattr(gateway, "TOKEN", "fixture-token")
    monkeypatch.setattr(gateway, "codex", Backend())
    monkeypatch.setattr(gateway, "WORKSPACES", {"jarvis": tmp_path})
    monkeypatch.delenv("JARVIS_AUTONOMY_OBSERVER_ENABLED", raising=False)
    with TestClient(gateway.app) as client, client.websocket_connect("/api/developer") as ws:
        ws.send_json({"type": "auth", "token": "fixture-token"})
        assert ws.receive_json()["type"] == "auth.ok"
        ws.send_json(
            {
                "type": "thread.resume",
                "thread_id": "api_deleted",
                "workspace": "jarvis",
                "request_id": 41,
            }
        )
        error = ws.receive_json()
        assert error["type"] == "error" and error["request_id"] == 41
        assert error["code"] == code
        assert error["thread_id"] == ("api_deleted" if code == "THREAD_NOT_FOUND" else "")
        assert "fixture-private" not in str(error)
        # A rejected resume grants no ownership/approval and does not disconnect.
        ws.send_json(
            {
                "type": "turn.start",
                "thread_id": "api_deleted",
                "text": "inspect",
                "workspace": "jarvis",
                "request_id": 42,
            }
        )
        assert "not owned" in ws.receive_json()["message"]
        ws.send_json(
            {
                "type": "approval.respond",
                "codex_request_id": -1,
                "decision": "accept",
                "request_id": 43,
            }
        )
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "thread.start", "workspace": "jarvis", "request_id": 44})
        result = ws.receive_json()
        assert result["request_id"] == 44 and result["result"]["thread"]["id"] == "thread-safe"


@pytest.mark.asyncio
async def test_deleted_api_thread_is_typed_but_workspace_error_is_not(tmp_path):
    backend = OpenAIResponsesDeveloperBackend(tmp_path, tmp_path / "state", client=Client(), key="")
    first = backend.state.create(str(tmp_path))
    kept = backend.state.create(str(tmp_path))
    backend.state.delete(first["id"])
    with pytest.raises(ThreadNotFound):
        await backend.request("thread/resume", {"threadId": first["id"], "cwd": str(tmp_path)})
    with pytest.raises(ValueError, match="workspace mismatch") as failure:
        await backend.request(
            "thread/resume", {"threadId": kept["id"], "cwd": str(tmp_path / "wrong")}
        )
    assert not isinstance(failure.value, ThreadNotFound)
    assert backend.state.get(kept["id"]) == kept
    await backend.stop()


def test_real_api_delete_resume_then_valid_resume_on_same_socket(tmp_path, monkeypatch):
    router = DeveloperBackends(tmp_path)

    def on_loop():
        if router.api is None:
            router.api = OpenAIResponsesDeveloperBackend(
                tmp_path, tmp_path / "agent", client=Client(), key=""
            )
        return router.api

    monkeypatch.setattr(router, "api_backend", on_loop)
    monkeypatch.setattr(gateway, "TOKEN", "fixture-token")
    monkeypatch.setattr(gateway, "WORKSPACES", {"jarvis": tmp_path})
    monkeypatch.setattr(gateway, "codex", router)
    monkeypatch.delenv("JARVIS_AUTONOMY_OBSERVER_ENABLED", raising=False)
    with TestClient(gateway.app) as client, client.websocket_connect("/api/developer") as ws:
        ws.send_json({"type": "auth", "token": "fixture-token"})
        assert ws.receive_json()["type"] == "auth.ok"

        def request(kind, ident, **args):
            ws.send_json({"type": kind, "workspace": "jarvis", "request_id": ident, **args})
            return ws.receive_json()

        gone = request("thread.start", 1)["result"]["thread"]
        kept = request("thread.start", 2)["result"]["thread"]
        assert request("thread.delete", 3, thread_id=gone["id"])["type"] == "response"
        missing = request("thread.resume", 4, thread_id=gone["id"])
        assert missing["code"] == "THREAD_NOT_FOUND" and missing["request_id"] == 4
        assert missing["thread_id"] == gone["id"]
        assert request("thread.resume", 5, thread_id=kept["id"])["result"]["thread"] == kept
