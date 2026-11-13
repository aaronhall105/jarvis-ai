"""Presentation state never becomes an approval or replaces protected IPC."""

import os
import socket
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from developer_gateway import autonomy_api, autonomy_host, pending_task_client


def view(status="idle"):
    return {
        "version": 2,
        "status": status,
        "task_id": None,
        "lifecycle_state": None,
        "provider_calls": 0,
        "development_task": None,
    }


@pytest.mark.parametrize(
    "status",
    [
        "idle",
        "pending",
        "expired",
        "blocked",
        "running",
        "review_pending",
        "review_required",
        "failed",
    ],
)
def test_valid_status_contract_and_authenticated_gateway(monkeypatch, status):
    value = view(status)
    monkeypatch.setattr(
        pending_task_client, "_fetch", lambda version: value if version == 2 else None
    )
    assert pending_task_client.fetch_lifecycle() == value
    monkeypatch.setattr(
        autonomy_api, "guardian_status", lambda: {"state": "STOPPED", "trusted": True}
    )
    app = FastAPI()
    app.include_router(
        autonomy_api.router(
            lambda: SimpleNamespace(snapshot=lambda guardian: {"status": guardian["state"]}),
            lambda authorization: authorization == "Bearer fixture",
        )
    )
    with TestClient(app) as client:
        assert client.get("/api/autonomy").status_code == 401
        response = client.get("/api/autonomy", headers={"Authorization": "Bearer fixture"})
    assert response.status_code == 200
    assert response.json()["development_status"]["status"] == status
    assert response.json()["development_task"] is None
    assert response.json()["status"] == "STOPPED"


@pytest.mark.parametrize(
    "invalid",
    [
        {"version": 1, "status": "none"},
        view("approved"),
        {**view(), "provider_calls": True},
        {**view("running"), "development_task": {"action": "approve_development"}},
        {**view("approval_requested"), "development_task": {"action": "approve_development"}},
    ],
)
def test_old_malformed_or_contradictory_publisher_is_unavailable(monkeypatch, invalid):
    monkeypatch.setattr(pending_task_client, "_fetch", lambda _version: invalid)
    assert autonomy_host.development_lifecycle() == {
        "status": "unavailable",
        "reason": "publisher_unavailable",
        "development_task": None,
    }


def test_missing_and_damaged_publisher_are_not_idle(monkeypatch):
    monkeypatch.setattr(
        pending_task_client,
        "_fetch",
        lambda _: {"version": 2, "status": "unavailable", "reason": "controller_unavailable"},
    )
    assert autonomy_host.development_lifecycle()["status"] == "unavailable"

    def missing(_version):
        raise FileNotFoundError("no socket")

    monkeypatch.setattr(pending_task_client, "_fetch", missing)
    assert autonomy_host.development_lifecycle() == {
        "status": "unavailable",
        "reason": "publisher_unavailable",
        "development_task": None,
    }


@pytest.mark.parametrize("field", ["approval_accepted", "proposal_available"])
@pytest.mark.parametrize("value", [True, 1, "true", None])
def test_unverified_or_wrong_state_proof_is_not_shown(monkeypatch, field, value):
    monkeypatch.setattr(pending_task_client, "_fetch", lambda _: {**view(), field: value})
    assert autonomy_host.development_lifecycle()["status"] == "unavailable"


def test_review_availability_requires_explicit_protected_receipt(monkeypatch):
    value = {
        **view("review_required"),
        "candidate_id": 75,
        "approval_accepted": False,
        "proposal_available": True,
    }
    monkeypatch.setattr(pending_task_client, "_fetch", lambda _: value)
    assert pending_task_client.fetch_lifecycle() == value


def test_real_socket_rejects_nonroot_publisher_before_request(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("non-root peer fixture requires unprivileged test process")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = str(tmp_path / "publication.sock")
    listener.bind(path)
    listener.listen(1)
    received = []

    def serve():
        connection, _ = listener.accept()
        with connection:
            received.append(connection.recv(1024))

    thread = threading.Thread(target=serve)
    thread.start()
    monkeypatch.setattr(pending_task_client, "SOCKET", path)
    with pytest.raises(PermissionError, match="untrusted_publication_peer"):
        pending_task_client.fetch_lifecycle()
    thread.join(3)
    listener.close()
    assert received == [b""]


@pytest.mark.parametrize(
    "pid,uid,gid,expected",
    [
        (1, 0, 0, True),
        (1, 65534, 65534, True),
        (1, 65534, 0, True),
        (1, 0, 65534, True),
        (2, 0, 0, False),
        (99, 65534, 65534, False),
        (1, 1000, 1000, False),
        (1, 0, 1000, False),
        (1, 1000, 0, False),
    ],
)
def test_publication_peer_identity_validation(pid, uid, gid, expected):
    assert pending_task_client._trusted_publication_peer(pid, uid, gid) is expected
