"""Offline operator handoff contract; GitHub writes are deterministic doubles."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools.autonomy_broker import canonical
from tools.autonomy_broker_store import Records
from tools.supervised_development_controller import LifecycleError, SupervisedDevelopmentController
from tools.supervised_development_review import TrustedReviewRunner
from tools.supervised_development_worker import CHECK_NAMES, verification_receipt
import tools.supervised_development_integration as integration


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL)


@pytest.fixture
def reviewed(tmp_path):
    workspaces = tmp_path / "worktrees"
    workspace = workspaces / "reviewed"
    (workspace / "bridge/app").mkdir(parents=True)
    (workspace / "bridge/app/response.py").write_text("VALUE = 1\n")
    (workspace / "bridge/app/response_extra.py").write_text("PRESERVED = True\n")
    git(workspace, "init", "-q")
    git(
        workspace, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "add", "."
    )
    git(
        workspace,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "base",
    )
    base = git(workspace, "rev-parse", "HEAD").decode().strip()
    (workspace / "bridge/app/response.py").write_text("VALUE = 2\n")
    database = tmp_path / "controller.db"
    controller = SupervisedDevelopmentController(database)
    task_id = controller.create_task(
        base_sha=base,
        allowed_paths=["bridge/app/response.py"],
        provider="openai",
        model="gpt-5.3-codex",
        max_calls=1,
        max_cost_usd=1,
        deadline_seconds=120,
        goal="Improve the recorded response behaviour.",
    )
    evidence = {
        "proposal": "Improve response behaviour.",
        "diff_sha256": hashlib.sha256(git(workspace, "diff", "--binary", base)).hexdigest(),
        "tests": {"passed": True, "checks": [{"kind": "pytest", "returncode": 0}]},
        "security": {"passed": True, "checks": ["scope", "ruff"]},
        "changed_files": ["bridge/app/response.py"],
        "worktree": str(workspace),
    }
    # Trusted coordinator receipts are outside the generated-code workspace.
    # This fixture models a completed execution, not live verification claims.
    checks = [
        {
            "name": name,
            "returncode": 0,
            "duration_ms": 1,
            "command": [name],
            "output_sha256": hashlib.sha256(b"fixture output").hexdigest(),
        }
        for name in CHECK_NAMES
    ]
    checks[0]["test_counts"] = {"executed": 1, "failed": 0, "skipped": 0, "collected": 1}
    evidence["tests"]["checks"] = checks
    receipt = canonical(
        {
            "version": 1,
            "base_sha": base,
            "diff_sha256": evidence["diff_sha256"],
            "changed_files": evidence["changed_files"],
            "sandbox": "bubblewrap-read-only-v1",
            "checks": checks,
        }
    )
    verification_receipt(workspace).write_bytes(receipt)
    verification_receipt(workspace).chmod(0o600)
    evidence["verification_receipt_sha256"] = hashlib.sha256(receipt).hexdigest()
    evidence["independent_review"] = TrustedReviewRunner(
        workspace, base_sha=base, allowed_paths=("bridge/app/response.py",)
    ).review(None, evidence)
    # This is disposable persistence for the handoff boundary, not a fake
    # execution admission or production mutation. The execution chain has its
    # own process-level integration test.
    controller.db.execute(
        "UPDATE tasks SET state='REVIEW_REQUIRED', evidence=? WHERE id=?",
        (canonical(evidence).decode(), task_id),
    )
    controller.close()
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_id = hashlib.sha256(public).hexdigest()
    policy = {
        "owner_public_keys": {key_id: base64.b64encode(public).decode()},
        "github_actor_id": 123,
    }
    journal = tmp_path / "journal"
    journal.mkdir(mode=0o700)
    return {
        "database": database,
        "workspace": workspace,
        "root": workspaces,
        "task": task_id,
        "private": private,
        "key_id": key_id,
        "policy": policy,
        "records": Records(journal, owner=os.getuid(), check_parents=False),
    }


def request(fixture):
    proposal = integration.prepare_proposal(fixture["database"], fixture["task"], fixture["root"])
    approval = integration.approval_request(
        proposal, key_id=fixture["key_id"], expires_at=int(time.time()) + 300
    )
    approval["signature"] = base64.b64encode(
        fixture["private"].sign(integration.DOMAIN + canonical(approval), ec.ECDSA(hashes.SHA256()))
    ).decode()
    return proposal, approval


class GitHubFixture:
    def __init__(self, proposal):
        self.proposal = proposal
        self.calls = []
        self.fail_at = None
        self.mutate_at = None
        self.head = "b" * 40
        self.tree = proposal["head_tree_sha"]
        self.actor = 123
        self.source = proposal["base_sha"]
        self.prs = []

    @property
    def writes(self):
        return [(route, body) for method, route, body in self.calls if method == "POST"]

    def call(self, method, route, body=None):
        self.calls.append((method, route, body))
        if route == self.fail_at:
            raise TimeoutError("disposable transport interruption")
        if route == "user":
            return {"id": self.actor}
        if route.startswith("git/ref/heads/"):
            return {"object": {"sha": self.source}}
        if route.startswith("pulls?"):
            return self.prs
        if route.startswith("git/commits/"):
            return {
                "sha": self.proposal["base_sha"],
                "tree": {"sha": self.proposal["base_tree_sha"]},
            }
        if route == "git/blobs":
            data = base64.b64decode(body["content"])
            return {
                "sha": hashlib.sha1(
                    b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
                ).hexdigest()
            }
        if route == "git/trees":
            return {"sha": self.tree}
        if route == "git/commits":
            return {"sha": self.head, "tree": {"sha": self.tree}, "parents": [{"sha": self.source}]}
        if route == "git/refs":
            return {"ref": body["ref"], "object": {"sha": body["sha"]}}
        if route == "pulls":
            result = {
                "draft": True,
                "state": "open",
                "number": 999,
                "head": {"sha": self.head},
                "base": {"sha": self.source, "ref": integration.BASE_BRANCH},
            }
            self.prs.append(result)
            return result
        raise AssertionError((method, route))


def publish_fixture(fixture, proposal, approval, github):
    with sqlite3.connect(fixture["database"], isolation_level=None) as db:
        return integration._publish(
            db, proposal, approval, fixture["policy"], github, fixture["records"]
        )


def test_preparation_is_read_only_and_binds_real_diff(reviewed):
    before = reviewed["database"].read_bytes()
    proposal, approval = request(reviewed)
    assert reviewed["database"].read_bytes() == before
    assert proposal["diff_sha256"] == approval["diff_sha256"]
    assert proposal["allowed_paths"] == ["bridge/app/response.py"]
    assert proposal["files"][0]["content"] == base64.b64encode(b"VALUE = 2\n").decode()
    assert git(reviewed["workspace"], "rev-parse", "HEAD").decode().strip() == proposal["base_sha"]
    # Independently compare the in-memory tree calculation with actual Git;
    # this disposable test staging is not an integration action.
    git(reviewed["workspace"], "add", "bridge/app/response.py")
    assert git(reviewed["workspace"], "write-tree").decode().strip() == proposal["head_tree_sha"]


def test_owner_signed_handoff_creates_one_draft_without_merge_or_deploy(reviewed):
    proposal, approval = request(reviewed)
    github = GitHubFixture(proposal)
    result = publish_fixture(reviewed, proposal, approval, github)
    assert result["status"] == "PR_REVIEW_REQUIRED"
    assert [route for route, _ in github.writes] == [
        "git/blobs",
        "git/trees",
        "git/commits",
        "git/refs",
        "pulls",
    ]
    assert github.writes[-1][1]["draft"] is True
    assert "owner_merge_approval" in result["required_gates"]
    assert reviewed["records"].read("handoff", reviewed["task"])["head_sha"] == github.head
    assert git(reviewed["workspace"], "rev-parse", "HEAD").decode().strip() == proposal["base_sha"]


@pytest.mark.parametrize("change", ["expiry", "task_evidence"])
def test_each_external_write_rechecks_frozen_authority(reviewed, monkeypatch, change):
    proposal, approval = request(reviewed)

    class ChangedDuringUpload(GitHubFixture):
        def call(self, method, route, body=None):
            result = super().call(method, route, body)
            if route == "git/blobs":
                if change == "expiry":
                    monkeypatch.setattr(
                        integration.time, "time", lambda: approval["expires_at"] + 1
                    )
                else:
                    with sqlite3.connect(reviewed["database"]) as db:
                        db.execute("UPDATE tasks SET evidence='{}' WHERE id=?", (reviewed["task"],))
            return result

    github = ChangedDuringUpload(proposal)
    with pytest.raises(LifecycleError, match="integration_(approval_expired|task_changed)"):
        publish_fixture(reviewed, proposal, approval, github)
    assert [route for route, _ in github.writes] == ["git/blobs"]
    assert (
        reviewed["records"].read("handoff", reviewed["task"])["status"] == "RECONCILIATION_REQUIRED"
    )


@pytest.mark.parametrize(
    "change",
    [
        "unsigned",
        "unknown_key",
        "wrong_domain",
        "expired",
        "different_task",
        "altered_scope",
        "changed_digest",
    ],
)
def test_untrusted_or_stale_authority_never_dispatches(reviewed, change):
    proposal, approval = request(reviewed)
    if change == "unsigned":
        approval.pop("signature")
    elif change == "unknown_key":
        approval["key_id"] = "0" * 64
    elif change == "wrong_domain":
        signed = {k: v for k, v in approval.items() if k != "signature"}
        approval["signature"] = base64.b64encode(
            reviewed["private"].sign(
                b"jarvis-development-admission-v1\n" + canonical(signed), ec.ECDSA(hashes.SHA256())
            )
        ).decode()
    elif change == "expired":
        approval["expires_at"] = int(time.time()) - 1
    elif change == "different_task":
        approval["task_id"] = str(uuid.uuid4())
    elif change == "altered_scope":
        approval["allowed_paths"].append("bridge/app/config.py")
    else:
        approval["evidence_sha256"] = "0" * 64
    github = GitHubFixture(proposal)
    with pytest.raises(LifecycleError):
        publish_fixture(reviewed, proposal, approval, github)
    assert github.calls == []


@pytest.mark.parametrize("change", ["actor", "source", "prior_pr"])
def test_remote_preflight_denies_unexpected_authority_or_source(reviewed, change):
    proposal, approval = request(reviewed)
    github = GitHubFixture(proposal)
    if change == "actor":
        github.actor = 999
    elif change == "source":
        github.source = "f" * 40
    else:
        github.prs = [{"number": 123}]
    with pytest.raises(LifecycleError):
        publish_fixture(reviewed, proposal, approval, github)
    assert github.writes == []


@pytest.mark.parametrize(
    "failed_effect", ["git/blobs", "git/trees", "git/commits", "git/refs", "pulls"]
)
def test_uncertain_effect_survives_restart_without_replay(reviewed, failed_effect):
    proposal, approval = request(reviewed)
    github = GitHubFixture(proposal)
    github.fail_at = failed_effect
    with pytest.raises(TimeoutError):
        publish_fixture(reviewed, proposal, approval, github)
    assert (
        reviewed["records"].read("handoff", reviewed["task"])["status"] == "RECONCILIATION_REQUIRED"
    )
    writes = len(github.writes)
    github.fail_at = None
    with pytest.raises(LifecycleError, match="replay_requires_reconciliation"):
        publish_fixture(reviewed, proposal, approval, github)
    assert len(github.writes) == writes


def test_worker_database_tampering_cannot_clear_integration_nonce(reviewed):
    proposal, approval = request(reviewed)
    github = GitHubFixture(proposal)
    publish_fixture(reviewed, proposal, approval, github)
    with sqlite3.connect(reviewed["database"]) as db:
        db.execute("DELETE FROM integration_handoffs")
    github.prs = []
    before = len(github.writes)
    with pytest.raises(LifecycleError, match="replay_requires_reconciliation"):
        publish_fixture(reviewed, proposal, approval, github)
    assert len(github.writes) == before


@pytest.mark.parametrize("change", ["diff", "source", "state", "evidence"])
def test_preparation_rejects_tampered_review_or_source(reviewed, change):
    if change == "diff":
        (reviewed["workspace"] / "bridge/app/response.py").write_text("VALUE = 3\n")
    elif change == "source":
        git(
            reviewed["workspace"],
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "drift",
        )
    else:
        with sqlite3.connect(reviewed["database"]) as db:
            if change == "state":
                db.execute("UPDATE tasks SET state='RUNNING'")
            else:
                db.execute("UPDATE tasks SET evidence='{}'")
    with pytest.raises((LifecycleError, FileNotFoundError)):
        integration.prepare_proposal(reviewed["database"], reviewed["task"], reviewed["root"])


def test_production_api_has_no_caller_selected_policy_or_verifier():
    import inspect

    assert list(inspect.signature(integration.publish).parameters) == ["task_id", "approval"]
    assert integration.POLICY.is_absolute()
    with pytest.raises((LifecycleError, FileNotFoundError)):
        integration.publish(str(uuid.uuid4()), {})


def test_transport_rejects_merge_and_deployment_even_with_credential_object():
    github = object.__new__(integration._GitHub)
    for method, route in [
        ("PUT", "pulls/1/merge"),
        ("POST", "deployments"),
        ("PATCH", "git/refs/heads/main"),
        ("DELETE", "git/refs/heads/main"),
    ]:
        with pytest.raises(LifecycleError, match="operation_denied"):
            github.call(method, route, {})


def test_database_mutation_after_owner_signature_fails_before_writes(reviewed):
    proposal, approval = request(reviewed)
    with sqlite3.connect(reviewed["database"]) as db:
        evidence = json.loads(db.execute("SELECT evidence FROM tasks").fetchone()[0])
        evidence["proposal"] = "Modified after owner review"
        db.execute("UPDATE tasks SET evidence=?", (json.dumps(evidence),))
    github = GitHubFixture(proposal)
    with pytest.raises(LifecycleError, match="task_changed"):
        publish_fixture(reviewed, proposal, approval, github)
    assert github.writes == []


def test_capsule_content_cannot_be_replaced_after_signature(reviewed):
    proposal, approval = request(reviewed)
    proposal["files"][0]["content"] = base64.b64encode(b"VALUE = 999\n").decode()
    proposal["files"][0]["sha256"] = hashlib.sha256(b"VALUE = 999\n").hexdigest()
    github = GitHubFixture(proposal)
    with pytest.raises(LifecycleError, match="proposal_changed"):
        publish_fixture(reviewed, proposal, approval, github)
    assert github.calls == []


def test_remote_tree_must_match_exact_locally_verified_diff(reviewed):
    proposal, approval = request(reviewed)
    github = GitHubFixture(proposal)
    github.tree = "f" * 40
    with pytest.raises(LifecycleError, match="tree_mismatch"):
        publish_fixture(reviewed, proposal, approval, github)
    assert all(route not in {"git/commits", "git/refs", "pulls"} for route, _ in github.writes)


@pytest.mark.parametrize("identity", [0, 1001, 9999])
def test_root_worker_or_wrong_identity_cannot_publish(monkeypatch, identity):
    from types import SimpleNamespace

    policy = {
        "version": 1,
        "repository": integration.REPOSITORY,
        "base_branch": integration.BASE_BRANCH,
        "integration_uid": 1002,
        "github_actor_id": 123,
        "owner_public_keys": {"key": "public"},
    }
    monkeypatch.setattr(integration, "_protected_json", lambda _: policy)
    monkeypatch.setattr(integration.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1001))
    monkeypatch.setattr(integration.os, "getuid", lambda: identity)
    monkeypatch.setattr(integration.os, "geteuid", lambda: identity)
    with pytest.raises(LifecycleError, match="identity_denied"):
        integration.publish(str(uuid.uuid4()), {})


def test_user_owned_policy_is_not_a_trusted_verifier(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text('{"owner_public_keys":{}}')
    with pytest.raises(LifecycleError, match="policy_untrusted"):
        integration._protected_json(path)


def test_concurrent_duplicate_cannot_dispatch_second_effect(reviewed):
    import threading

    proposal, approval = request(reviewed)
    github = GitHubFixture(proposal)
    entered, release = threading.Event(), threading.Event()
    original = github.call

    def blocked(method, route, body=None):
        if route == "git/blobs":
            entered.set()
            assert release.wait(timeout=5)
        return original(method, route, body)

    github.call = blocked
    results = []
    thread = threading.Thread(
        target=lambda: results.append(publish_fixture(reviewed, proposal, approval, github))
    )
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(LifecycleError, match="replay_requires_reconciliation"):
            publish_fixture(reviewed, proposal, approval, github)
    finally:
        release.set()
        thread.join(timeout=5)
    assert len(results) == 1
    assert len([route for route, _ in github.writes if route == "pulls"]) == 1
