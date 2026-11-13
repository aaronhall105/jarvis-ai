"""Native product routes relay exact signed capabilities, never grant them."""

import json
import os
import socket
import struct
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from developer_gateway import autonomy_api, development_client as ipc


def envelope(action="prepare_development"):
    body = {key: "a" * 64 for key in ipc.FIELDS[action]}
    body.update(
        version=1,
        action=action,
        key_id="a" * 64,
        request_id=str(uuid.uuid4()),
        signature="fixture-not-an-owner-signature",
        expires_at=int(time.time()) + 60,
    )
    if action == "prepare_development":
        body.update(
            candidate_id=75,
            goal="Owner-requested voice interruption improvement",
            source_sha="b" * 40,
            allowed_paths=["bridge/app/ai_engine.py"],
            provider="openai",
            model="gpt-5.3-codex",
            max_calls=1,
            max_cost_usd=1,
            deadline_seconds=120,
            guardian_generation=str(uuid.uuid4()),
        )
    else:
        body["task_id"] = str(uuid.uuid4())
    if action == "create_development_pr":
        body.update(
            repository="aaronhall105/jarvis-ai",
            base_branch="jarvis/unified-production",
            base_sha="b" * 40,
            base_tree_sha="c" * 40,
            head_tree_sha="d" * 40,
            allowed_paths=["bridge/app/ai_engine.py"],
        )
    return body


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(
        autonomy_api.router(lambda: SimpleNamespace(), lambda auth: auth == "Bearer fixture")
    )
    with TestClient(app) as connection:
        yield connection


HEADERS = {"Authorization": "Bearer fixture"}


@pytest.mark.parametrize(
    "suffix,action",
    [
        ("start", "prepare_development"),
        ("activate", "activate_development"),
        ("publish", "create_development_pr"),
    ],
)
def test_authentication_and_domain_are_separate(client, monkeypatch, suffix, action):
    seen = []

    def exchange(path, body, **kwargs):
        seen.append((path, body, kwargs))
        return {"ok": False, "error": "signature_invalid"}

    monkeypatch.setattr(ipc, "_exchange", exchange)
    request = envelope(action)
    assert client.post("/api/autonomy/development/" + suffix, json=request).status_code == 401
    assert not seen
    response = client.post("/api/autonomy/development/" + suffix, headers=HEADERS, json=request)
    assert response.status_code == 403
    assert seen[0][1] == request  # No re-signing, ID substitution or scope mutation.
    assert seen[0][0] == (ipc.INTEGRATION_SOCKET if suffix == "publish" else ipc.OPERATOR_SOCKET)
    seen.clear()
    request["action"] = "approve_development"  # A coding signature is not PR/activation authority.
    assert (
        client.post(
            "/api/autonomy/development/" + suffix, headers=HEADERS, json=request
        ).status_code
        == 403
    )
    assert not seen


@pytest.mark.parametrize("raw", [b'{"action":"a","action":"b"}', b'{"x":NaN}', b"[]", b"{"])
def test_ambiguous_json_never_reaches_protected_socket(client, monkeypatch, raw):
    monkeypatch.setattr(ipc, "_exchange", lambda *_args, **_kw: pytest.fail("must not send"))
    assert (
        client.post("/api/autonomy/development/start", headers=HEADERS, content=raw).status_code
        == 400
    )


def test_expired_approval_is_not_forwarded_and_generic_resume_still_denied(client, monkeypatch):
    monkeypatch.setattr(ipc, "_exchange", lambda *_args, **_kw: pytest.fail("must not send"))
    body = envelope()
    body["expires_at"] = int(time.time()) - 1
    assert (
        client.post("/api/autonomy/development/start", headers=HEADERS, json=body).status_code
        == 410
    )
    assert (
        client.post("/api/autonomy/control", headers=HEADERS, json={"action": "resume"}).status_code
        == 403
    )


def test_lost_response_never_retries_or_claims_task_created(client, monkeypatch):
    seen = []

    def lost(*args, **kwargs):
        seen.append(args)
        raise TimeoutError("fixture ambiguous outcome")

    monkeypatch.setattr(ipc, "_exchange", lost)
    response = client.post("/api/autonomy/development/start", headers=HEADERS, json=envelope())
    assert response.status_code == 503
    assert "do not resend" in response.json()["detail"]
    assert len(seen) == 1


def test_signed_preparation_receipt_does_not_invent_a_task(client, monkeypatch):
    body = envelope()
    monkeypatch.setattr(
        ipc,
        "_exchange",
        lambda *_args, **_kw: {
            "ok": True,
            "result": {"status": "preparation_requested", "request_id": body["request_id"]},
        },
    )
    response = client.post("/api/autonomy/development/start", headers=HEADERS, json=body)
    assert response.status_code == 200
    assert response.json()["task_id"] is None
    assert response.json()["status"] == "preparation_requested"


