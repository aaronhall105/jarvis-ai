"""Disposable container rehearsal, never an installed runtime or host command.

All fixed production paths below exist only in the disposable Docker filesystem.
The controller/worker, publisher, operator relay and admission implementations are
real. The only provider adaptation selects a loopback HTTP fixture for the real
SDK. Guardian activation uses a separately signed fixture ceremony and real
write_state generation rotation; it never starts an unrelated autonomy service.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

SOURCE = (
    Path("/usr/local/libexec/jarvis-autonomy-broker")
    if Path(__file__).name == "entry.py"
    else Path("/opt/jarvis-source")
)
VENV = Path("/home/aaron/.venvs/jarvis-phase2")
sys.path.insert(0, str(SOURCE))
# Coordinator and broker use the actual OS interpreter. Only fixture owner/HTTP
# helpers use the test venv; the coding child must use the sealed production
# worker environment. Never disguise a test venv as the privileged interpreter.

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from tools.autonomy_broker import Denied, Ledger, canonical  # noqa: E402
from tools.autonomy_broker_ipc import handle  # noqa: E402
from tools.autonomy_broker_operator import OperatorDispatcher  # noqa: E402
from tools.autonomy_broker_runtime import (  # noqa: E402
    forward_development_approval,
    installed_policy,
    main as broker_main,
)
from tools.autonomy_guardian import Guardian  # noqa: E402
from tools.supervised_development_controller import (  # noqa: E402
    CONTROLLER_DATABASE,
    SupervisedDevelopmentController,
)
from tools.supervised_development_runtime import CANONICAL, GOVERNOR_DATABASE  # noqa: E402

ENTRY = Path("/usr/local/libexec/jarvis-autonomy-broker/entry.py")
BASE = Path("/var/lib/jarvis-autonomy-broker")
SOCKETS = Path("/run/jarvis-autonomy-broker")
WORKER_UID = 600
OWNER_UID = 1000
PROVIDER_PORT = 18089


def private_json(path, value, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))
    path.chmod(mode)


def child_identity(uid):
    def drop():
        os.setgroups([])
        os.setgid(uid)
        os.setuid(uid)

    return drop


def run_child(role, *, uid=0, env=None, wait=True):
    interpreter = (
        "/usr/bin/python3" if role == "development-controller" else str(VENV / "bin/python")
    )
    process = subprocess.Popen(
        [interpreter, "-I", "-B", str(ENTRY), role],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=child_identity(uid) if uid else None,
    )
    if not wait:
        return process
    stdout, stderr = process.communicate(timeout=150)
    assert process.returncode == 0, (role, process.returncode, stderr[-2000:])
    return json.loads(stdout)


def exchange(path, body):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(str(path))
        raw = canonical(body)
        connection.sendall(struct.pack("!I", len(raw)) + raw)
        size = struct.unpack("!I", connection.recv(4))[0]
        data = bytearray()
        while len(data) < size:
            data.extend(connection.recv(size - len(data)))
        return json.loads(data)


def sign(private, body, domain):
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    body = {**body, "key_id": hashlib.sha256(public).hexdigest()}
    body["signature"] = base64.b64encode(
        private.sign(domain + canonical(body), ec.ECDSA(hashes.SHA256()))
    ).decode()
    return body


def create_fixture():
    assert os.getuid() == 0 and Path("/.dockerenv").is_file()
    with Path("/etc/passwd").open("a") as stream:
        stream.write("jarvis-autonomy:x:600:600::/var/lib/jarvis-autonomy:/usr/sbin/nologin\n")
        stream.write(
            "jarvis-integration:x:700:700::/var/lib/jarvis-development-integration:/usr/sbin/nologin\n"
        )
        stream.write("aaron:x:1000:1000::/nonexistent:/usr/sbin/nologin\n")
    with Path("/etc/group").open("a") as stream:
        stream.write("jarvis-autonomy:x:600:\n")
        stream.write("jarvis-integration:x:700:\naaron:x:1000:\n")
    os.setgroups([WORKER_UID])
    for directory in [BASE, SOCKETS, BASE / "operator", BASE / "development-admission"]:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700 if directory.name in {"operator", "development-admission"} else 0o755)
    for directory in [BASE / "work", BASE / "worktrees"]:
        directory.mkdir()
        directory.chmod(0o770)
        os.chown(directory, WORKER_UID, WORKER_UID)
    ENTRY.parent.mkdir(parents=True, exist_ok=True)
    from tools.autonomy_broker_install import payload

    staged = payload()
    for target, (contents, mode) in staged.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
        target.chmod(mode)
    # Real owner-only offline provisioning, inside this disposable filesystem.
    # The coordinator retains /usr/bin/python3; SDK and verification packages
    # are installed solely into the new sealed, lock-derived worker path.
    provision = subprocess.run(
        [
            "/usr/bin/python3",
            "-I",
            "-B",
            str(ENTRY.parent / "tools/supervised_development_environment.py"),
            "--install-wheelhouse",
            "/fixture-wheelhouse",
        ],
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert provision.returncode == 0, provision.stderr[-3000:]
    # Fixture-only bootstrap adds local SDK endpoint/test dependencies. All
    # production payload modules are installed unchanged by the real manifest.
    ENTRY.write_text(Path(__file__).read_text())
    ENTRY.chmod(0o644)

    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    owner_key = Path("/tmp/disposable-owner-key.pem")
    owner_key.write_bytes(
        private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    owner_key.chmod(0o600)
    os.chown(owner_key, OWNER_UID, OWNER_UID)
    policy = Path("/etc/jarvis-autonomy/broker.json")
    private_json(
        policy,
        {
            "version": 1,
            "operator_uid": OWNER_UID,
            "autonomy_uid": WORKER_UID,
            "autonomy_gid": WORKER_UID,
            "operator_public_keys": {key_id: base64.b64encode(public).decode()},
        },
    )
    private_json(
        BASE / "installed.json",
        {
            "files": {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [policy, *staged]
            }
        },
    )
    private_json(
        Path("/etc/jarvis-development-integration/policy.json"),
        {
            "version": 1,
            "repository": "aaronhall105/jarvis-ai",
            "base_branch": "jarvis/unified-production",
            "integration_uid": 700,
            "github_actor_id": 123,
            "owner_public_keys": {key_id: base64.b64encode(public).decode()},
        },
        0o644,
    )
    for directory in (
        Path("/var/lib/jarvis-development-integration"),
        Path("/run/jarvis-development-integration"),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700 if directory.parts[1] == "var" else 0o711)
        os.chown(directory, 700, 700)
    guardian_policy = Path("/etc/jarvis-autonomy/guardian.json")
    private_json(
        guardian_policy,
        {
            "version": 1,
            "operator_uid": OWNER_UID,
            "autonomy_account": "jarvis-autonomy",
            "privileged_operations": [],
        },
        0o644,
    )
    guardian_base = Path("/var/lib/jarvis-autonomy-guardian")
    private_json(
        guardian_base / "installed.json",
        {"files": {str(guardian_policy): hashlib.sha256(guardian_policy.read_bytes()).hexdigest()}},
        0o644,
    )
    (guardian_base / "control.lock").touch(mode=0o600)
    (guardian_base / "audit").mkdir()
    (guardian_base / "audit/events.jsonl").touch(mode=0o600)
    Guardian().write_state("STOPPED")
    assert Guardian().status()["trusted"] is True
    installed_policy()

    (CANONICAL / "tools/tests").mkdir(parents=True)
    (CANONICAL / "tools/diagnostic.py").write_text("VALUE = 1\n")
    (CANONICAL / "tools/tests/test_diagnostic.py").write_text(
        "from tools.diagnostic import VALUE\n\n\ndef test_value():\n    assert VALUE == 3\n"
    )
    for command in [
        ["init", "-q"],
        ["config", "user.email", "fixture@example.invalid"],
        ["config", "user.name", "Disposable fixture"],
        ["add", "."],
        ["commit", "-qm", "fixture base"],
    ]:
        subprocess.run(["/usr/bin/git", "-C", str(CANONICAL), *command], check=True)
    sha = subprocess.check_output(
        ["/usr/bin/git", "-C", str(CANONICAL), "rev-parse", "HEAD"], text=True
    ).strip()
    evidence = {
        "source": {"fixture": "existing deterministic test asserts VALUE3"},
        "observed": 1,
        "expected": 3,
    }
    evidence_hash = hashlib.sha256(canonical(evidence)).hexdigest()
    paths = ["tools/diagnostic.py", "tools/tests/test_diagnostic.py"]
    scope = {
        "scope": "application",
        "evidence_failure_id": 1,
        "evidence_sha256": evidence_hash,
        "source_sha": sha,
        "affected_files": paths,
        "review": {
            "reviewed_by": "disposable-test",
            "source_sha": sha,
            "evidence_sha256": evidence_hash,
            "reproduction": "Existing deterministic regression expects VALUE3 but receives VALUE1.",
            "file_findings": {
                path: "The disposable source and regression establish the incorrect value."
                for path in paths
            },
        },
    }
    GOVERNOR_DATABASE.parent.mkdir(parents=True)
    with sqlite3.connect(GOVERNOR_DATABASE) as database:
        database.executescript(
            "CREATE TABLE improvement_failures(failure_id INTEGER PRIMARY KEY,summary TEXT,evidence_json TEXT,status TEXT);"
            "CREATE TABLE improvement_candidates(candidate_id INTEGER PRIMARY KEY,failure_id INTEGER,status TEXT,summary TEXT,development_scope_json TEXT);"
        )
        database.execute(
            "INSERT INTO improvement_failures VALUES(1,?,?,?)",
            ("Incorrect diagnostic value", json.dumps(evidence), "queued"),
        )
        database.execute(
            "INSERT INTO improvement_candidates VALUES(1,1,?,?,?)",
            ("queued", "Correct the evidenced diagnostic result.", json.dumps(scope)),
        )
    GOVERNOR_DATABASE.chmod(0o644)
    controller = SupervisedDevelopmentController(CONTROLLER_DATABASE, database_mode=0o660)
    controller.close()
    os.chown(CONTROLLER_DATABASE, WORKER_UID, WORKER_UID)
    return sha, hashlib.sha256(GOVERNOR_DATABASE.read_bytes()).hexdigest()


def activated(role, path):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    path.chmod(0o666)  # Disposable peers test SO_PEERCRED, not an installation mode.
    listener.listen(16)
    fd = listener.fileno()
    env = {**os.environ, "FIXTURE_LISTENER_FD": str(fd)}
    process = subprocess.Popen(
        [str(VENV / "bin/python"), str(ENTRY), role],
        pass_fds=(fd,),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    listener.close()
    return process


def operator_service():
    policy = installed_policy()
    guardian = Guardian()
    ledger = Ledger(BASE / "operator/operations.sqlite3")

    def stop(actor):
        guardian.stop(actor, terminate=lambda: None)  # No host/systemd in disposable fixture.

    from tools import autonomy_guardian as guardian_module
    from tools.supervised_development_product import production_operator, CONTROLLER_UNIT

    # Disposable service-manager boundary. The orchestrator starts the actual
    # installed root entrypoint only after this fixed unit notification. All
    # signatures, durable queue, task binding and Guardian transitions are real.
    def fixed_systemctl(*args):
        if args == ("start", "--no-block", CONTROLLER_UNIT):
            Path("/tmp/controller-start-request").write_text("requested")
            return True
        if args == ("is-active", "--quiet", CONTROLLER_UNIT):
            return Path("/tmp/controller-process-active").exists()
        raise AssertionError(("unexpected service operation", args))

    guardian_module.fixed_systemctl = fixed_systemctl
    original_run = subprocess.run

    def service_read(command, *args, **kwargs):
        if command == ["/usr/bin/systemctl", "is-active", guardian_module.UNIT]:
            return subprocess.CompletedProcess(command, 3, b"inactive\n", b"")
        if command == ["/usr/bin/systemctl", "is-enabled", guardian_module.UNIT]:
            return subprocess.CompletedProcess(command, 1, b"disabled\n", b"")
        return original_run(command, *args, **kwargs)

    subprocess.run = service_read

    class FixtureGuardian:
        status = guardian.status

        @staticmethod
        def stop(actor):
            return guardian.stop(actor, terminate=lambda: None)

        @staticmethod
        def resume(euid, actor, ready, start, **kwargs):
            return guardian.resume(euid, actor, ready, start, terminate=lambda: None, **kwargs)

    keys = {key: base64.b64decode(value) for key, value in policy["operator_public_keys"].items()}
    product = production_operator(ledger, keys, FixtureGuardian())

    dispatcher = OperatorDispatcher(
        ledger,
        relay_uid=OWNER_UID,
        autonomy_uid=WORKER_UID,
        public_keys=keys,
        guardian=guardian.status,
        stop=stop,
        resume=lambda *_: (_ for _ in ()).throw(AssertionError("legacy resume forbidden")),
        development_approve=forward_development_approval,
        development_product=product.dispatch,
    )

    def dispatch(raw, actor):
        if actor == 0 and raw == canonical({"emergency_stop": True}):
            stop(0)
            return {"state": "STOPPED"}
        try:
            return dispatcher.dispatch(raw, actor)
        except Exception as exc:
            print(
                "fixture_operator_failure",
                type(exc).__name__,
                str(exc),
                file=sys.stderr,
                flush=True,
            )
            raise

    listener = socket.socket(fileno=3)
    while True:
        connection, _ = listener.accept()
        with connection:
            handle(connection, dispatch)


def owner_flow():
    sys.path.insert(0, "/opt/jarvis-source")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from developer_gateway import autonomy_api

    # Only the observation transport is fixture-local; admission and signature
    # checks are actual protected Unix connections from UID1000.
    autonomy_api.guardian_status = Guardian().status
    app = FastAPI()
    app.include_router(
        autonomy_api.router(
            lambda: SimpleNamespace(snapshot=lambda state: {"status": state["state"]}),
            lambda value: value == "Bearer disposable-gateway-session",
        )
    )
    private = serialization.load_pem_private_key(
        Path("/tmp/disposable-owner-key.pem").read_bytes(), password=None
    )
    with TestClient(app) as client:
        assert client.get("/api/autonomy").status_code == 401
        headers = {"Authorization": "Bearer disposable-gateway-session"}
        preview = client.get("/api/autonomy/development/prepare/1", headers=headers)
        assert preview.status_code == 200 and preview.json()["status"] == "ready", preview.text
        preparation = sign(
            private, preview.json()["request"], b"jarvis-development-preparation-v1\n"
        )
        prepared = client.post("/api/autonomy/development/start", headers=headers, json=preparation)
        assert prepared.status_code == 200, prepared.text
        assert Guardian().status()["state"] == "STOPPED"
        deadline = time.monotonic() + 20
        envelope = None
        while time.monotonic() < deadline:
            result = client.get("/api/autonomy", headers=headers).json()
            envelope = result.get("development_task")
            if envelope:
                break
            time.sleep(0.1)
        assert envelope, result
        assert result["development_status"]["status"] == "approval_requested"
        assert envelope["max_calls"] == 2
        body = {key: value for key, value in envelope.items() if key != "goal"}
        body["request_id"] = body.pop("request_ref")
        signed = sign(private, body, b"jarvis-development-admission-v1\n")
        wrong_key = sign(
            ec.generate_private_key(ec.SECP256R1()), body, b"jarvis-development-admission-v1\n"
        )
        assert (
            client.post("/api/autonomy/control", headers=headers, json=wrong_key).status_code == 403
        )
        rejected = {**signed, "signature": "bad"}
        assert (
            client.post("/api/autonomy/control", headers=headers, json=rejected).status_code == 403
        )
        expired = {**signed, "expires_at": int(time.time()) - 1}
        assert (
            client.post("/api/autonomy/control", headers=headers, json=expired).status_code == 410
        )
        assert client.post("/api/autonomy/control", headers=headers, json=signed).status_code == 200
        replay_conflict = sign(
            private, {**body, "source_sha": "0" * 40}, b"jarvis-development-admission-v1\n"
        )
        assert (
            client.post("/api/autonomy/control", headers=headers, json=replay_conflict).status_code
            == 403
        )
        assert Guardian().status()["state"] == "STOPPED"
        admission = {
            key: signed[key]
            for key in (
                "task_id",
                "source_sha",
                "parameters_sha256",
                "allowed_paths",
                "provider",
                "model",
                "max_calls",
                "max_cost_usd",
                "deadline_seconds",
            )
        }
        admission["approval_id"] = signed["request_id"]
        assert (
            exchange(SOCKETS / "development-admission.sock", admission)["error"]
            == "development_admission_peer"
        )
        legacy_activation = sign(
            private,
            {
                "version": 1,
                "action": "resume",
                "request_id": str(uuid.uuid4()),
                "expires_at": int(time.time()) + 45,
                "generation": envelope["guardian_generation"],
            },
            b"jarvis-autonomy-control-v1\n",
        )
        assert (
            client.post(
                "/api/autonomy/control", headers=headers, json=legacy_activation
            ).status_code
            == 403
        )
        preview = client.get(
            "/api/autonomy/development/activation/" + envelope["task_id"], headers=headers
        )
        assert preview.status_code == 200 and preview.json()["status"] == "ready", preview.text
        activation = sign(private, preview.json()["request"], b"jarvis-development-activation-v1\n")
        transition = client.post(
            "/api/autonomy/development/activate", headers=headers, json=activation
        )
        assert transition.status_code == 200, transition.text
        assert Guardian().status()["generation"] != envelope["guardian_generation"]
    print(
        json.dumps(
            {"task_id": envelope["task_id"], "admission": admission, "gateway_uid": os.getuid()}
        )
    )


def provider_server():
    class Provider(BaseHTTPRequestHandler):
        calls = 0
        transmitted = threading.Event()
        release = threading.Event()

        def do_POST(self):
            assert self.path == "/v1/responses"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["model"] == "gpt-5.3-codex" and body["store"] is False
            type(self).calls += 1
            type(self).transmitted.set()
            number = type(self).calls
            assert number <= 2
            if os.environ.get("JARVIS_FIXTURE_OUTCOME") in {"terminated", "guardian-stopped"}:
                type(self).release.wait(20)
                self.close_connection = True
                return
            if os.environ.get("JARVIS_FIXTURE_OUTCOME") == "disconnect":
                # Real SDK sees an ambiguous transmitted request; no response,
                # retry or follow-up request may be sent.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            if number == 2:
                assert "local_verification_failure" in body["input"]
            result = json.dumps(
                {
                    "protocol_version": 1,
                    "explanation": "Correct the reproducible fixture value.",
                    "edits": [
                        {
                            "path": "tools/diagnostic.py",
                            "old_text": f"VALUE = {number}\n",
                            "new_text": f"VALUE = {number + 1}\n",
                        }
                    ],
                }
            )
            response = {
                "id": f"resp_fixture_{number}",
                "object": "response",
                "created_at": int(time.time()),
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "id": f"msg_{number}",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": result, "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200},
            }
            raw = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", PROVIDER_PORT), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Provider


def proposal_flow():
    sys.path.insert(0, "/opt/jarvis-source")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from developer_gateway import autonomy_api

    autonomy_api.guardian_status = Guardian().status
    app = FastAPI()
    app.include_router(
        autonomy_api.router(
            lambda: SimpleNamespace(snapshot=lambda state: {"status": state["state"]}),
            lambda value: value == "Bearer disposable-gateway-session",
        )
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/autonomy/development/proposal/" + os.environ["JARVIS_FIXTURE_TASK_ID"],
            headers={"Authorization": "Bearer disposable-gateway-session"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "ready", body
        proposal = body["proposal"]
        assert proposal["provider_calls"] == 2
        assert proposal["cost_usd"] == 0.0004
        assert hashlib.sha256(proposal["diff"].encode()).hexdigest() == proposal["diff_sha256"]
        assert [item["name"] for item in proposal["checks"]] == [
            "pytest",
            "ruff",
            "bandit",
            "compile",
        ]
        assert proposal["checks"][0]["test_counts"]["executed"] > 0
        assert body["handoff"] is None and body["request"] is not None, body
        original = json.loads(json.dumps(body))
        private = serialization.load_pem_private_key(
            Path("/tmp/disposable-owner-key.pem").read_bytes(), password=None
        )
        approval = sign(private, body["request"], b"jarvis-development-integration-v1\n")
        published = client.post(
            "/api/autonomy/development/publish",
            headers={"Authorization": "Bearer disposable-gateway-session"},
            json=approval,
        )
        assert published.status_code == 200, published.text
        refreshed = client.get(
            "/api/autonomy/development/proposal/" + os.environ["JARVIS_FIXTURE_TASK_ID"],
            headers={"Authorization": "Bearer disposable-gateway-session"},
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["request"] is None
        assert refreshed.json()["handoff"]["status"] == "PR_REVIEW_REQUIRED"
        assert refreshed.json()["handoff"]["number"] == published.json()["result"]["number"]
        body = original
        print(json.dumps(body))


def integration_service():
    assert os.getuid() == os.geteuid() == 700
    for protected in (CONTROLLER_DATABASE, Path("/tmp/disposable-owner-key.pem")):
        try:
            protected.read_bytes()
        except PermissionError:
            pass
        else:
            raise AssertionError("integration identity obtained protected state")
    sys.path.append("/opt/jarvis-source")
    import importlib.util

    specification = importlib.util.spec_from_file_location(
        "fixture_github", "/opt/jarvis-source/tools/tests/test_supervised_development_handoff.py"
    )
    fixture_module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(fixture_module)
    GitHubFixture = fixture_module.GitHubFixture
    from tools import supervised_development_integration as integration
    from tools import supervised_development_integration_service as service

    original_fetch = service.fetch_proposal
    latest = {}

    def fetch(task_id):
        result = original_fetch(task_id)
        latest["proposal"] = result
        return result

    service.fetch_proposal = fetch
    integration._GitHub = lambda: GitHubFixture(latest["proposal"])
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(service.SOCKET)
    Path(service.SOCKET).chmod(0o666)
    listener.listen(10)
    os.dup2(listener.fileno(), 3)
    os.environ["LISTEN_PID"], os.environ["LISTEN_FDS"] = str(os.getpid()), "1"
    service.main()


def orchestrate():
    sha, governor_digest = create_fixture()
    os.environ["JARVIS_SUPERVISED_MAX_CALLS"] = "2"
    services = [
        activated("development-admission", SOCKETS / "development-admission.sock"),
        activated("development-publication", SOCKETS / "development-task.sock"),
        activated("fixture-operator", SOCKETS / "operator.sock"),
        run_child("fixture-integration", uid=700, wait=False),
    ]
    server, provider = provider_server()
    environment = {
        **os.environ,
        "OPENAI_API_KEY": "disposable-not-a-real-key",
        "JARVIS_GPT53_INPUT_USD_PER_MILLION": "1",
        "JARVIS_GPT53_OUTPUT_USD_PER_MILLION": "1",
        "JARVIS_SUPERVISED_MAX_CALLS": "2",
    }
    try:
        time.sleep(0.5)
        for process in services:
            if process.poll() is not None:
                raise AssertionError((process.args[-1], process.communicate()))
        owner_process = run_child("fixture-owner", uid=OWNER_UID, wait=False)
        deadline = time.monotonic() + 20
        while not Path("/tmp/controller-start-request").exists() and time.monotonic() < deadline:
            if owner_process.poll() is not None:
                owner_output = owner_process.communicate()
                services[2].terminate()
                operator_output = services[2].communicate(timeout=10)
                raise AssertionError((owner_output, operator_output))
            time.sleep(0.05)
        assert Path("/tmp/controller-start-request").exists()
        Path("/tmp/controller-process-active").touch()
        controller = run_child("development-controller", env=environment, wait=False)
        owner_out, owner_err = owner_process.communicate(timeout=30)
        assert owner_process.returncode == 0, owner_err
        owner = json.loads(owner_out)
        # Actual root client is denied even after genuine activation.
        root_denial = exchange(SOCKETS / "development-admission.sock", owner["admission"])
        assert root_denial["error"] == "development_admission_peer"
        if os.environ.get("JARVIS_FIXTURE_OUTCOME") in {"terminated", "guardian-stopped"}:
            assert provider.transmitted.wait(15)
            if os.environ.get("JARVIS_FIXTURE_OUTCOME") == "terminated":
                controller.terminate()
            else:
                # Actual protected operator STOP route. Fixture callback changes
                # Guardian state but deliberately does not kill the worker: the
                # coordinator's independent in-flight fence must observe STOP,
                # terminate the child and preserve its uncertain call record.
                stopped = exchange(SOCKETS / "operator.sock", {"emergency_stop": True})
                assert stopped["ok"] is True and stopped["result"]["state"] == "STOPPED"
        stdout, stderr = controller.communicate(timeout=140)
        assert Guardian().status()["state"] == "STOPPED", stderr
        assert Guardian().status()["trusted"] is True
        if os.environ.get("JARVIS_FIXTURE_OUTCOME") in {"terminated", "guardian-stopped"}:
            for directory in Path("/proc").iterdir():
                if not directory.name.isdigit():
                    continue
                try:
                    process_status = (directory / "status").read_text().splitlines()
                    uid_line = next(line for line in process_status if line.startswith("Uid:"))
                except (OSError, StopIteration):
                    continue
                if int(uid_line.split()[1]) == WORKER_UID:
                    summary = [
                        line
                        for line in process_status
                        if line.startswith(("Name:", "State:", "PPid:", "NSpgid:"))
                    ]
                    raise AssertionError(
                        ("terminated worker remains present", directory.name, summary)
                    )
        reader = SupervisedDevelopmentController(CONTROLLER_DATABASE, read_only=True)
        result = reader.snapshot(owner["task_id"])
        calls = [dict(row) for row in reader.db.execute("SELECT * FROM calls")]
        reader.close()
        with sqlite3.connect(
            "file:" + str(BASE / "development-admission/operations.sqlite3") + "?mode=ro",
            uri=True,
        ) as admission_ledger:
            admissions = admission_ledger.execute(
                "SELECT status,receipt FROM development_admissions"
            ).fetchall()
            assert len(admissions) == 1 and admissions[0][0] == "admitted"
            audits = [
                json.loads(row[0]) for row in admission_ledger.execute("SELECT entry FROM audit")
            ]
            assert sum(item.get("operation") == "admit_development" for item in audits) == 1
            assert any(item.get("actor_uid") == WORKER_UID for item in audits), audits
        calls_before_restart = provider.calls
        assert hashlib.sha256(GOVERNOR_DATABASE.read_bytes()).hexdigest() == governor_digest
        assert (
            subprocess.check_output(
                ["git", "-C", str(CANONICAL), "rev-parse", "HEAD"], text=True
            ).strip()
            == sha
        )
        assert not subprocess.check_output(["git", "-C", str(CANONICAL), "status", "--porcelain"])
        restart = {"status": "not_terminal"}
        if result["state"] in {
            "REVIEW_REQUIRED",
            "FAILED",
            "RECONCILIATION_REQUIRED",
            "RUNNING",
            "REVIEW_PENDING",
        }:
            restart = run_child("development-controller", env=environment)
            assert restart["status"] in {
                "no_eligible_candidate",
                "owner_preparation_required",
                "RECONCILIATION_REQUIRED",
                "reconciliation_required",
            }, restart
            reader = SupervisedDevelopmentController(CONTROLLER_DATABASE, read_only=True)
            result = reader.snapshot(owner["task_id"])
            reader.close()
        assert provider.calls == calls_before_restart
        installed_policy()
        error_file = Path("/tmp/fixture-worker-error")
        worker_interpreter = subprocess.check_output(
            [
                "/usr/bin/python3",
                "-I",
                "-B",
                str(ENTRY.parent / "tools/supervised_development_environment.py"),
                "--verify",
            ],
            text=True,
            timeout=30,
        ).strip()
        output = {
            "state": result["state"],
            "provider_requests": provider.calls,
            "calls": calls,
            "evidence": result.get("evidence"),
            "guardian": Guardian().status(),
            "gateway_uid": owner["gateway_uid"],
            "worker_uid": WORKER_UID,
            "worker_interpreter": worker_interpreter,
            "coordinator_interpreter": "/usr/bin/python3",
            "controller_returncode": controller.returncode,
            "controller_output": stdout.strip(),
            "stderr": stderr[-1500:],
            "worker_error": error_file.read_text()[-3000:] if error_file.exists() else None,
            "worker_stderr": Path("/tmp/fixture-worker-stderr").read_text()[-3000:]
            if Path("/tmp/fixture-worker-stderr").exists()
            else None,
            "restart": restart,
            "proposal_view": run_child(
                "fixture-proposal",
                uid=OWNER_UID,
                env={**os.environ, "JARVIS_FIXTURE_TASK_ID": owner["task_id"]},
            )
            if result["state"] == "REVIEW_REQUIRED"
            else None,
        }
        print(json.dumps(output))
    finally:
        provider.release.set()
        server.shutdown()
        for process in services:
            if process.args[-1] == "fixture-integration" and process.poll() is None:
                subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-c",
                        "import os,signal,sys;os.kill(int(sys.argv[1]),signal.SIGTERM)",
                        str(process.pid),
                    ],
                    preexec_fn=child_identity(700),
                    check=True,
                )
            else:
                process.terminate()
            process.communicate(timeout=10)


def entry():
    role = sys.argv[1]
    if role == "fixture-run":
        orchestrate()
        return 0
    if role == "fixture-owner":
        owner_flow()
        return 0
    if role == "fixture-proposal":
        proposal_flow()
        return 0
    if role == "fixture-integration":
        integration_service()
        return 0
    listener = os.getenv("FIXTURE_LISTENER_FD")
    if listener:
        os.dup2(int(listener), 3)
        os.environ["LISTEN_PID"] = str(os.getpid())
        os.environ["LISTEN_FDS"] = "1"
    if role == "fixture-operator":
        operator_service()
        return 0
    if role == "development-worker":
        sys.stderr = Path("/tmp/fixture-worker-stderr").open("w")
        import openai

        real_client = openai.OpenAI

        def loopback_client(**kwargs):
            return real_client(base_url=f"http://127.0.0.1:{PROVIDER_PORT}/v1", **kwargs)

        openai.OpenAI = loopback_client

    def trace(frame, event, argument):
        if event == "exception" and frame.f_code.co_filename.endswith("autonomy_broker_runtime.py"):
            print(
                "fixture_exception",
                frame.f_code.co_name,
                argument[0].__name__,
                str(argument[1]),
                file=sys.stderr,
            )
        if (
            role == "development-worker"
            and event == "exception"
            and argument[0].__name__ in {"LifecycleError", "OperationalError", "PermissionError"}
        ):
            with Path("/tmp/fixture-worker-error").open("a") as stream:
                stream.write(
                    frame.f_code.co_name
                    + ": "
                    + argument[0].__name__
                    + " "
                    + str(argument[1])
                    + "\n"
                )
                if frame.f_code.co_name == "_sandbox_preflight":
                    stream.write(str(frame.f_locals.get("_", "no-result")) + "\n")
        return trace

    sys.settrace(trace)
    try:
        return broker_main(sys.argv[1:])
    except BaseException:
        if role == "development-worker":
            import traceback

            Path("/tmp/fixture-worker-error").write_text(traceback.format_exc())
        raise


if __name__ == "__main__":
    raise SystemExit(entry())
