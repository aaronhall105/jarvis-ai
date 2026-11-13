"""Durable owner enhancement requests and read-only, source-bound proposals.

These records are not execution approvals or claims of reproduced software defects.
Only the protected controller may create a signed-owner approval request from them.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.self_improvement import SelfImprovementEngine

MAX_SOURCE_BLOB_BYTES = 1_048_576


class DevelopmentRequestError(ValueError):
    def __init__(self, code: str, status_code: int = 400):
        super().__init__(code)
        self.status_code = status_code


class DevelopmentRequests:
    def __init__(self, engine: SelfImprovementEngine):
        self.engine = engine

    @staticmethod
    def identifier(value: str) -> str:
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise DevelopmentRequestError("invalid_request_id") from exc
        return value

    def create(self, request_id: str, request: str, actor: str) -> dict[str, Any]:
        request_id = self.identifier(request_id)
        clean = self.engine.redact_text(request).strip()
        if not 3 <= len(clean) <= 2000 or actor != "aaron":
            raise DevelopmentRequestError("owner_request_required", 403)
        digest = hashlib.sha256(request.encode()).hexdigest()
        now = self.engine._utc_now()
        with self.engine._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT * FROM improvement_development_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if prior:
                if prior["request_sha256"] != digest or prior["actor"] != actor:
                    raise DevelopmentRequestError("request_id_conflict", 409)
            else:
                cursor = connection.execute(
                    """INSERT INTO improvement_interactions
                    (created_at, conversation_id, user_key, raw_text, interpreted_text,
                     intent, success, response, understanding_json, core_version)
                    VALUES (?, ?, ?, ?, ?, 'owner_development_request', 1, ?, ?, ?)""",
                    (
                        now,
                        f"development:{request_id}",
                        actor,
                        clean,
                        clean,
                        "Owner requested a supervised enhancement; no execution authorised.",
                        self.engine._json({"owner_enhancement": True}),
                        self.engine.core_version,
                    ),
                )
                interaction_id = self.engine._required_lastrowid(cursor)
                evidence = {
                    "source": {
                        "kind": "owner_enhancement",
                        "request_id": request_id,
                        "request": clean,
                        "actor": actor,
                        "interaction_id": interaction_id,
                    },
                    "confirmed_defect": False,
                }
                cursor = connection.execute(
                    """INSERT INTO improvement_failures
                    (created_at, updated_at, last_seen_at, conversation_id, user_key,
                     source_interaction_id, signature, category, severity, summary,
                     evidence_json, explicit, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'owner_enhancement', 'low', ?, ?, 1, 'queued')""",
                    (
                        now,
                        now,
                        now,
                        f"development:{request_id}",
                        actor,
                        interaction_id,
                        hashlib.sha256(
                            f"owner-development-v1:{actor}:{request_id}".encode()
                        ).hexdigest(),
                        clean,
                        self.engine._json(evidence),
                    ),
                )
                failure_id = self.engine._required_lastrowid(cursor)
                cursor = connection.execute(
                    """INSERT INTO improvement_candidates
                    (failure_id, created_at, updated_at, status, summary)
                    VALUES (?, ?, ?, 'queued', ?)""",
                    (failure_id, now, now, clean),
                )
                candidate_id = self.engine._required_lastrowid(cursor)
                connection.execute(
                    """INSERT INTO improvement_development_requests
                    (request_id, actor, request_sha256, failure_id, candidate_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (request_id, actor, digest, failure_id, candidate_id, now),
                )
                connection.execute(
                    """INSERT INTO improvement_audit
                    (created_at, event_type, actor, failure_id, candidate_id, details_json)
                    VALUES (?, 'owner_development_requested', ?, ?, ?, ?)""",
                    (
                        now,
                        actor,
                        failure_id,
                        candidate_id,
                        self.engine._json({"request_id": request_id, "confirmed_defect": False}),
                    ),
                )
        return self.get(request_id, actor)

    def get(self, request_id: str, actor: str) -> dict[str, Any]:
        request_id = self.identifier(request_id)
        with self.engine._connection() as connection:
            row = connection.execute(
                """SELECT r.*, c.status AS candidate_status, c.development_scope_json,
                          f.evidence_json FROM improvement_development_requests r
                   JOIN improvement_candidates c ON c.candidate_id = r.candidate_id
                   JOIN improvement_failures f ON f.failure_id = r.failure_id
                   WHERE r.request_id = ? AND r.actor = ?""",
                (request_id, actor),
            ).fetchone()
        if row is None:
            raise DevelopmentRequestError("development_request_not_found", 404)
        scope = json.loads(row["development_scope_json"])
        original_evidence = json.loads(row["evidence_json"])
        status, reason = "needs_review", "source_scope_required"
        if row["candidate_status"] != "queued":
            status, reason = "blocked", "candidate_not_queued"
        elif scope:
            try:
                current_sha, inventory = self.inventory()
                if current_sha != scope.get("source_sha"):
                    raise DevelopmentRequestError("source_binding_stale")
                expected = hashlib.sha256(
                    json.dumps(
                        json.loads(row["evidence_json"]), sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest()
                if expected != scope.get("evidence_sha256"):
                    raise DevelopmentRequestError("evidence_binding_invalid")
                source = original_evidence.get("source", {})
                if (
                    scope.get("kind") != "owner_enhancement_proposal"
                    or scope.get("owner_request_id") != request_id
                    or scope.get("evidence_failure_id") != row["failure_id"]
                    or scope.get("proposed_by") != actor
                    or source.get("kind") != "owner_enhancement"
                    or source.get("request_id") != request_id
                    or source.get("actor") != actor
                    or original_evidence.get("confirmed_defect") is not False
                    or scope.get("confirmed_defect") is not False
                ):
                    raise DevelopmentRequestError("owner_request_binding_invalid")
                findings = self.verify_findings(inventory, scope.get("source_findings"))
                if scope.get("affected_files") != sorted(findings):
                    raise DevelopmentRequestError("source_scope_invalid")
                self.verify_criteria(scope.get("acceptance_criteria"))
                status, reason = "ready", "owner_signature_required"
            except DevelopmentRequestError as exc:
                status, reason = "blocked", str(exc)
        return {
            "success": True,
            "goal": original_evidence["source"]["request"],
            "request_id": request_id,
            "candidate_id": row["candidate_id"],
            "failure_id": row["failure_id"],
            "status": status,
            "reason": reason,
            "source_sha": scope.get("source_sha"),
            "affected_files": scope.get("affected_files", []),
            "evidence_sha256": scope.get("evidence_sha256"),
            "scope_kind": scope.get("kind"),
            "acceptance_criteria": scope.get("acceptance_criteria", []),
            "task_id": None,
            "approval_required": True,
            "review_screen": "autonomy",
        }

    def git(self, *args: str) -> bytes:
        try:
            return subprocess.check_output(
                [
                    "/usr/bin/git",
                    "-c",
                    "safe.directory=" + str(self.engine.development_source_root.resolve()),
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-C",
                    str(self.engine.development_source_root),
                    *args,
                ],
                stderr=subprocess.DEVNULL,
                timeout=10,
                env={
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/nonexistent",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_OPTIONAL_LOCKS": "0",
                    "LC_ALL": "C.UTF-8",
                },
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise DevelopmentRequestError("trusted_source_unavailable", 503) from exc

    def inventory(self) -> tuple[str, dict[str, str]]:
        sha = self.git("rev-parse", "HEAD").decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or self.git("status", "--porcelain").strip():
            raise DevelopmentRequestError("trusted_source_not_clean", 503)
        inventory = {}
        for entry in self.git("ls-tree", "-rz", sha).split(b"\0"):
            if not entry:
                continue
            metadata, path_bytes = entry.split(b"\t", 1)
            path = path_bytes.decode("utf-8", "strict")
            mode, kind, blob = metadata.decode().split()
            if (
                mode in {"100644", "100755"}
                and kind == "blob"
                and self.engine._normalise_development_path(path) is not None
            ):
                inventory[path] = blob
        return sha, inventory

    def inspect(self, query: str, path: str | None = None) -> dict[str, Any]:
        """Read only verified editable blobs; no arbitrary paths or host files."""
        if not isinstance(query, str) or not 3 <= len(query) <= 120:
            raise DevelopmentRequestError("source_query_required")
        sha, inventory = self.inventory()
        if path is not None and path not in inventory:
            raise DevelopmentRequestError("source_path_not_editable")
        results = []
        for name in sorted(inventory):
            if path is not None and name != path:
                continue
            try:
                blob = self.source_blob(inventory[name])
            except DevelopmentRequestError as exc:
                if str(exc) != "source_blob_not_supported":
                    raise
                continue
            content = blob.decode("utf-8")
            lines = content.splitlines()
            matches = [i for i, line in enumerate(lines) if query.casefold() in line.casefold()]
            if not matches and query.casefold() not in name.casefold():
                continue
            line = matches[0] if matches else 0
            results.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "line": line + 1,
                    "excerpt": self.engine.redact_text(
                        "\n".join(lines[max(0, line - 2) : line + 6])
                    )[:2000],
                }
            )
            if len(results) == 12:
                break
        return {"source_sha": sha, "files": results, "read_only": True}

    def source_blob(self, identity: str) -> bytes:
        """Bound whole-file evidence reads; displayed excerpts stay independently bounded."""
        if int(self.git("cat-file", "-s", identity)) > MAX_SOURCE_BLOB_BYTES:
            raise DevelopmentRequestError("source_blob_not_supported")
        blob = self.git("cat-file", "blob", identity)
        if len(blob) > MAX_SOURCE_BLOB_BYTES or b"\0" in blob:
            raise DevelopmentRequestError("source_blob_not_supported")
        try:
            blob.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise DevelopmentRequestError("source_blob_not_supported") from exc
        return blob

    @staticmethod
    def verify_criteria(criteria: Any) -> None:
        if (
            not isinstance(criteria, list)
            or not criteria
            or any(
                not isinstance(item, str) or not 10 <= len(item.strip()) <= 1000
                for item in criteria
            )
        ):
            raise DevelopmentRequestError("acceptance_criteria_required")

    def verify_findings(self, inventory: dict[str, str], source_findings: Any) -> dict[str, Any]:
        if not isinstance(source_findings, dict) or not source_findings:
            raise DevelopmentRequestError("source_findings_required")
        findings = {}
        for path, item in source_findings.items():
            if (
                path not in inventory
                or not isinstance(item, dict)
                or set(item) != {"sha256", "anchor"}
            ):
                raise DevelopmentRequestError("source_path_not_editable")
            anchor = item.get("anchor")
            if not isinstance(anchor, str) or not 10 <= len(anchor) <= 500:
                raise DevelopmentRequestError("source_anchor_required")
            blob = self.source_blob(inventory[path])
            digest = hashlib.sha256(blob).hexdigest()
            if digest != item.get("sha256") or anchor.encode() not in blob:
                raise DevelopmentRequestError("source_finding_not_verified")
            if self.engine.redact_text(anchor) != anchor:
                raise DevelopmentRequestError("source_anchor_contains_sensitive_content")
            findings[path] = {"sha256": digest, "anchor": anchor}
        return findings

    def propose(
        self,
        request_id: str,
        actor: str,
        source_sha: str,
        source_findings: dict[str, Any],
        acceptance_criteria: list[str],
    ) -> dict[str, Any]:
        request_id = self.identifier(request_id)
        self.get(request_id, actor)
        actual_sha, inventory = self.inventory()
        if source_sha != actual_sha:
            raise DevelopmentRequestError("source_binding_stale")
        self.verify_criteria(acceptance_criteria)
        findings = self.verify_findings(inventory, source_findings)
        # Read facts above, then serialize writes. Recheck the revision before
        # binding; the Governor independently verifies it again at selection.
        if self.inventory()[0] != actual_sha:
            raise DevelopmentRequestError("source_binding_stale")
        with self.engine._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT r.*, c.status, c.development_scope_json, f.evidence_json
                   FROM improvement_development_requests r
                   JOIN improvement_candidates c ON c.candidate_id = r.candidate_id
                   JOIN improvement_failures f ON f.failure_id = r.failure_id
                   WHERE request_id = ? AND actor = ?""",
                (request_id, actor),
            ).fetchone()
            if row is None or row["status"] != "queued":
                raise DevelopmentRequestError("candidate_not_queued", 409)
            scope = {
                "scope": "application",
                "kind": "owner_enhancement_proposal",
                "affected_files": sorted(findings),
                "source_sha": actual_sha,
                "evidence_sha256": hashlib.sha256(
                    json.dumps(
                        json.loads(row["evidence_json"]), sort_keys=True, separators=(",", ":")
                    ).encode()
                ).hexdigest(),
                "evidence_failure_id": row["failure_id"],
                "owner_request_id": request_id,
                "acceptance_criteria": [
                    self.engine.redact_text(value) for value in acceptance_criteria
                ],
                "source_findings": findings,
                "proposed_by": actor,
                "confirmed_defect": False,
            }
            existing = json.loads(row["development_scope_json"])
            if existing and existing != scope:
                raise DevelopmentRequestError("scope_already_bound", 409)
            if not existing:
                connection.execute(
                    "UPDATE improvement_candidates SET development_scope_json = ?, updated_at = ? WHERE candidate_id = ?",
                    (self.engine._json(scope), self.engine._utc_now(), row["candidate_id"]),
                )
                connection.execute(
                    """INSERT INTO improvement_audit
                    (created_at, event_type, actor, failure_id, candidate_id, details_json)
                    VALUES (?, 'owner_enhancement_scope_proposed', ?, ?, ?, ?)""",
                    (
                        self.engine._utc_now(),
                        actor,
                        row["failure_id"],
                        row["candidate_id"],
                        self.engine._json(
                            {
                                "request_id": request_id,
                                "source_sha": actual_sha,
                                "affected_files": sorted(findings),
                                "confirmed_defect": False,
                            }
                        ),
                    ),
                )
        return self.get(request_id, actor)
