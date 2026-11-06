"""Owner-admitted development vertical slice.

This module joins the durable lifecycle controller to the already certified
development worker.  Execution admission is *only* obtained by the controller
from the fixed protected-broker socket; no task, worker, or caller can provide a
verifier.  Provider execution remains disabled until that broker endpoint is
installed and owner admission is authenticated there.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Protocol

from tools.supervised_development_controller import (
    LifecycleError,
    SupervisedDevelopmentController,
)


class DevelopmentWorker(Protocol):
    def run(self, task: "GovernorTask") -> dict[str, Any]: ...


class IndependentReviewer(Protocol):
    def review(self, task: "GovernorTask", evidence: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GovernorTask:
    """Immutable bounded task selected by the trusted Governor boundary."""

    objective: str
    source_sha: str
    allowed_paths: tuple[str, ...]
    provider: str
    model: str
    max_calls: int
    max_cost_usd: float
    deadline_seconds: int
    owner_id: str
    request_ref: str
    candidate_id: int | None = None


class GovernorAdapter:
    """Translate one queued improvement candidate into a bounded task."""

    _UNSAFE = re.compile(
        r"(?i)\b(?:deploy|release|publish|ota|sign(?:ing)?|credential|secret|password|guardian|resume|home assistant|android)\b"
    )

    @classmethod
    def from_candidate(
        cls,
        candidate: dict[str, Any],
        *,
        owner_id: str,
        request_ref: str,
        source_sha: str,
        allowed_paths: tuple[str, ...],
        model: str = "gpt-5.3-codex",
    ) -> GovernorTask:
        if candidate.get("status") not in {"queued", "recorded"}:
            raise LifecycleError("governor_candidate_not_queued")
        objective = str(candidate.get("summary") or "").strip()
        if len(objective) < 3 or len(objective) > 2_000 or cls._UNSAFE.search(objective):
            raise LifecycleError("governor_objective_out_of_policy")
        if not allowed_paths or any(
            not path or path.startswith("/") or ".." in Path(path).parts for path in allowed_paths
        ):
            raise LifecycleError("governor_allow_list_invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            raise LifecycleError("governor_source_invalid")
        if model != "gpt-5.3-codex":
            raise LifecycleError("governor_model_not_approved")
        candidate_id = candidate.get("candidate_id")
        if type(candidate_id) is not int or candidate_id <= 0:
            raise LifecycleError("governor_candidate_invalid")
        return GovernorTask(
            objective=objective,
            source_sha=source_sha,
            allowed_paths=tuple(sorted(set(allowed_paths))),
            provider="openai",
            model=model,
            max_calls=1,
            max_cost_usd=1.0,
            deadline_seconds=120,
            owner_id=owner_id,
            request_ref=request_ref,
            candidate_id=candidate_id,
        )


class ExistingSelfDevelopmentWorker:
    """Adapter for the certified worker; it never grants lifecycle authority."""

    def __init__(self, config: Any, env_values: dict[str, str]) -> None:
        self.config = config
        self.env_values = dict(env_values)

    def run(self, task: GovernorTask) -> dict[str, Any]:
        if task.candidate_id is None:
            raise LifecycleError("worker_candidate_missing")
        tools_dir = str(Path(__file__).resolve().parent)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        worker = importlib.import_module("self_development_worker")

        candidate = worker.base.fetch_candidate_by_id(task.candidate_id)
        if candidate is None:
            raise LifecycleError("worker_candidate_not_found")
        worker.process_development_candidate(candidate, self.config, self.env_values)
        final = worker.base.fetch_candidate_by_id(task.candidate_id)
        if not final or final.get("status") != "awaiting_approval":
            raise LifecycleError("worker_did_not_produce_reviewable_proposal")
        diff = final.get("diff_stats_json") or {}
        tests = final.get("test_results_json") or {}
        security = final.get("security_results_json") or {}
        return {
            "proposal": str(final.get("summary") or task.objective),
            "diff_sha256": str(
                final.get("validated_patch_sha256") or diff.get("patch_sha256") or ""
            ),
            "tests": tests if isinstance(tests, dict) else {},
            "security": security if isinstance(security, dict) else {},
            "changed_files": final.get("changed_files_json") or [],
            "candidate_id": task.candidate_id,
        }


class SupervisedDevelopmentPipeline:
    """Submit and execute one bounded task, stopping at REVIEW_REQUIRED."""

    def __init__(
        self,
        controller: SupervisedDevelopmentController,
        worker: DevelopmentWorker,
        reviewer: IndependentReviewer,
    ) -> None:
        self.controller = controller
        self.worker = worker
        self.reviewer = reviewer

    @staticmethod
    def _parameters_hash(task: GovernorTask) -> str:
        payload = {
            "base_sha": task.source_sha,
            "allowed_paths": sorted(set(task.allowed_paths)),
            "provider": task.provider,
            "model": task.model,
            "max_calls": task.max_calls,
            "max_cost_usd": task.max_cost_usd,
            "deadline_seconds": task.deadline_seconds,
            "goal": task.objective,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def submit(self, task: GovernorTask) -> str:
        task_id = self.controller.create_task(
            base_sha=task.source_sha,
            allowed_paths=list(task.allowed_paths),
            provider=task.provider,
            model=task.model,
            max_calls=task.max_calls,
            max_cost_usd=task.max_cost_usd,
            deadline_seconds=task.deadline_seconds,
            goal=task.objective,
        )
        self.controller.record_approval_request(
            task_id,
            owner_id=task.owner_id,
            parameters_sha256=self._parameters_hash(task),
            request_ref=task.request_ref,
        )
        return task_id

    def execute(self, task_id: str, task: GovernorTask, *, current_base_sha: str) -> dict[str, Any]:
        """Run exactly one admitted worker call and stop at a review handoff."""
        self.controller.start(task_id, current_base_sha=current_base_sha)
        call_id = str(uuid.uuid4())
        if not self.controller.reserve_provider_call(task_id, call_id=call_id):
            raise LifecycleError("provider_call_already_reserved")
        try:
            evidence = self.worker.run(task)
            self.controller.finish_provider_call(task_id, call_id=call_id, outcome="completed")
            self.controller.record_evidence(task_id, evidence=evidence)
            review = self.reviewer.review(task, evidence)
            self.controller.record_review(task_id, review=review)
        except BaseException:
            try:
                self.controller.finish_provider_call(task_id, call_id=call_id, outcome="uncertain")
            except LifecycleError:
                # Evidence/review failures can happen after the call was
                # durably completed.  Preserve the fail-closed terminal path
                # rather than leaving a task RUNNING or attempting a replay.
                try:
                    self.controller.recover_after_restart(task_id)
                except LifecycleError:
                    pass
            raise
        return self.controller.snapshot(task_id)
