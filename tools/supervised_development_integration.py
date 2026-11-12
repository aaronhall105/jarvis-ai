"""Separately authorised, draft-only GitHub handoff of a reviewed proposal.

The worker never receives this adapter's credential. Production entry points use
fixed paths and a root-provisioned integration identity; development admission,
Guardian controls and database labels confer no GitHub authority. Provisioning
and an owner UI for this separate signing domain are intentionally not implied
by installing the development controller. No merge/deploy endpoint exists.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import pwd
import re
import sqlite3
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from tools.autonomy_broker import canonical
from tools.autonomy_broker_store import Records
from tools.supervised_development_controller import (
    CONTROLLER_DATABASE,
    LifecycleError,
    REVIEW_REQUIRED,
    SupervisedDevelopmentController,
)
from tools.supervised_development_review import TrustedReviewRunner
from tools.supervised_development_scope import editable_path

REPOSITORY = "aaronhall105/jarvis-ai"
BASE_BRANCH = "jarvis/unified-production"
POLICY = Path("/etc/jarvis-development-integration/policy.json")
CREDENTIAL = Path("/run/credentials/jarvis-development-integration.service/github-token")
WORKSPACES = Path("/var/lib/jarvis-autonomy-broker/worktrees")
JOURNAL = Path("/var/lib/jarvis-development-integration")
DOMAIN = b"jarvis-development-integration-v1\n"
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_APPROVAL_FIELDS = {
    "version",
    "action",
    "key_id",
    "request_id",
    "task_id",
    "repository",
    "base_branch",
    "base_sha",
    "base_tree_sha",
    "head_tree_sha",
    "allowed_paths",
    "parameters_sha256",
    "evidence_sha256",
    "diff_sha256",
    "proposal_sha256",
    "expires_at",
    "signature",
}


def _require(condition: bool, error: str) -> None:
    if not condition:
        raise LifecycleError(error)


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _git(workspace: Path, *arguments: str) -> bytes:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        result = subprocess.run(
            [
                "/usr/bin/git",
                "--no-optional-locks",
                "-c",
                "safe.directory=" + str(workspace),
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(workspace),
                *arguments,
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LifecycleError("integration_source_unavailable") from exc
    _require(len(result.stdout) <= 8 * 1024 * 1024, "integration_artifact_too_large")
    return result.stdout


def _tree_sha(entries: dict[str, tuple[str, str]]) -> str:
    """Recompute Git's object identity without staging or committing anything."""
    root: dict[str, Any] = {}
    for path, value in entries.items():
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            _require(isinstance(node, dict), "integration_tree_path_conflict")
        _require(parts[-1] not in node, "integration_tree_path_conflict")
        node[parts[-1]] = value

    def encode(node: dict[str, Any]) -> str:
        content = b""
        for name, value in sorted(
            node.items(),
            key=lambda item: (item[0] + ("/" if isinstance(item[1], dict) else "")).encode(),
        ):
            mode, digest = ("40000", encode(value)) if isinstance(value, dict) else value
            content += mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(digest)
        return hashlib.sha1(
            b"tree " + str(len(content)).encode() + b"\0" + content, usedforsecurity=False
        ).hexdigest()

    return encode(root)


