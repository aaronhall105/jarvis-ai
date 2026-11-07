"""Owner-admitted development vertical slice.

This module joins the durable lifecycle controller to the already certified
development worker.  Execution admission is *only* obtained by the controller
from the fixed protected-broker socket; no task, worker, or caller can provide a
verifier.  Provider execution remains disabled until that broker endpoint is
installed and owner admission is authenticated there.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import time
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
    guardian_generation: str = ""
    expires_at: int = 0


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
        guardian_generation: str = "",
        expires_at: int = 0,
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
            guardian_generation=guardian_generation,
            expires_at=expires_at,
        )


class ExistingSelfDevelopmentWorker:
    """Compatibility adapter that routes supervised work to the bounded worker.

    The historical repair-loop worker is deliberately not used here: it can
    plan, retry, review and integrate through legacy paths.  Supervised tasks
    must provide an immutable authoritative workspace and use the one-call
    worker below.
    """

    def __init__(self, config: Any, env_values: dict[str, str]) -> None:
        self.config = config
        self.env_values = dict(env_values)

    def run(self, task: GovernorTask) -> dict[str, Any]:
        from tools.supervised_development_worker import BoundedDevelopmentWorker

        source = self.env_values.get("JARVIS_AUTHORITATIVE_WORKSPACE", "")
        worktrees = self.env_values.get("JARVIS_SUPERVISED_WORKTREE_ROOT", "")
        if not source or not worktrees:
            raise LifecycleError("bounded_worker_workspace_unconfigured")
        try:
            input_price = float(self.env_values["JARVIS_GPT53_INPUT_USD_PER_MILLION"])
            output_price = float(self.env_values["JARVIS_GPT53_OUTPUT_USD_PER_MILLION"])
        except (KeyError, ValueError) as exc:
            raise LifecycleError("provider_pricing_unverified") from exc
        worker = BoundedDevelopmentWorker(
            Path(source),
            worktree_root=Path(worktrees),
            input_price_per_million=input_price,
            output_price_per_million=output_price,
        )
        return worker.run(task)


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
        if task.candidate_id is not None:
            payload["candidate_id"] = task.candidate_id
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
            candidate_id=task.candidate_id,
        )
        self.controller.record_approval_request(
            task_id,
            owner_id=task.owner_id,
            parameters_sha256=self._parameters_hash(task),
            request_ref=task.request_ref,
            guardian_generation=task.guardian_generation,
            expires_at=task.expires_at or None,
        )
        return task_id

    def execute(self, task_id: str, task: GovernorTask, *, current_base_sha: str) -> dict[str, Any]:
        """Run exactly one admitted worker call and stop at a review handoff."""
        started = time.monotonic()
        self.controller.start(task_id, current_base_sha=current_base_sha)
        call_id = str(uuid.uuid4())
        if not self.controller.reserve_provider_call(task_id, call_id=call_id):
            raise LifecycleError("provider_call_already_reserved")
        try:
            evidence = self.worker.run(task)
            if time.monotonic() - started > task.deadline_seconds:
                raise LifecycleError("whole_cycle_deadline_expired")
            self.controller.finish_provider_call(task_id, call_id=call_id, outcome="completed")
            self.controller.record_evidence(task_id, evidence=evidence)
            if time.monotonic() - started > task.deadline_seconds:
                raise LifecycleError("whole_cycle_deadline_expired")
            review = self.reviewer.review(task, evidence)
            if time.monotonic() - started > task.deadline_seconds:
                raise LifecycleError("whole_cycle_deadline_expired")
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
