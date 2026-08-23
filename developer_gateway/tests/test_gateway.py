from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from developer_gateway.app import app, authorised, codex_inputs, thread_options
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


def test_thread_policy_avoids_routine_prompts_but_keeps_workspace_sandbox(tmp_path: Path) -> None:
    assert thread_options(tmp_path) == {
        "cwd": str(tmp_path),
        "approvalPolicy": "on-request",
        "sandbox": "workspace-write",
    }


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


def test_attachment_content_is_bounded_and_never_accepts_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    monkeypatch.setattr("developer_gateway.app.ATTACHMENT_DIR", tmp_path)
    inputs = codex_inputs("inspect this", [{
        "name": "../../safe.log", "mime": "text/plain",
        "data": base64.b64encode(b"hello").decode(), "path": "/etc/passwd",
    }])
    assert inputs == [
        {"type": "text", "text": "inspect this"},
        {"type": "text", "text": "Attached file safe.log:\n\nhello"},
    ]
    with pytest.raises(ValueError, match="Unsupported"):
        codex_inputs("x", [{"name": "x.bin", "mime": "application/octet-stream", "data": "eA=="}])


def test_image_attachment_is_copied_to_private_gateway_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import base64
    monkeypatch.setattr("developer_gateway.app.ATTACHMENT_DIR", tmp_path)
    inputs = codex_inputs("inspect", [{"name": "screen.png", "mime": "image/png", "data": base64.b64encode(b"png").decode()}])
    copied = Path(inputs[1]["path"])
    assert inputs[1]["type"] == "localImage"
    assert copied.parent == tmp_path
    assert copied.read_bytes() == b"png"
    assert copied.stat().st_mode & 0o777 == 0o600