def prepare_proposal(
    database: Path, task_id: str, workspace_root: Path, *, expected_receipt_uid: int | None = None
) -> dict[str, Any]:
    """Read-only preparation, never an approval or a GitHub write capability."""
    _require(str(uuid.UUID(task_id)) == task_id, "integration_task_invalid")
    controller = SupervisedDevelopmentController(database, read_only=True)
    try:
        snapshot = controller.snapshot(task_id)
    finally:
        controller.close()
    _require(snapshot["state"] == REVIEW_REQUIRED, "integration_review_required")
    parameters, evidence = snapshot["parameters"], snapshot["evidence"]
    _require(
        isinstance(parameters.get("allowed_paths"), list)
        and bool(parameters["allowed_paths"])
        and all(editable_path(path) for path in parameters["allowed_paths"]),
        "integration_scope_untrusted",
    )
    _require(isinstance(evidence, dict), "integration_evidence_missing")
    workspace = Path(evidence.get("worktree", ""))
    root = workspace_root.resolve(strict=True)
    _require(
        workspace.is_absolute()
        and not workspace.is_symlink()
        and workspace.resolve(strict=True).is_relative_to(root)
        and workspace.resolve() != root,
        "integration_workspace_untrusted",
    )
    _require(
        not any(parent.is_symlink() for parent in workspace.parents),
        "integration_workspace_untrusted",
    )
    base = parameters["base_sha"]
    _require(isinstance(base, str) and bool(_SHA.fullmatch(base)), "integration_source_invalid")
    review = TrustedReviewRunner(
        workspace,
        base_sha=base,
        allowed_paths=tuple(parameters["allowed_paths"]),
        expected_receipt_uid=expected_receipt_uid,
    )
    checked = review.review(None, evidence)
    recorded = evidence.get("independent_review", {})
    _require(
        recorded.get("verified") is True
        and recorded.get("diff_sha256") == checked["diff_sha256"]
        and sorted(recorded.get("changed_files", [])) == sorted(checked["changed_files"]),
        "integration_review_mismatch",
    )
    diff = _git(workspace, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--")
    _require(hashlib.sha256(diff).hexdigest() == checked["diff_sha256"], "integration_diff_changed")
    files = []
    base_entries = {}
    for raw in _git(workspace, "ls-tree", "-r", "-z", base).split(b"\0"):
        if raw:
            metadata, raw_name = raw.split(b"\t", 1)
            mode, _kind, sha = metadata.decode("ascii").split(" ")
            base_entries[raw_name.decode("utf-8")] = (mode, sha)
    base_tree = _git(workspace, "rev-parse", base + "^{tree}").decode().strip()
    _require(_tree_sha(base_entries) == base_tree, "integration_base_tree_unverifiable")
    final_entries = dict(base_entries)
    for name in sorted(checked["changed_files"]):
        path = Path(name)
        _require(
            editable_path(name)
            and not path.is_absolute()
            and path.as_posix() == name
            and not any(part in {"", ".", ".."} for part in name.split("/"))
            and "\\" not in name
            and not any(ord(c) < 32 for c in name),
            "integration_path_invalid",
        )
        target = workspace / name
        _require(
            not target.is_symlink() and target.resolve().is_relative_to(workspace.resolve()),
            "integration_path_invalid",
        )
        old = _git(workspace, "ls-tree", base, "--", name).decode().strip()
        old_mode = old.split(" ")[0] if old else "100644"
        _require(old_mode in {"100644", "100755"}, "integration_path_mode_invalid")
        data = target.read_bytes() if target.is_file() else None
        if data is not None:
            info = target.stat()
            _require(
                info.st_nlink == 1 and not info.st_mode & 0o7000, "integration_path_mode_invalid"
            )
            mode = "100755" if info.st_mode & 0o111 else "100644"
        else:
            mode = old_mode
        _require(data is not None or bool(old), "integration_missing_file")
        files.append(
            {
                "path": name,
                "mode": mode,
                "content": base64.b64encode(data).decode() if data is not None else None,
                "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
            }
        )
        if data is None:
            final_entries.pop(name)
        else:
            blob = hashlib.sha1(
                b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
            ).hexdigest()
            final_entries[name] = (mode, blob)
    _require(
        _git(workspace, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--") == diff,
        "integration_diff_changed",
    )
    proposal = {
        "task_id": task_id,
        "repository": REPOSITORY,
        "base_branch": BASE_BRANCH,
        "base_sha": base,
        "base_tree_sha": base_tree,
        "head_tree_sha": _tree_sha(final_entries),
        "allowed_paths": parameters["allowed_paths"],
        "parameters_sha256": _digest(parameters),
        "evidence_sha256": _digest(evidence),
        "diff_sha256": checked["diff_sha256"],
        "files": files,
    }
    _require(len(canonical(proposal)) <= 8 * 1024 * 1024, "integration_artifact_too_large")
    return {**proposal, "proposal_sha256": _digest(proposal)}


def approval_request(proposal: dict[str, Any], *, key_id: str, expires_at: int) -> dict[str, Any]:
    """Prepare an UNSIGNED request for separate explicit owner review."""
    _require(bool(_DIGEST.fullmatch(key_id)), "integration_key_invalid")
    _require(
        type(expires_at) is int and time.time() < expires_at <= time.time() + 900,
        "integration_expiry_invalid",
    )
    return {
        "version": 1,
        "action": "create_development_pr",
        "key_id": key_id,
        "request_id": str(uuid.uuid4()),
        "expires_at": expires_at,
        **{key: value for key, value in proposal.items() if key != "files"},
    }


def _verify_approval(
    approval: dict[str, Any], proposal: dict[str, Any], policy: dict[str, Any]
) -> None:
    _require(
        proposal.get("proposal_sha256")
        == _digest({key: value for key, value in proposal.items() if key != "proposal_sha256"}),
        "integration_proposal_changed",
    )
    _require(
        set(approval) == _APPROVAL_FIELDS
        and type(approval["version"]) is int
        and approval["version"] == 1
        and approval["action"] == "create_development_pr",
        "integration_approval_schema",
    )
    _require(
        type(approval["expires_at"]) is int
        and time.time() < approval["expires_at"] <= time.time() + 900,
        "integration_approval_expired",
    )
    for key, value in proposal.items():
        if key != "files":
            _require(approval.get(key) == value, "integration_approval_mismatch")
    try:
        _require(
            str(uuid.UUID(approval["request_id"])) == approval["request_id"],
            "integration_request_invalid",
        )
        encoded = base64.b64decode(policy["owner_public_keys"][approval["key_id"]], validate=True)
        _require(
            hashlib.sha256(encoded).hexdigest() == approval["key_id"], "integration_key_integrity"
        )
        verifier = serialization.load_der_public_key(encoded)
        if not isinstance(verifier, ec.EllipticCurvePublicKey) or not isinstance(
            verifier.curve, ec.SECP256R1
        ):
            raise LifecycleError("integration_key_type")
        signature = base64.b64decode(approval["signature"], validate=True)
        verifier.verify(
            signature,
            DOMAIN + canonical({k: v for k, v in approval.items() if k != "signature"}),
            ec.ECDSA(hashes.SHA256()),
        )
    except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
        raise LifecycleError("integration_signature_invalid") from exc


def _protected_json(path: Path) -> dict[str, Any]:
    for parent in path.parents:
        info = parent.lstat()
        _require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "integration_policy_untrusted",
        )
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        _require(
            stat.S_ISREG(info.st_mode)
            and info.st_uid == 0
            and info.st_nlink == 1
            and not info.st_mode & 0o022,
            "integration_policy_untrusted",
        )
        data = handle.read(65537)
    _require(len(data) <= 65536, "integration_policy_untrusted")
    return json.loads(data)


def _policy() -> dict[str, Any]:
    try:
        policy = _protected_json(POLICY)
        worker_uid = pwd.getpwnam("jarvis-autonomy").pw_uid
        _require(
            set(policy)
            == {
                "version",
                "repository",
                "base_branch",
                "integration_uid",
                "github_actor_id",
                "owner_public_keys",
            }
            and policy["version"] == 1
            and policy["repository"] == REPOSITORY
            and policy["base_branch"] == BASE_BRANCH
            and type(policy["github_actor_id"]) is int
            and policy["github_actor_id"] > 0
            and type(policy["integration_uid"]) is int
            and os.getuid() == os.geteuid() == policy["integration_uid"]
            and os.getuid() not in {0, worker_uid}
            and isinstance(policy["owner_public_keys"], dict)
            and bool(policy["owner_public_keys"]),
            "integration_identity_denied",
        )
        return policy
    except (OSError, ValueError, KeyError) as exc:
        raise LifecycleError("integration_authority_unprovisioned") from exc


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any) -> None:
        raise LifecycleError("integration_github_redirect_denied")


