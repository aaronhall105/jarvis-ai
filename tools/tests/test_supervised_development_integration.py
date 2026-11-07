from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import subprocess
import threading
import uuid
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

import tools.supervised_development_controller as controller_module
from tools.supervised_development_broker import DOMAIN, DevelopmentAdmissionBroker
from tools.supervised_development_controller import SupervisedDevelopmentController
from tools.supervised_development_pipeline import GovernorAdapter, SupervisedDevelopmentPipeline
from tools.supervised_development_publication import PendingTaskPublisher, fetch_pending_task
from tools.supervised_development_review import TrustedReviewRunner
from tools.supervised_development_worker import BoundedDevelopmentWorker
from tools.autonomy_broker import Ledger, canonical


def test_controller_publication_gateway_approval_worker_review_flow(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "tools/tests").mkdir(parents=True)
    (source / "tools/developer_session_diagnostics.py").write_text("VALUE = 1\n")
    (source / "tools/tests/test_developer_session_diagnostics.py").write_text(
        "from tools.developer_session_diagnostics import VALUE\n\n\ndef test_value():\n    assert VALUE == 2\n"
    )
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True
    )
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "base"], check=True)
    source_sha = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    generation = str(uuid.uuid4())
    task = GovernorAdapter.from_candidate(
        {"candidate_id": 1, "status": "queued", "summary": "Bound diagnostics warning."},
        owner_id="owner",
        request_ref="owner-request",
        source_sha=source_sha,
        allowed_paths=(
            "tools/developer_session_diagnostics.py",
            "tools/tests/test_developer_session_diagnostics.py",
        ),
        guardian_generation=generation,
    )
    controller = SupervisedDevelopmentController(tmp_path / "controller.sqlite3")
    pipeline = SupervisedDevelopmentPipeline(controller, None, None)  # type: ignore[arg-type]
    task_id = pipeline.submit(task)
    listener_path = str(tmp_path / "development-task.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(listener_path)
    listener.listen(1)
    thread = threading.Thread(
        target=lambda: _serve_once(listener, controller),
        daemon=True,
    )
    thread.start()
    import tools.supervised_development_publication as publication

    original_socket = publication.SOCKET
    publication.SOCKET = listener_path
    try:
        envelope = fetch_pending_task(listener_path)
    finally:
        publication.SOCKET = original_socket
    thread.join()
    listener.close()
    assert envelope and envelope["task_id"] == task_id

    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    approval = {key: envelope[key] for key in envelope if key != "goal"}
    approval["request_id"] = approval.pop("request_ref")
    approval["key_id"] = key_id
    approval["signature"] = base64.b64encode(
        private.sign(DOMAIN + canonical(approval), ec.ECDSA(hashes.SHA256()))
    ).decode()
    ledger = Ledger(tmp_path / "ledger.sqlite3", owner_uid=os.getuid())
    broker = DevelopmentAdmissionBroker(
        ledger,
        relay_uid=1000,
        worker_uid=1001,
        public_keys={key_id: public},
        guardian=lambda: {"state": "ACTIVE", "trusted": True, "generation": generation},
    )
    assert broker.approve(canonical(approval), 1000)["status"] == "approved"
    monkeypatch.setattr(
        controller_module,
        "_request_protected_admission",
        lambda request: broker.admit(request, 1001),
    )

    def provider(prompt, model, timeout):
        return (
            json.dumps(
                {
                    "protocol_version": 1,
                    "explanation": "Bound diagnostics warning.",
                    "edits": [
                        {
                            "path": "tools/developer_session_diagnostics.py",
                            "old_text": "VALUE = 1\n",
                            "new_text": "VALUE = 2\n",
                        }
                    ],
                }
            ),
            {"input_tokens": 100, "output_tokens": 80},
        )

    worker = BoundedDevelopmentWorker(
        source,
        worktree_root=tmp_path / "worktrees",
        provider_call=provider,
        input_price_per_million=10,
        output_price_per_million=10,
    )
    reviewer_holder = {}

    class Reviewer:
        def review(self, selected, evidence):
            reviewer = TrustedReviewRunner(
                Path(evidence["worktree"]),
                base_sha=selected.source_sha,
                allowed_paths=selected.allowed_paths,
            )
            result = reviewer.review(selected, evidence)
            reviewer_holder["result"] = result
            return result

    result = SupervisedDevelopmentPipeline(controller, worker, Reviewer()).execute(
        task_id, task, current_base_sha=source_sha
    )
    assert result["state"] == "REVIEW_REQUIRED"
    assert result["provider_calls"] == 1
    assert reviewer_holder["result"]["verified"] is True
    assert (
        subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        == source_sha
    )
    ledger.close()
    controller.close()


def _serve_once(listener, controller):
    connection, _ = listener.accept()
    with connection:
        PendingTaskPublisher(controller, gateway_uid=__import__("os").getuid()).serve_connection(
            connection
        )
