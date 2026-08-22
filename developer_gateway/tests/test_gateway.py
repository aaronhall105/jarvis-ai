from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from developer_gateway.app import app, authorised
from developer_gateway.codex_client import canonical_workspace


def test_authentication_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("developer_gateway.app.TOKEN", "correct-token")
    assert authorised("Bearer correct-token")
    assert not authorised(None)
    assert not authorised("Bearer incorrect")


def test_workspace_allowlist_rejects_paths_and_unknown_ids(tmp_path: Path) -> None:
    allowed = {"jarvis": tmp_path}
    assert canonical_workspace("jarvis", allowed) == ("jarvis", tmp_path.resolve())
    with pytest.raises(ValueError):
        canonical_workspace("../../etc", allowed)
    with pytest.raises(ValueError):
        canonical_workspace("/home/aaron/jarvis", allowed)


class FakeCodex:
    healthy = True

    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    def subscribe(self, sink) -> None: self.sink = sink
    def unsubscribe(self, sink) -> None: pass
    async def request(self, method, params):
        if method == "thread/start":
            return {"result": {"thread": {"id": "thread-safe"}}}
        if method == "thread/resume":
            return {"result": {"thread": {"id": params["threadId"]}}}
        return {"result": {}}
    async def respond(self, request_id, result) -> None: pass


def test_websocket_rejects_bad_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("developer_gateway.app.TOKEN", "correct-token")
    monkeypatch.setattr("developer_gateway.app.codex", FakeCodex())
    with TestClient(app) as client, client.websocket_connect("/api/developer") as socket:
        socket.send_json({"type": "auth", "token": "incorrect"})
        assert socket.receive_json()["type"] == "auth.error"


def test_authenticated_client_can_start_and_resume_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("developer_gateway.app.TOKEN", "correct-token")
    monkeypatch.setattr("developer_gateway.app.codex", FakeCodex())
    with TestClient(app) as client, client.websocket_connect("/api/developer") as socket:
        socket.send_json({"type": "auth", "token": "correct-token"})
        assert socket.receive_json()["type"] == "auth.ok"
        socket.send_json({"type": "thread.start", "workspace": "jarvis-wear", "request_id": 1})
        started = socket.receive_json()
        assert started["result"]["thread"]["id"] == "thread-safe"
        socket.send_json({"type": "thread.resume", "workspace": "jarvis-wear", "thread_id": "thread-safe", "request_id": 2})
        resumed = socket.receive_json()
        assert resumed["result"]["thread"]["id"] == "thread-safe"


def test_turn_rejects_thread_not_owned_by_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("developer_gateway.app.TOKEN", "correct-token")
    monkeypatch.setattr("developer_gateway.app.codex", FakeCodex())
    with TestClient(app) as client, client.websocket_connect("/api/developer") as socket:
        socket.send_json({"type": "auth", "token": "correct-token"})
        assert socket.receive_json()["type"] == "auth.ok"
        socket.send_json({
            "type": "turn.start", "workspace": "jarvis-wear",
            "thread_id": "someone-elses-thread", "text": "status", "request_id": 3,
        })
        rejected = socket.receive_json()
        assert rejected["type"] == "error"
        assert "not owned" in rejected["message"]