class _GitHub:
    """Fixed repository REST transport; no retries and no merge/deploy route."""

    def __init__(self) -> None:
        fd = os.open(CREDENTIAL, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "r") as handle:
            info = os.fstat(handle.fileno())
            _require(
                stat.S_ISREG(info.st_mode)
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) == 0o400
                and info.st_nlink == 1,
                "integration_credential_untrusted",
            )
            self._credential = handle.read(4097).strip()
        _require(
            0 < len(self._credential) <= 4096 and "\n" not in self._credential,
            "integration_credential_invalid",
        )
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def call(self, method: str, route: str, body: dict[str, Any] | None = None) -> Any:
        _require(method in {"GET", "POST"}, "integration_operation_denied")
        if method == "POST":
            _require(
                route in {"git/blobs", "git/trees", "git/commits", "git/refs", "pulls"},
                "integration_operation_denied",
            )
        else:
            _require(
                route == "user"
                or route.startswith(("git/ref/heads/", "git/commits/", "pulls?", "pulls/")),
                "integration_operation_denied",
            )
        path = "user" if route == "user" else f"repos/{REPOSITORY}/{route}"
        request = urllib.request.Request(
            "https://api.github.com/" + path,
            data=canonical(body) if body is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self._credential,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(8 * 1024 * 1024 + 1)
            _require(len(data) <= 8 * 1024 * 1024, "integration_response_limit")
            return json.loads(data)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise LifecycleError(
                "integration_external_effect_uncertain"
                if method == "POST"
                else "integration_github_unavailable"
            ) from exc


def _publish(
    db: sqlite3.Connection,
    proposal: dict[str, Any],
    approval: dict[str, Any],
    policy: dict[str, Any],
    github: Any,
    records: Records,
) -> dict[str, Any]:
    """Internal transaction engine; production authority is fixed by publish()."""
    _verify_approval(approval, proposal, policy)
    _require(
        records.read("handoff", proposal["task_id"]) is None
        and records.read("results", approval["request_id"]) is None,
        "integration_replay_requires_reconciliation",
    )
    actor = github.call("GET", "user")
    _require(actor.get("id") == policy["github_actor_id"], "integration_github_identity_mismatch")
    source = github.call("GET", "git/ref/heads/" + BASE_BRANCH)
    _require(
        source.get("object", {}).get("sha") == proposal["base_sha"], "integration_source_drift"
    )
    branch = "jarvis/proposal/" + proposal["task_id"]
    existing = github.call("GET", "pulls?state=all&head=aaronhall105:" + branch + "&per_page=100")
    _require(existing == [], "integration_existing_pr_requires_reconciliation")
    db.execute("""CREATE TABLE IF NOT EXISTS integration_handoffs (
        task_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
        proposal_sha256 TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
        receipt TEXT, FOREIGN KEY(task_id) REFERENCES tasks(id))""")
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            "SELECT state,parameters,evidence FROM tasks WHERE id=?", (proposal["task_id"],)
        ).fetchone()
        _require(
            row is not None
            and row[0] == REVIEW_REQUIRED
            and _digest(json.loads(row[1])) == proposal["parameters_sha256"]
            and _digest(json.loads(row[2])) == proposal["evidence_sha256"],
            "integration_task_changed",
        )
        prior = db.execute(
            "SELECT status FROM integration_handoffs WHERE task_id=? OR request_id=?",
            (proposal["task_id"], approval["request_id"]),
        ).fetchone()
        _require(prior is None, "integration_replay_requires_reconciliation")
        # This journal is owned by the separate integration identity, not the
        # worker. A forged/deleted task-DB row cannot make the nonce reusable.
        records.write(
            "results",
            approval["request_id"],
            {
                "task_id": proposal["task_id"],
                "proposal_sha256": proposal["proposal_sha256"],
                "status": "RESERVED",
            },
            new=True,
        )
        records.write(
            "handoff",
            proposal["task_id"],
            {
                "request_id": approval["request_id"],
                "proposal_sha256": proposal["proposal_sha256"],
                "status": "DISPATCHING",
                "phase": "reserved",
            },
            new=True,
        )
        db.execute(
            "INSERT INTO integration_handoffs VALUES(?,?,?,'DISPATCHING','reserved',NULL)",
            (proposal["task_id"], approval["request_id"], proposal["proposal_sha256"]),
        )
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise

    def effect(route: str, body: dict[str, Any]) -> Any:
        # Revalidate before each external write, not merely before a possibly
        # long sequence of uploads. The approved capsule is held in memory;
        # expiry or a changed task record makes the remaining steps forbidden.
        _verify_approval(approval, proposal, policy)
        current = db.execute(
            "SELECT state,parameters,evidence FROM tasks WHERE id=?", (proposal["task_id"],)
        ).fetchone()
        _require(
            current is not None
            and current[0] == REVIEW_REQUIRED
            and _digest(json.loads(current[1])) == proposal["parameters_sha256"]
            and _digest(json.loads(current[2])) == proposal["evidence_sha256"],
            "integration_task_changed",
        )
        records.write(
            "handoff",
            proposal["task_id"],
            {
                "request_id": approval["request_id"],
                "proposal_sha256": proposal["proposal_sha256"],
                "status": "DISPATCHING",
                "phase": route,
            },
        )
        db.execute(
            "UPDATE integration_handoffs SET phase=? WHERE task_id=?", (route, proposal["task_id"])
        )
        return github.call("POST", route, body)

    try:
        entries = []
        for file in proposal["files"]:
            sha = None
            if file["content"] is not None:
                data = base64.b64decode(file["content"], validate=True)
                _require(
                    hashlib.sha256(data).hexdigest() == file["sha256"],
                    "integration_content_changed",
                )
                blob = effect("git/blobs", {"encoding": "base64", "content": file["content"]})
                sha = hashlib.sha1(
                    b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False
                ).hexdigest()
                _require(blob.get("sha") == sha, "integration_blob_mismatch")
            entries.append({"path": file["path"], "mode": file["mode"], "type": "blob", "sha": sha})
        base = github.call("GET", "git/commits/" + proposal["base_sha"])
        _require(
            base.get("sha") == proposal["base_sha"]
            and base.get("tree", {}).get("sha") == proposal["base_tree_sha"],
            "integration_base_tree_mismatch",
        )
        tree = effect("git/trees", {"base_tree": base["tree"]["sha"], "tree": entries})
        _require(tree.get("sha") == proposal["head_tree_sha"], "integration_tree_mismatch")
        commit = effect(
            "git/commits",
            {
                "message": "Supervised development proposal " + proposal["task_id"],
                "tree": tree["sha"],
                "parents": [proposal["base_sha"]],
            },
        )
        head = commit.get("sha", "")
        _require(
            bool(_SHA.fullmatch(head))
            and commit.get("tree", {}).get("sha") == tree["sha"]
            and [parent["sha"] for parent in commit.get("parents", [])] == [proposal["base_sha"]],
            "integration_commit_mismatch",
        )
        latest = github.call("GET", "git/ref/heads/" + BASE_BRANCH)
        _require(
            latest.get("object", {}).get("sha") == proposal["base_sha"], "integration_source_drift"
        )
        ref = effect("git/refs", {"ref": "refs/heads/" + branch, "sha": head})
        _require(
            ref.get("ref") == "refs/heads/" + branch and ref.get("object", {}).get("sha") == head,
            "integration_branch_mismatch",
        )
        body = "\n".join(
            [
                "Owner-authorised supervised proposal; NOT merge or deployment approval.",
                "Independent human review and exact-head protected CI/CodeQL remain required.",
                *[
                    f"{key}: {proposal[key]}"
                    for key in (
                        "task_id",
                        "base_sha",
                        "diff_sha256",
                        "evidence_sha256",
                        "proposal_sha256",
                    )
                ],
            ]
        )
        pr = effect(
            "pulls",
            {
                "title": "Supervised proposal " + proposal["task_id"],
                "head": branch,
                "base": BASE_BRANCH,
                "body": body,
                "draft": True,
            },
        )
        _require(
            pr.get("draft") is True
            and pr.get("state") == "open"
            and pr.get("head", {}).get("sha") == head
            and pr.get("base", {}).get("sha") == proposal["base_sha"]
            and pr.get("base", {}).get("ref") == BASE_BRANCH
            and type(pr.get("number")) is int,
            "integration_pr_mismatch",
        )
        receipt = {
            "status": "PR_REVIEW_REQUIRED",
            "number": pr["number"],
            "head_sha": head,
            "base_sha": proposal["base_sha"],
            "proposal_sha256": proposal["proposal_sha256"],
            "required_gates": [
                "independent_human_review",
                "protected_ci",
                "codeql",
                "owner_merge_approval",
            ],
        }
        records.write("handoff", proposal["task_id"], receipt)
        db.execute(
            "UPDATE integration_handoffs SET status='PR_REVIEW_REQUIRED',phase='complete',receipt=? WHERE task_id=?",
            (canonical(receipt).decode(), proposal["task_id"]),
        )
        return receipt
    except BaseException:
        # A failed journal write also leaves its prior DISPATCHING reservation
        # intact. Neither state can be retried automatically after restart.
        records.write(
            "handoff",
            proposal["task_id"],
            {
                "request_id": approval["request_id"],
                "proposal_sha256": proposal["proposal_sha256"],
                "status": "RECONCILIATION_REQUIRED",
            },
        )
        db.execute(
            "UPDATE integration_handoffs SET status='RECONCILIATION_REQUIRED' WHERE task_id=?",
            (proposal["task_id"],),
        )
        raise


