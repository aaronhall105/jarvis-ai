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
import time
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Protocol

from tools.supervised_development_controller import (
    LifecycleError,
    SupervisedDevelopmentController,
)
from tools.supervised_development_scope import editable_path


class DevelopmentWorker(Protocol):
    def run(
        self, task: "GovernorTask", *, call_ledger: "ControllerCallLedger"
    ) -> dict[str, Any]: ...


class ControllerCallLedger:
    """Durable reservation at each outbound transport, not logical worker entry."""

    def __init__(self, controller: SupervisedDevelopmentController, task_id: str):
        self.controller = controller
        self.task_id = task_id

    @property
    def deadline_monotonic(self) -> float:
        row = self.controller.db.execute(
            "SELECT started_at,parameters,state FROM tasks WHERE id=?", (self.task_id,)
        ).fetchone()
        if row is None or row["started_at"] is None or row["state"] != "RUNNING":
            raise LifecycleError("task_not_running")
        expires = row["started_at"] + json.loads(row["parameters"])["deadline_seconds"]
        return time.monotonic() + max(0, expires - time.time())

    def reserve(self, call_id: str, worst_case_usd: float) -> None:
        if not self.controller.reserve_provider_call(
            self.task_id, call_id=call_id, reserved_cost_usd=worst_case_usd
        ):
            raise LifecycleError("provider_call_already_reserved")

    def completed(self, call_id: str, actual_usd: float) -> None:
        self.controller.finish_provider_call(
            self.task_id, call_id=call_id, outcome="completed", actual_cost_usd=actual_usd
        )

    def uncertain(self, call_id: str) -> None:
        self.controller.finish_provider_call(self.task_id, call_id=call_id, outcome="uncertain")


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
        max_calls: int = 1,
    ) -> GovernorTask:
        if candidate.get("status") not in {"queued", "recorded"}:
            raise LifecycleError("governor_candidate_not_queued")
        objective = str(candidate.get("summary") or "").strip()
        if len(objective) < 3 or len(objective) > 2_000 or cls._UNSAFE.search(objective):
            raise LifecycleError("governor_objective_out_of_policy")
        if not allowed_paths or not all(editable_path(path) for path in allowed_paths):
            raise LifecycleError("governor_allow_list_invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            raise LifecycleError("governor_source_invalid")
        if not isinstance(model, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", model
        ):
            raise LifecycleError("governor_model_not_approved")
        if type(max_calls) is not int or not 1 <= max_calls <= 4:
            raise LifecycleError("governor_call_policy_invalid")
        candidate_id = candidate.get("candidate_id")
        if type(candidate_id) is not int or candidate_id <= 0:
            raise LifecycleError("governor_candidate_invalid")
        return GovernorTask(
            objective=objective,
            source_sha=source_sha,
            allowed_paths=tuple(sorted(set(allowed_paths))),
            provider="openai",
            model=model,
            max_calls=max_calls,
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
    must provide an immutable authoritative workspace and use the durably
    call-limited worker below.
    """

    def __init__(self, config: Any, env_values: dict[str, str]) -> None:
        self.config = config
        self.env_values = dict(env_values)

    def run(self, task: GovernorTask, *, call_ledger: ControllerCallLedger) -> dict[str, Any]:
        from tools.supervised_development_worker import BoundedDevelopmentWorker

        source = self.env_values.get("JARVIS_AUTHORITATIVE_WORKSPACE", "")
        worktrees = self.env_values.get("JARVIS_SUPERVISED_WORKTREE_ROOT", "")
        if not source or not worktrees:
            raise LifecycleError("bounded_worker_workspace_unconfigured")
        configured_model = self.env_values.get("JARVIS_SUPERVISED_MODEL", "gpt-5.3-codex")
        if task.model != configured_model:
            raise LifecycleError("provider_model_policy_changed")
        generic_price_keys = (
            "JARVIS_SUPERVISED_PRICING_MODEL",
            "JARVIS_SUPERVISED_INPUT_USD_PER_MILLION",
            "JARVIS_SUPERVISED_OUTPUT_USD_PER_MILLION",
        )
        if any(name in self.env_values for name in generic_price_keys):
            if self.env_values.get(generic_price_keys[0]) != task.model:
                raise LifecycleError("provider_pricing_model_mismatch")
            input_key, output_key = generic_price_keys[1:]
        elif task.model == "gpt-5.3-codex":
            # Historical configuration is bound only to its original model.
            input_key, output_key = (
                "JARVIS_GPT53_INPUT_USD_PER_MILLION",
                "JARVIS_GPT53_OUTPUT_USD_PER_MILLION",
            )
        else:
            raise LifecycleError("provider_pricing_unverified")
        try:
            input_price = float(self.env_values[input_key])
            output_price = float(self.env_values[output_key])
        except (KeyError, ValueError) as exc:
            raise LifecycleError("provider_pricing_unverified") from exc
        try:
            input_tokens = int(self.env_values.get("JARVIS_SUPERVISED_MAX_INPUT_TOKENS", "12000"))
            output_tokens = int(self.env_values.get("JARVIS_SUPERVISED_MAX_OUTPUT_TOKENS", "4000"))
        except ValueError as exc:
            raise LifecycleError("provider_token_policy_invalid") from exc
        if not 1 <= input_tokens <= 1_000_000 or not 1 <= output_tokens <= 65_536:
            raise LifecycleError("provider_token_policy_invalid")
        worker = BoundedDevelopmentWorker(
            Path(source),
            worktree_root=Path(worktrees),
            input_price_per_million=input_price,
            output_price_per_million=output_price,
            max_input_tokens=input_tokens,
            max_output_tokens=output_tokens,
        )
        return worker.run(task, call_ledger=call_ledger)


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
        snapshot = self.controller.snapshot(task_id)
        actual = hashlib.sha256(
            json.dumps(snapshot["parameters"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if actual != self._parameters_hash(task):
            raise LifecycleError("execution_task_parameters_mismatch")
        self.controller.start(task_id, current_base_sha=current_base_sha)
        ledger = ControllerCallLedger(self.controller, task_id)
        try:
            evidence = self.worker.run(task, call_ledger=ledger)
            if time.monotonic() - started > task.deadline_seconds:
                raise LifecycleError("whole_cycle_deadline_expired")
            calls = self.controller.db.execute(
                "SELECT outcome,actual_cost_usd FROM calls WHERE task_id=?", (task_id,)
            ).fetchall()
            if not calls or any(
                c["outcome"] != "completed" or c["actual_cost_usd"] is None for c in calls
            ):
                raise LifecycleError("provider_usage_unverified")
            self.controller.record_evidence(task_id, evidence=evidence)
            if time.monotonic() - started > task.deadline_seconds:
                raise LifecycleError("whole_cycle_deadline_expired")
            review = self.reviewer.review(task, evidence)
            if time.monotonic() - started > task.deadline_seconds:
                raise LifecycleError("whole_cycle_deadline_expired")
            self.controller.record_review(task_id, review=review)
        except BaseException:
            try:
                self.controller.recover_after_restart(task_id)
            except LifecycleError:
                pass
            raise
        return self.controller.snapshot(task_id)