def test_read_only_preview_uses_fixed_identifier_and_exact_protected_request(client, monkeypatch):
    body = envelope()
    unsigned = {k: v for k, v in body.items() if k not in {"key_id", "signature"}}
    seen = []

    def exchange(path, request):
        seen.append((path, request))
        return {"version": 3, "status": "ready", "request": unsigned}

    monkeypatch.setattr(ipc, "_exchange", exchange)
    assert client.get("/api/autonomy/development/prepare/75").status_code == 401
    response = client.get("/api/autonomy/development/prepare/75", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["request"] == unsigned
    assert seen == [(ipc.PUBLICATION_SOCKET, {"version": 3, "view": "prepare", "candidate_id": 75})]
    # A changed identity is not silently rendered as another owner's task.
    unsigned["candidate_id"] = 76
    assert client.get("/api/autonomy/development/prepare/75", headers=HEADERS).status_code == 503


@pytest.mark.parametrize(
    "value",
    [
        {"version": 1, "status": "none"},
        {"version": 3, "status": "ready", "request": None},
        {"version": 3, "status": "blocked", "request": {"action": "approve_development"}},
        {"version": 3, "status": "unavailable", "request": None},
    ],
)
def test_invalid_and_unavailable_preview_never_becomes_empty_form(client, monkeypatch, value):
    monkeypatch.setattr(ipc, "_exchange", lambda *_: value)
    assert client.get("/api/autonomy/development/prepare/75", headers=HEADERS).status_code == 503


def test_verified_pr_receipt_returns_only_fixed_repository_url(client, monkeypatch):
    body = envelope("create_development_pr")
    receipt = {
        "status": "PR_REVIEW_REQUIRED",
        "number": 17,
        "head_sha": "e" * 40,
        "base_sha": body["base_sha"],
        "proposal_sha256": body["proposal_sha256"],
        "required_gates": [
            "independent_human_review",
            "protected_ci",
            "codeql",
            "owner_merge_approval",
        ],
    }
    monkeypatch.setattr(ipc, "_exchange", lambda *_args, **_kw: {"ok": True, "result": receipt})
    response = client.post("/api/autonomy/development/publish", headers=HEADERS, json=body)
    assert response.status_code == 200
    assert response.json()["url"] == "https://github.com/aaronhall105/jarvis-ai/pull/17"
    receipt["proposal_sha256"] = "f" * 64
    assert (
        client.post("/api/autonomy/development/publish", headers=HEADERS, json=body).status_code
        == 503
    )


@pytest.mark.parametrize(
    "record",
    [
        None,
        {"status": "DISPATCHING", "proposal_sha256": "a" * 64},
        {"status": "RECONCILIATION_REQUIRED", "proposal_sha256": "a" * 64},
    ],
)
def test_handoff_reconnect_only_reads_existing_record(monkeypatch, record):
    body = envelope("create_development_pr")
    seen = []

    def exchange(path, request, **kwargs):
        seen.append(request)
        return {"ok": True, "result": {"handoff": record}}

    monkeypatch.setattr(ipc, "_exchange", exchange)
    assert ipc.handoff_status(body["task_id"], body) == record
    assert seen == [{"version": 1, "action": "handoff_status", "task_id": body["task_id"]}]


def test_unavailable_integration_never_offers_another_signature(monkeypatch):
    body = envelope("create_development_pr")
    unsigned = {key: value for key, value in body.items() if key not in {"key_id", "signature"}}

    def exchange(path, query, **kwargs):
        if path == ipc.PUBLICATION_SOCKET:
            return {"version": 3, "status": "ready", "request": unsigned, "proposal": {}}
        raise TimeoutError("unknown existing handoff")

    monkeypatch.setattr(ipc, "_exchange", exchange)
    result = ipc.preview("proposal", body["task_id"])
    assert result["status"] == "ready"
    assert result["request"] is None
    assert result["handoff"]["status"] == "UNAVAILABLE"


def test_real_unix_peer_rejected_before_unsigned_data_sent(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("requires unprivileged client fixture")
    path = str(tmp_path / "protected.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    received = []

    def serve():
        connection, _ = listener.accept()
        with connection:
            received.append(connection.recv(4096))

    thread = threading.Thread(target=serve)
    thread.start()
    with pytest.raises(PermissionError, match="untrusted_service_peer"):
        ipc._exchange(path, {"version": 3})
    thread.join(2)
    listener.close()
    assert received == [b""]


def test_actual_integration_peer_credentials_and_framing(tmp_path, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("requires independent nonroot integration fixture identity")
    # The fixture identity is the test account, never the production integration account.
    monkeypatch.setattr(ipc.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=os.geteuid()))
    path = str(tmp_path / "integration.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(1)
    received = []

    def serve():
        connection, _ = listener.accept()
        with connection:
            size = struct.unpack("!I", ipc._receive(connection, 4))[0]
            received.append(ipc.decode(ipc._receive(connection, size)))
            raw = json.dumps({"ok": False, "error": "signature_invalid"}).encode()
            connection.sendall(struct.pack("!I", len(raw)) + raw)

    thread = threading.Thread(target=serve)
    thread.start()
    body = envelope("create_development_pr")
    assert ipc._exchange(path, body, integration=True)["ok"] is False
    thread.join(2)
    listener.close()
    assert received == [body]