def publish(task_id: str, approval: dict[str, Any]) -> dict[str, Any]:
    """The only production publishing entry point; no authority/transport overrides."""
    policy = _policy()
    proposal = prepare_proposal(
        CONTROLLER_DATABASE,
        task_id,
        WORKSPACES,
        expected_receipt_uid=pwd.getpwnam("jarvis-autonomy").pw_uid,
    )
    _verify_approval(approval, proposal, policy)
    github = _GitHub()
    for parent in JOURNAL.parents:
        info = parent.lstat()
        _require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "integration_journal_untrusted",
        )
    records = Records(JOURNAL, owner=os.getuid(), check_parents=False)
    lock_fd = os.open(JOURNAL / "integration.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(lock_fd)
    _require(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600,
        "integration_lock_untrusted",
    )
    # Opening an existing DB must not initialise, chmod or replace it.
    db = sqlite3.connect(f"file:{CONTROLLER_DATABASE}?mode=rw", uri=True, isolation_level=None)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        with os.fdopen(lock_fd, "rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            return _publish(db, proposal, approval, policy, github, records)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "publish"))
    parser.add_argument("task_id")
    parser.add_argument("--approval-file", type=Path)
    parser.add_argument("--key-id")
    parser.add_argument("--expires-at", type=int)
    args = parser.parse_args()
    if args.action == "prepare":
        result = prepare_proposal(
            CONTROLLER_DATABASE,
            args.task_id,
            WORKSPACES,
            expected_receipt_uid=pwd.getpwnam("jarvis-autonomy").pw_uid,
        )
        if args.key_id is not None or args.expires_at is not None:
            _require(
                args.key_id is not None and args.expires_at is not None,
                "integration_request_parameters_missing",
            )
            result = approval_request(result, key_id=args.key_id, expires_at=args.expires_at)
        else:
            result = {key: value for key, value in result.items() if key != "files"}
            result["execution_authority"] = "none"
    else:
        _require(
            args.approval_file is not None and args.approval_file.stat().st_size <= 65536,
            "integration_signed_approval_required",
        )
        result = publish(args.task_id, json.loads(args.approval_file.read_text()))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
