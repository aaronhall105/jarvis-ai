import asyncio
import threading
from pathlib import Path

from fastapi.testclient import TestClient

from developer_gateway import app as gateway
from developer_gateway.api_backend import OpenAIResponsesDeveloperBackend
from developer_gateway.backends import DeveloperBackends
from developer_gateway.tests.test_api_backend import Client, response


def test_websocket_disconnect_then_reconnect_keeps_server_job(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    router = DeveloperBackends(tmp_path)

    async def provider(thread):
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        return response()

    async def observe(event):
        if event["method"] == "turn/completed":
            finished.set()

    def backend_on_server_loop():
        if router.api is None:
            router.api = OpenAIResponsesDeveloperBackend(
                tmp_path, tmp_path / "agent", client=Client(response()), key=""
            )
            router.api.provider = provider
            router.api.sinks.add(observe)
        return router.api

    monkeypatch.setattr(router, "api_backend", backend_on_server_loop)
    monkeypatch.setattr(gateway, "TOKEN", "fixture-token")
    monkeypatch.setattr(gateway, "WORKSPACES", {"jarvis": tmp_path})
    monkeypatch.setattr(gateway, "codex", router)
    with TestClient(gateway.app) as client:
        backend = router.api
        with client.websocket_connect("/api/developer") as socket:
            socket.send_json({"type": "auth", "token": "fixture-token"})
            assert socket.receive_json()["type"] == "auth.ok"
            socket.send_json({"type": "thread.start", "workspace": "jarvis", "request_id": 1})
            tid = socket.receive_json()["result"]["thread"]["id"]
            socket.send_json(
                {
                    "type": "turn.start",
                    "workspace": "jarvis",
                    "thread_id": tid,
                    "text": "inspect",
                    "request_id": 2,
                }
            )
            assert started.wait(2)
        # Socket gone, server task still owns the job.
        assert not backend.jobs[tid].done()
        release.set()
        assert finished.wait(2)
        with client.websocket_connect("/api/developer") as socket:
            socket.send_json({"type": "auth", "token": "fixture-token"})
            assert socket.receive_json()["type"] == "auth.ok"
            socket.send_json(
                {"type": "thread.resume", "workspace": "jarvis", "thread_id": tid, "request_id": 3}
            )
            thread = socket.receive_json()["result"]["thread"]
            assert thread["status"] == "completed"
            assert thread["turns"][-1]["items"][-1]["text"] == "Grounded result"
