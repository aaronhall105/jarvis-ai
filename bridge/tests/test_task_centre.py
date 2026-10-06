from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from app.agent_planner import (
    AgentPlan,
    CapabilityAccess,
    CapabilityRequirement,
    ConfirmationStatus,
    EvidenceRequirement,
    PlanStatus,
    PlanStep,
    RiskLevel,
    StepFailure,
    StepStatus,
)
from app.dialogue_manager import DialogueManager
from app.pending_interactions import PendingInteractionKind, PendingInteractionService
from app.task_centre import TaskCentre, TaskCentreStatus


NOW = datetime.now(timezone.utc).isoformat()


class FakeFollowups:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, str]] = []

    async def list(self, *, principal_id: str, limit: int, **_: Any):
        return [row for row in self.rows if row["principal_id"] == principal_id][:limit]

    async def audit(self, job_id: str, *, principal_id: str):
        return []

    async def cancel(self, job_id: str, **_: Any):
        self.calls.append(("cancel", job_id))
        return self._status(job_id, "cancelled")

    async def pause(self, job_id: str, **_: Any):
        self.calls.append(("pause", job_id))
        return self._status(job_id, "paused")

    async def resume(self, job_id: str, **_: Any):
        self.calls.append(("resume", job_id))
        return self._status(job_id, "pending")

    async def reschedule(self, job_id: str, **_: Any):
        self.calls.append(("reschedule", job_id))
        return self._status(job_id, "pending")

    def _status(self, job_id: str, status: str):
        row = next(item for item in self.rows if item["job_id"] == job_id)
        row["status"] = status
        row["updated_at"] = NOW
        return row


class FakeExecutiveStore:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []

    async def list_tasks(self, *, principal_id: str, limit: int):
        return [row for row in self.rows if row["principal_id"] == principal_id][:limit]

    async def update_task(self, task_id: str, **values: Any):
        row = next(item for item in self.rows if item["task_id"] == task_id)
        row.update(values)
        return row


class FakePlanner:
    def __init__(self, plans: list[AgentPlan] | None = None) -> None:
        self.plans = {plan.plan_id: plan for plan in plans or []}
        self.calls: list[tuple[str, str]] = []

    async def get(self, plan_id: str):
        return self.plans.get(plan_id)

    async def list_plans(self, *, limit: int):
        return list(self.plans.values())[:limit]

    async def cancel(self, plan_id: str):
        self.calls.append(("cancel", plan_id))
        plan = self.plans[plan_id]
        plan.status = PlanStatus.CANCELLED
        return plan

    async def resume(self, plan_id: str):
        self.calls.append(("resume", plan_id))
        return self.plans[plan_id]

    async def approve(self, plan_id: str, step_id: str, *, approved: bool = True):
        self.calls.append(("approve" if approved else "decline", step_id))
        plan_value = self.plans[plan_id]
        step = plan_value.step(step_id)
        step.confirmation_status = (
            ConfirmationStatus.APPROVED if approved else ConfirmationStatus.DENIED
        )
        step.status = StepStatus.PENDING if approved else StepStatus.CANCELLED
        plan_value.status = PlanStatus.RUNNING if approved else PlanStatus.PARTIAL
        return plan_value


class FakeEmailPolicies:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, str]] = []
        self.important_only: dict[str, Any] = {
            "enabled": False,
            "recoverable_cleanup_authority": False,
            "providers": {"google_gmail": True, "microsoft_outlook": True},
            "provider_states": [],
        }

    async def important_only_status(self, *, principal_id: str):
        return {"principal_id": principal_id, **self.important_only}

    async def configure_important_only(
        self, *, enabled: bool, recoverable_cleanup_authority: bool, **_: Any
    ):
        self.calls.append(("important_only", "resume" if enabled else "pause"))
        self.important_only["enabled"] = enabled
        self.important_only["recoverable_cleanup_authority"] = recoverable_cleanup_authority
        for item in self.important_only.get("provider_states") or ():
            item["status"] = "pending" if enabled else "paused"
        return dict(self.important_only)

    async def run_important_only_once(self, **_: Any):
        self.calls.append(("important_only", "retry"))
        return {"status": "running", "ran": True}

    async def list_bulk_actions(self, *, principal_id: str, limit: int):
        return [row for row in self.rows if row["principal_id"] == principal_id][:limit]

    async def cancel_bulk_action(self, *, bulk_action_id: str, **_: Any):
        self.calls.append(("cancel", bulk_action_id))
        self._row(bulk_action_id)["status"] = "cancelled"
        return True

    async def execute_bulk_action(self, *, bulk_action_id: str, **_: Any):
        self.calls.append(("execute", bulk_action_id))
        row = self._row(bulk_action_id)
        row["status"] = "completed"
        row["succeeded_count"] = row["intended_count"]
        row["completed_at"] = NOW
        return {"success": True, **row}

    async def retry_bulk_action(self, *, bulk_action_id: str, **_: Any):
        self.calls.append(("retry", bulk_action_id))
        row = self._row(bulk_action_id)
        row["status"] = "awaiting_confirmation"
        return {"success": True, **row}

    async def bulk_action(self, *, bulk_action_id: str, **_: Any):
        return self._row(bulk_action_id)

    def _row(self, action_id: str):
        return next(item for item in self.rows if item["bulk_action_id"] == action_id)


class FakeNotifier:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def __call__(self, **values: str):
        self.messages.append(values)
        return {"success": True, "provider_reference": f"notification-{len(self.messages)}"}


def followup(job_id: str, status: str, *, principal: str = "aaron") -> dict[str, Any]:
    return {
        "job_id": job_id,
        "principal_id": principal,
        "conversation_id": f"usr:{principal}:chat-1",
        "kind": "external_monitor" if "monitor" in job_id else "scheduled",
        "payload": {"label": "Watch the parcel" if "monitor" in job_id else "Take bins out"},
        "result": {},
        "schedule": None,
        "status": status,
        "created_at": NOW,
        "updated_at": NOW,
        "next_run_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "delivered_at": NOW if status == "completed" else None,
        "capability_id": "personal.monitor",
        "notification_state": "not_requested",
    }


def bulk(
    action_id: str,
    status: str,
    *,
    principal: str = "aaron",
    provider: str = "google_gmail",
    task_group_id: str | None = None,
) -> dict[str, Any]:
    return {
        "bulk_action_id": action_id,
        "principal_id": principal,
        "conversation_id": f"usr:{principal}:chat-1",
        "provider": provider,
        "account_id": f"{provider}-account",
        "operation": "trash",
        "filter_kind": "compound",
        "status": status,
        "intended_count": 46_502,
        "attempted_count": 100 if status in {"partial", "completed"} else 0,
        "succeeded_count": 98 if status == "partial" else 46_502 if status == "completed" else 0,
        "failed_count": 2 if status == "partial" else 0,
        "skipped_count": 0,
        "halt_reason": "Microsoft Graph timeout" if status == "partial" else None,
        "created_at": NOW,
        "updated_at": NOW,
        "completed_at": NOW if status == "completed" else None,
        "task_group_id": task_group_id,
    }


def plan(plan_id: str = "plan-1", *, principal: str = "aaron") -> AgentPlan:
    steps = [
        PlanStep(
            step_id="outlook",
            title="Check Outlook",
            capability=CapabilityRequirement(
                "outlook.search", CapabilityAccess.READ, EvidenceRequirement.ACCEPTED
            ),
            arguments={},
            depends_on=(),
            risk=RiskLevel.LOW,
            required_confirmation=False,
            confirmation_status=ConfirmationStatus.NOT_REQUIRED,
            max_attempts=1,
            continuation=None,
            action_id="action-1",
            status=StepStatus.SUCCEEDED,
            completed_at=NOW,
            result={"summary": "Outlook checked"},
        ),
        PlanStep(
            step_id="gmail",
            title="Check Gmail",
            capability=CapabilityRequirement(
                "gmail.search", CapabilityAccess.READ, EvidenceRequirement.ACCEPTED
            ),
            arguments={},
            depends_on=(),
            risk=RiskLevel.LOW,
            required_confirmation=False,
            confirmation_status=ConfirmationStatus.NOT_REQUIRED,
            max_attempts=2,
            continuation=None,
            action_id="action-2",
            status=StepStatus.BLOCKED,
            failure=StepFailure("provider_unavailable", "Gmail unavailable", retryable=True),
        ),
    ]
    return AgentPlan(
        plan_id=plan_id,
        conversation_id=f"usr:{principal}:chat-1",
        goal="Check tomorrow's email and calendar",
        status=PlanStatus.BLOCKED,
        steps=steps,
        continuation=None,
        created_at=NOW,
        updated_at=NOW,
    )


def executive(task_id: str = "exec-1", *, principal: str = "aaron") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "principal_id": principal,
        "conversation_id": f"usr:{principal}:chat-1",
        "objective": "Check tomorrow's email and calendar",
        "status": "waiting_tool",
        "route": "executive",
        "model": "gpt-6-astra",
        "reasoning_effort": "medium",
        "reason_code": "multi_domain_request",
        "plan_id": "plan-1",
        "current_step": "Check Gmail",
        "waiting_reason": "Gmail unavailable",
        "last_verified_result": "Outlook checked",
        "error_summary": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def service(
    path: Path,
    *,
    followups: list[dict[str, Any]] | None = None,
    executives: list[dict[str, Any]] | None = None,
    plans: list[AgentPlan] | None = None,
    bulks: list[dict[str, Any]] | None = None,
    notifier: FakeNotifier | None = None,
    legacy_task_path: Path | None = None,
    legacy_recurring_path: Path | None = None,
) -> TaskCentre:
    return TaskCentre(
        database_path=path,
        followups=FakeFollowups(followups),
        executive_store=FakeExecutiveStore(executives),
        planner=FakePlanner(plans),
        email_policies=FakeEmailPolicies(bulks),
        notifier=notifier,
        legacy_task_path=legacy_task_path,
        legacy_recurring_path=legacy_recurring_path,
    )


@pytest.mark.asyncio
async def test_unified_projection_contains_active_scheduled_executive_email_and_history(
    tmp_path: Path,
):
    centre = service(
        tmp_path / "tasks.db",
        followups=[
            followup("scheduled-1", "pending"),
            followup("failed-1", "failed"),
            followup("done-1", "completed"),
        ],
        executives=[executive()],
        plans=[plan()],
        bulks=[bulk("cleanup-wait", "awaiting_confirmation"), bulk("cleanup-partial", "partial")],
    )
    tasks = await centre.list_tasks(principal_id="aaron", filter_name="ALL")
    assert {item["task_type"] for item in tasks} >= {"followup", "executive", "email_cleanup"}
    assert {item["status"] for item in tasks} >= {
        "SCHEDULED",
        "WAITING_FOR_JARVIS",
        "WAITING_FOR_YOU",
        "PARTIAL",
        "FAILED",
        "COMPLETED",
    }
    waiting = next(item for item in tasks if item["task_id"] == "email_bulk:cleanup-wait")
    assert waiting["progress_total"] == 46_502
    assert waiting["requires_user_action"] is True
    assert waiting["result_summary"] == "No email has been changed yet"


@pytest.mark.asyncio
async def test_important_only_is_one_generic_task_without_batch_confirmations(tmp_path: Path):
    centre = service(tmp_path / "tasks.db")
    centre.email_policies.important_only = {
        "enabled": True,
        "recoverable_cleanup_authority": True,
        "conversation_id": "usr:aaron:important-only",
        "created_at": NOW,
        "updated_at": NOW,
        "providers": {"google_gmail": True, "microsoft_outlook": True},
        "progress": {
            "total_estimate": 35_000,
            "processed_count": 12_000,
            "moved_count": 9_000,
            "kept_important_count": 2_000,
            "kept_active_count": 250,
            "temporary_count": 500,
            "uncertain_count": 500,
        },
        "provider_states": [
            {
                "provider": "google_gmail",
                "account_id": "gmail-account",
                "phase": "backlog",
                "status": "pending",
                "processed_count": 7_000,
                "moved_count": 5_000,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "provider": "microsoft_outlook",
                "account_id": "outlook-account",
                "phase": "backlog",
                "status": "pending",
                "processed_count": 5_000,
                "moved_count": 4_000,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ],
    }
    centre.email_policies.rows = [
        {
            **bulk("internal-batch", "running"),
            "filter_kind": "important_only",
        }
    ]

    tasks = await centre.list_tasks(principal_id="aaron", filter_name="ALL")
    task = next(item for item in tasks if item["task_id"] == "important_only:inbox")

    assert not any(item["task_id"] == "email_bulk:internal-batch" for item in tasks)
    assert task["status"] == "RUNNING"
    assert task["progress_current"] == 12_000
    assert task["progress_total"] == 35_000
    assert task["requires_user_action"] is False
    assert task["can_confirm"] is False
    assert task["metadata"]["permanent_delete"] is False
    assert task["metadata"]["remaining"] == 23_000
    assert "250 kept active" in task["result_summary"]
    assert task["progress"] == {
        "mode": "DETERMINATE",
        "current": 12_000,
        "total": 35_000,
        "unit": "messages",
        "fraction": 12_000 / 35_000,
        "percent": 34,
        "remaining": 23_000,
    }
    assert {item["title"] for item in task["subtasks"]} == {"Gmail", "Outlook"}
    assert next(item for item in task["metrics"] if item["key"] == "moved")["value"] == 9_000

    paused = await centre.pause(principal_id="aaron", task_id=task["task_id"], request_id="pause-1")
    assert paused is not None and paused["status"] == "PAUSED"
    resumed = await centre.resume(
        principal_id="aaron", task_id=task["task_id"], request_id="resume-1"
    )
    assert resumed is not None and resumed["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_important_only_monitoring_is_continuous_not_unfinished_backlog(
    tmp_path: Path,
) -> None:
    centre = service(tmp_path / "tasks.db")
    centre.email_policies.important_only = {
        "enabled": True,
        "recoverable_cleanup_authority": True,
        "conversation_id": "usr:aaron:important-only",
        "progress": {
            "total_estimate": 50_654,
            "processed_count": 48_513,
            "moved_count": 7_380,
        },
        "provider_states": [
            {
                "provider": "google_gmail",
                "account_id": "gmail-account",
                "phase": "monitoring",
                "status": "monitoring",
                "total_estimate": 11_363,
                "processed_count": 10_863,
                "moved_count": 6_333,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "provider": "microsoft_outlook",
                "account_id": "outlook-account",
                "phase": "monitoring",
                "status": "monitoring",
                "total_estimate": 39_291,
                "processed_count": 37_650,
                "moved_count": 1_047,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ],
    }

    task = next(
        item
        for item in await centre.list_tasks(principal_id="aaron", filter_name="ACTIVE")
        if item["task_id"] == "important_only:inbox"
    )

    assert task["status"] == "MONITORING"
    assert task["work_mode"] == "CONTINUOUS"
    assert task["phase"] == "MONITORING"
    assert task["progress"] == {
        "mode": "NONE",
        "current": None,
        "total": None,
        "unit": "messages",
        "fraction": None,
        "percent": None,
        "remaining": None,
    }
    assert task["timing"]["eta_seconds"] is None
    assert task["timing"]["eta_quality"] == "not_applicable"
    assert task["backlog"]["status"] == "COMPLETED"
    assert task["backlog"]["reviewed_count"] == 48_513
    assert task["backlog"]["moved_count"] == 7_380
    assert task["backlog"]["initial_estimate"] == 50_654
    assert "remaining" not in task["result_summary"]
    assert all(item["progress"]["mode"] == "NONE" for item in task["subtasks"])
    assert all(item["backlog"]["status"] == "COMPLETED" for item in task["subtasks"])

    centre.email_policies.important_only["progress"]["processed_count"] = 48_520
    centre.email_policies.important_only["progress"]["moved_count"] = 7_382
    centre.email_policies.important_only["provider_states"][0]["processed_count"] = 10_870
    restarted = service(tmp_path / "tasks.db")
    restarted.email_policies.important_only = centre.email_policies.important_only
    after_restart = next(
        item
        for item in await restarted.list_tasks(principal_id="aaron", filter_name="ACTIVE")
        if item["task_id"] == "important_only:inbox"
    )
    assert after_restart["backlog"]["reviewed_count"] == 48_513
    assert after_restart["backlog"]["moved_count"] == 7_380
    gmail = next(item for item in after_restart["subtasks"] if item["title"] == "Gmail")
    assert gmail["backlog"]["reviewed_count"] == 10_863


@pytest.mark.asyncio
async def test_mixed_monitoring_and_backlog_only_counts_bounded_provider(
    tmp_path: Path,
) -> None:
    centre = service(tmp_path / "tasks.db")
    centre.email_policies.important_only = {
        "enabled": True,
        "recoverable_cleanup_authority": True,
        "conversation_id": "usr:aaron:important-only",
        "progress": {
            "total_estimate": 50_654,
            "processed_count": 30_863,
            "moved_count": 7_000,
        },
        "provider_states": [
            {
                "provider": "google_gmail",
                "account_id": "gmail-account",
                "phase": "monitoring",
                "status": "monitoring",
                "total_estimate": 11_363,
                "processed_count": 10_863,
                "moved_count": 6_333,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "provider": "microsoft_outlook",
                "account_id": "outlook-account",
                "phase": "backlog",
                "status": "running",
                "total_estimate": 39_291,
                "processed_count": 20_000,
                "moved_count": 667,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ],
    }

    task = next(
        item
        for item in await centre.list_tasks(principal_id="aaron", filter_name="ACTIVE")
        if item["task_id"] == "important_only:inbox"
    )

    assert task["status"] == "RUNNING"
    assert task["work_mode"] == "BOUNDED"
    assert task["progress"]["current"] == 20_000
    assert task["progress"]["total"] == 39_291
    assert task["progress"]["remaining"] == 19_291
    gmail = next(item for item in task["subtasks"] if item["title"] == "Gmail")
    outlook = next(item for item in task["subtasks"] if item["title"] == "Outlook")
    assert gmail["progress"]["mode"] == "NONE"
    assert outlook["progress"]["mode"] == "DETERMINATE"


def test_generic_continuous_task_has_no_fake_completion_or_eta(tmp_path: Path) -> None:
    centre = service(tmp_path / "tasks.db")
    task = centre._task(
        task_id="external:weather-watch",
        task_type="external_monitor",
        title="Watch the forecast",
        status=TaskCentreStatus.MONITORING,
        underlying_status="active",
        work_mode="CONTINUOUS",
        phase="MONITORING",
        progress_mode="NONE",
        progress_current=412,
        progress_total=500,
        progress_unit="checks",
    )

    centre._decorate_structured_progress("aaron", task)

    assert task["status"] == "MONITORING"
    assert task["progress"]["mode"] == "NONE"
    assert task["progress"]["current"] is None
    assert task["progress"]["percent"] is None
    assert task["progress"]["remaining"] is None
    assert task["timing"]["eta_seconds"] is None
    assert task["timing"]["eta_quality"] == "not_applicable"


@pytest.mark.parametrize(
    "status",
    [
        TaskCentreStatus.MONITORING,
        TaskCentreStatus.WAITING_FOR_JARVIS,
        TaskCentreStatus.WAITING_FOR_YOU,
        TaskCentreStatus.PAUSED,
        TaskCentreStatus.COMPLETED,
    ],
)
def test_eta_is_suppressed_for_every_non_running_state(
    tmp_path: Path,
    status: TaskCentreStatus,
) -> None:
    centre = service(tmp_path / f"tasks-{status.value}.db")
    task = centre._task(
        task_id=f"generic:{status.value.casefold()}",
        task_type="generic",
        title="Background work",
        status=status,
        underlying_status=status.value.casefold(),
        work_mode="CONTINUOUS" if status is TaskCentreStatus.MONITORING else "BOUNDED",
        progress_mode="NONE" if status is TaskCentreStatus.MONITORING else None,
        progress_current=40,
        progress_total=100,
        progress_unit="items",
    )

    centre._decorate_structured_progress("aaron", task)

    assert task["timing"]["eta_seconds"] is None
    assert task["timing"]["review_rate_per_second"] is None


@pytest.mark.asyncio
async def test_task_progress_eta_uses_bounded_review_history_and_clamps_percent(
    tmp_path: Path,
) -> None:
    centre = service(tmp_path / "tasks.db")
    centre.email_policies.important_only = {
        "enabled": True,
        "recoverable_cleanup_authority": True,
        "conversation_id": "usr:aaron:important-only",
        "providers": {"google_gmail": True, "microsoft_outlook": True},
        "progress": {"total_estimate": 50_654, "processed_count": 43_998},
        "provider_states": [
            {
                "provider": "google_gmail",
                "account_id": "gmail-account",
                "phase": "backlog",
                "status": "running",
                "processed_count": 10_598,
                "moved_count": 6_289,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "provider": "microsoft_outlook",
                "account_id": "outlook-account",
                "phase": "backlog",
                "status": "running",
                "processed_count": 33_400,
                "moved_count": 999,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ],
    }
    now = datetime.now(timezone.utc)
    with sqlite3.connect(centre.path) as connection:
        connection.executemany(
            "INSERT INTO task_progress_samples(principal_id,task_id,observed_at,"
            "progress_current,progress_total,progress_unit) VALUES(?,?,?,?,?,?)",
            [
                (
                    "aaron",
                    "important_only:inbox",
                    (now - timedelta(minutes=10)).isoformat(),
                    40_000,
                    50_654,
                    "messages",
                ),
                (
                    "aaron",
                    "important_only:inbox",
                    (now - timedelta(minutes=5)).isoformat(),
                    42_000,
                    50_654,
                    "messages",
                ),
            ],
        )

    task = next(
        item
        for item in await centre.list_tasks(principal_id="aaron", filter_name="ALL")
        if item["task_id"] == "important_only:inbox"
    )

    assert task["progress"]["percent"] == 87
    assert task["progress"]["remaining"] == 6_656
    assert task["timing"]["throughput_per_second"] > 0
    assert task["timing"]["eta_seconds"] > 0
    assert task["timing"]["eta_quality"] == "smoothed_rolling_15_minute"

    first_rate = task["timing"]["review_rate_per_second"]
    centre.email_policies.important_only["progress"]["processed_count"] = 48_000
    centre.email_policies.important_only["provider_states"][1]["processed_count"] = 37_402
    faster = next(
        item
        for item in await centre.list_tasks(principal_id="aaron", filter_name="ALL")
        if item["task_id"] == "important_only:inbox"
    )
    assert faster["timing"]["review_rate_per_second"] > first_rate
    assert faster["timing"]["review_rate_per_second"] < first_rate * 1.5

    task["progress_current"] = 60_000
    centre._decorate_structured_progress("aaron", task)
    assert task["progress"]["percent"] == 100
    assert task["progress"]["remaining"] == 0
    assert task["timing"]["eta_seconds"] is None

    task["status"] = TaskCentreStatus.COMPLETED.value
    task["progress_current"] = 40_000
    centre._decorate_structured_progress("aaron", task)
    assert task["progress"]["current"] == 50_654
    assert task["progress"]["percent"] == 100
    assert task["progress"]["remaining"] == 0
    assert task["timing"]["eta_seconds"] is None


@pytest.mark.asyncio
async def test_unknown_total_and_paused_tasks_never_invent_eta(tmp_path: Path) -> None:
    centre = service(tmp_path / "tasks.db")
    task = centre._task(
        task_id="fake:one",
        task_type="generic",
        title="Review results",
        status=TaskCentreStatus.PAUSED,
        underlying_status="paused",
        progress_current=400,
        progress_total=None,
        progress_unit="items",
    )

    centre._decorate_structured_progress("aaron", task)

    assert task["progress"]["mode"] == "INDETERMINATE"
    assert task["progress"]["percent"] is None
    assert task["progress"]["remaining"] is None
    assert task["timing"]["eta_seconds"] is None


@pytest.mark.asyncio
async def test_important_only_task_never_creates_authority_and_uses_provider_error_state(
    tmp_path: Path,
):
    centre = service(tmp_path / "tasks.db")
    centre.email_policies.important_only = {
        "enabled": False,
        "recoverable_cleanup_authority": False,
        "conversation_id": "usr:aaron:important-only",
        "providers": {"google_gmail": True, "microsoft_outlook": False},
        "progress": {},
        "provider_states": [
            {
                "provider": "google_gmail",
                "account_id": "gmail-account",
                "phase": "backlog",
                "status": "paused",
                "last_error": "Gmail needs reconnecting",
                "created_at": NOW,
                "updated_at": NOW,
            }
        ],
    }

    task = await centre.get_task(principal_id="aaron", task_id="important_only:inbox")

    assert task is not None
    assert task["can_resume"] is False
    assert (
        await centre.resume(
            principal_id="aaron", task_id="important_only:inbox", request_id="resume-no-authority"
        )
        is None
    )
    assert centre.email_policies.calls == []

    centre.email_policies.important_only["enabled"] = True
    centre.email_policies.important_only["recoverable_cleanup_authority"] = True
    centre.email_policies.important_only["provider_states"][0]["status"] = "waiting_provider"
    waiting = await centre.get_task(principal_id="aaron", task_id="important_only:inbox")
    assert waiting is not None
    assert waiting["status"] == "WAITING_FOR_YOU"
    assert waiting["user_action_type"] == "provider_reconnect"
    assert waiting["timing"]["eta_seconds"] is None


@pytest.mark.asyncio
async def test_pending_interaction_is_waiting_for_you_and_task_confirm_uses_same_path(tmp_path):
    dialogue = DialogueManager(str(tmp_path / "dialogue.db"))
    proposals = type("Proposals", (), {"resolve": lambda *args, **kwargs: None})()
    pending = PendingInteractionService(dialogue=dialogue, action_proposals=proposals)
    calls: list[tuple[str, str]] = []

    async def continue_selection(record, answer, request_id):
        calls.append((answer, request_id))
        return {"success": True, "response": "Outlook selected.", "intent": "selected"}

    pending.register_handler("select_mailbox", continue_selection)
    created = await pending.begin(
        principal_id="aaron",
        conversation_id="usr:aaron:mailbox-choice",
        kind=PendingInteractionKind.CLARIFICATION,
        goal="Choose the mailbox for this search",
        prompt="Do you mean Outlook?",
        unresolved_slot="provider",
        handler_id="select_mailbox",
        proposed_value="microsoft_outlook",
    )
    centre = service(tmp_path / "tasks.db")
    centre.set_pending_interaction_service(pending)

    tasks = await centre.list_tasks(principal_id="aaron", filter_name="WAITING_FOR_YOU")
    task = next(item for item in tasks if item["task_id"] == created["task_id"])
    resolved = await centre.confirm(principal_id="aaron", task_id=task["task_id"])

    assert task["status"] == "WAITING_FOR_YOU"
    assert task["user_action_type"] == "clarification"
    assert task["can_confirm"] is True
    assert resolved is not None and resolved["status"] == "COMPLETED"
    assert resolved["result_summary"] == "Outlook selected."
    assert calls and calls[0][0] == "microsoft_outlook"
    assert await centre.get_task(principal_id="aaron", task_id=task["task_id"]) is None


@pytest.mark.asyncio
async def test_external_monitor_recurring_and_cancelled_work_are_projected(
    tmp_path: Path,
) -> None:
    recurring_path = tmp_path / "recurring.db"
    connection = sqlite3.connect(recurring_path)
    try:
        connection.execute(
            "CREATE TABLE recurring_schedules("
            "schedule_id INTEGER PRIMARY KEY,owner_key TEXT,action_summary TEXT,"
            "recurrence_description TEXT,status TEXT,next_run_at TEXT,created_at TEXT,"
            "updated_at TEXT,last_run_at TEXT,last_error TEXT)"
        )
        connection.execute(
            "INSERT INTO recurring_schedules VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                7,
                "aaron",
                "Check the garden sensor",
                "Every morning",
                "active",
                NOW,
                NOW,
                NOW,
                None,
                None,
            ),
        )
        connection.commit()
    finally:
        connection.close()
    centre = service(
        tmp_path / "tasks.db",
        followups=[
            followup("monitor-door", "pending"),
            followup("cancelled-job", "cancelled"),
        ],
        legacy_recurring_path=recurring_path,
    )

    tasks = await centre.list_tasks(principal_id="aaron", filter_name="ALL")

    assert (
        next(item for item in tasks if item["task_id"] == "followup:monitor-door")["task_type"]
        == "external_monitor"
    )
    assert next(item for item in tasks if item["task_id"] == "recurring:7")["status"] == "SCHEDULED"
    assert (
        next(item for item in tasks if item["task_id"] == "followup:cancelled-job")["status"]
        == "CANCELLED"
    )


@pytest.mark.asyncio
async def test_projection_is_principal_isolated(tmp_path: Path):
    centre = service(
        tmp_path / "tasks.db",
        followups=[
            followup("aaron-job", "pending"),
            followup("amber-job", "pending", principal="amber"),
        ],
        bulks=[
            bulk("aaron-clean", "awaiting_confirmation"),
            bulk("amber-clean", "awaiting_confirmation", principal="amber"),
        ],
    )
    tasks = await centre.list_tasks(principal_id="aaron", filter_name="ALL")
    assert all("amber" not in item["task_id"] for item in tasks)
    assert {item["task_id"] for item in tasks} == {"followup:aaron-job", "email_bulk:aaron-clean"}


@pytest.mark.asyncio
async def test_notify_when_done_binds_the_single_conversation_task(tmp_path: Path):
    centre = service(tmp_path / "tasks.db", bulks=[bulk("cleanup", "awaiting_confirmation")])
    result = await centre.bind_notification_from_conversation(
        principal_id="aaron",
        conversation_id="usr:aaron:chat-1",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    assert result["bound"] is True
    assert "inbox cleanup finishes" in result["response"].casefold()
    task = await centre.get_task(principal_id="aaron", task_id="email_bulk:cleanup")
    assert task is not None and task["notification_on_completion"] is True

    failure = await centre.bind_notification_from_conversation(
        principal_id="aaron",
        conversation_id="usr:aaron:chat-1",
        notify_on_completion=False,
        notify_on_failure=True,
    )
    assert failure["bound"] is True
    assert "if inbox cleanup fails" in failure["response"].casefold()
    task = await centre.get_task(principal_id="aaron", task_id="email_bulk:cleanup")
    assert task is not None
    assert task["notification_on_completion"] is True
    assert task["notification_on_failure"] is True


@pytest.mark.asyncio
async def test_ambiguous_or_missing_task_is_truthful(tmp_path: Path):
    empty = service(tmp_path / "empty.db")
    missing = await empty.bind_notification_from_conversation(
        principal_id="aaron",
        conversation_id="usr:aaron:chat-1",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    assert missing["reason"] == "no_active_task"
    centre = service(
        tmp_path / "many.db",
        followups=[followup("monitor-one", "pending")],
        bulks=[bulk("cleanup", "awaiting_confirmation")],
    )
    ambiguous = await centre.bind_notification_from_conversation(
        principal_id="aaron",
        conversation_id="usr:aaron:chat-1",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    assert ambiguous["reason"] == "ambiguous"
    assert "Which one" in ambiguous["response"]


@pytest.mark.asyncio
async def test_completion_notification_is_exactly_once_across_restart(tmp_path: Path):
    database = tmp_path / "tasks.db"
    notifier = FakeNotifier()
    row = bulk("cleanup", "awaiting_confirmation")
    first = service(database, bulks=[row], notifier=notifier)
    await first.set_notification_preference(
        principal_id="aaron",
        task_id="email_bulk:cleanup",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    row["status"] = "completed"
    row["succeeded_count"] = row["intended_count"]
    row["completed_at"] = NOW
    assert await first.process_notifications_once() == 1
    restarted = service(database, bulks=[row], notifier=notifier)
    assert await restarted.process_notifications_once() == 0
    assert len(notifier.messages) == 1
    assert "finished" in notifier.messages[0]["message"]


@pytest.mark.asyncio
async def test_requested_failure_notification_is_sent_once(tmp_path: Path):
    notifier = FakeNotifier()
    row = followup("failed-1", "pending")
    centre = service(tmp_path / "tasks.db", followups=[row], notifier=notifier)
    await centre.set_notification_preference(
        principal_id="aaron",
        task_id="followup:failed-1",
        notify_on_completion=False,
        notify_on_failure=True,
    )
    row["status"] = "failed"
    row["result"] = {"error": "Provider unavailable"}
    assert await centre.process_notifications_once() == 1
    assert await centre.process_notifications_once() == 0
    assert "needs your attention" in notifier.messages[0]["message"]


@pytest.mark.asyncio
async def test_partial_task_uses_completion_subscription_and_never_claims_success(
    tmp_path: Path,
) -> None:
    notifier = FakeNotifier()
    row = bulk("partial-cleanup", "awaiting_confirmation")
    centre = service(tmp_path / "tasks.db", bulks=[row], notifier=notifier)
    await centre.set_notification_preference(
        principal_id="aaron",
        task_id="email_bulk:partial-cleanup",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    row.update(status="partial", succeeded_count=10, attempted_count=12, failed_count=2)

    assert await centre.process_notifications_once() == 1
    assert "partially completed" in notifier.messages[0]["message"]
    task = await centre.get_task(principal_id="aaron", task_id="email_bulk:partial-cleanup")
    assert task is not None
    assert task["completion_notification_state"] == "accepted_unverified"
    assert task["notification_delivered_at"] is None


@pytest.mark.asyncio
async def test_disable_task_notifications_preserves_task_and_sends_nothing(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    row = bulk("disabled-cleanup", "awaiting_confirmation")
    centre = service(tmp_path / "tasks.db", bulks=[row], notifier=notifier)
    await centre.set_notification_preference(
        principal_id="aaron",
        task_id="email_bulk:disabled-cleanup",
        notify_on_completion=True,
        notify_on_failure=True,
    )
    result = await centre.bind_notification_from_conversation(
        principal_id="aaron",
        conversation_id="usr:aaron:chat-1",
        notify_on_completion=False,
        notify_on_failure=False,
    )
    assert result["bound"] is True
    assert "won’t notify" in result["response"]
    row.update(status="completed", succeeded_count=row["intended_count"], completed_at=NOW)
    assert await centre.process_notifications_once() == 0
    assert notifier.messages == []
    task = await centre.get_task(principal_id="aaron", task_id="email_bulk:disabled-cleanup")
    assert task is not None
    assert task["notification_on_completion"] is False
    assert task["notification_on_failure"] is False


@pytest.mark.asyncio
async def test_partial_mobile_notification_submission_is_unknown_and_never_retried(
    tmp_path: Path,
) -> None:
    class PartialNotifier(FakeNotifier):
        async def __call__(self, **values: str):
            self.messages.append(values)
            return {"success": False, "command_sent": True, "outcome_unknown": True}

    path = tmp_path / "tasks.db"
    notifier = PartialNotifier()
    row = bulk("partial-notification", "awaiting_confirmation")
    first = service(path, bulks=[row], notifier=notifier)
    await first.set_notification_preference(
        principal_id="aaron",
        task_id="email_bulk:partial-notification",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    row.update(status="completed", succeeded_count=row["intended_count"], completed_at=NOW)
    assert await first.process_notifications_once() == 0

    restarted = service(path, bulks=[row], notifier=notifier)
    assert await restarted.process_notifications_once() == 0
    assert len(notifier.messages) == 1
    task = await restarted.get_task(principal_id="aaron", task_id="email_bulk:partial-notification")
    assert task is not None
    assert task["completion_notification_state"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_cancelled_task_does_not_send_completion_notification(tmp_path: Path) -> None:
    notifier = FakeNotifier()
    row = bulk("cancelled-cleanup", "awaiting_confirmation")
    centre = service(tmp_path / "tasks.db", bulks=[row], notifier=notifier)
    await centre.set_notification_preference(
        principal_id="aaron",
        task_id="email_bulk:cancelled-cleanup",
        notify_on_completion=True,
        notify_on_failure=True,
    )
    row["status"] = "cancelled"

    assert await centre.process_notifications_once() == 0
    assert notifier.messages == []


@pytest.mark.asyncio
async def test_unknown_notification_outcome_is_not_blindly_resent_after_restart(
    tmp_path: Path,
) -> None:
    class UnknownNotifier(FakeNotifier):
        async def __call__(self, **values: str):
            self.messages.append(values)
            raise TimeoutError("connection ended after submit")

    path = tmp_path / "tasks.db"
    notifier = UnknownNotifier()
    row = bulk("unknown-cleanup", "awaiting_confirmation")
    first = service(path, bulks=[row], notifier=notifier)
    await first.set_notification_preference(
        principal_id="aaron",
        task_id="email_bulk:unknown-cleanup",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    row["status"] = "completed"
    row["succeeded_count"] = row["intended_count"]

    assert await first.process_notifications_once() == 0
    restarted = service(path, bulks=[row], notifier=notifier)
    assert await restarted.process_notifications_once() == 0
    assert len(notifier.messages) == 1
    task = await restarted.get_task(principal_id="aaron", task_id="email_bulk:unknown-cleanup")
    assert task is not None
    assert task["completion_notification_state"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_restart_converts_inflight_delivery_to_unknown_without_resend(
    tmp_path: Path,
) -> None:
    path = tmp_path / "tasks.db"
    notifier = FakeNotifier()
    row = bulk("crash-window", "completed")
    first = service(path, bulks=[row], notifier=notifier)
    await first.set_notification_preference(
        principal_id="aaron",
        task_id="email_bulk:crash-window",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    with first._db() as connection:
        connection.execute(
            "INSERT INTO task_notification_deliveries("
            "principal_id,task_id,terminal_status,delivery_kind,state,delivery_message,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                "aaron",
                "email_bulk:crash-window",
                "COMPLETED",
                "completion",
                "attempting",
                "Inbox cleanup finished.",
                NOW,
                NOW,
            ),
        )

    restarted = service(path, bulks=[row], notifier=notifier)

    assert await restarted.process_notifications_once() == 0
    assert notifier.messages == []
    task = await restarted.get_task(principal_id="aaron", task_id="email_bulk:crash-window")
    assert task is not None
    assert task["completion_notification_state"] == "outcome_unknown"


@pytest.mark.asyncio
async def test_existing_followup_notification_delivery_is_not_duplicated(
    tmp_path: Path,
) -> None:
    notifier = FakeNotifier()
    row = followup("reminder", "pending")
    row["payload"]["notify"] = True
    centre = service(tmp_path / "tasks.db", followups=[row], notifier=notifier)
    await centre.set_notification_preference(
        principal_id="aaron",
        task_id="followup:reminder",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    row["status"] = "completed"
    row["notification_state"] = "accepted_unverified"

    assert await centre.process_notifications_once() == 0
    assert notifier.messages == []
    task = await centre.get_task(principal_id="aaron", task_id="followup:reminder")
    assert task is not None
    assert task["completion_notification_state"] == "source_accepted_unverified"


@pytest.mark.asyncio
async def test_controls_delegate_without_reimplementing_execution(tmp_path: Path):
    centre = service(
        tmp_path / "tasks.db",
        followups=[followup("job", "pending")],
        bulks=[bulk("cleanup", "partial")],
    )
    await centre.pause(principal_id="aaron", task_id="followup:job", request_id="pause")
    await centre.resume(principal_id="aaron", task_id="followup:job", request_id="resume")
    await centre.reschedule(
        principal_id="aaron",
        task_id="followup:job",
        request_id="reschedule",
        due_at=datetime.now(timezone.utc) + timedelta(hours=2),
        timezone_name="Europe/London",
    )
    await centre.retry(principal_id="aaron", task_id="email_bulk:cleanup")
    await centre.cancel(principal_id="aaron", task_id="followup:job", request_id="cancel")
    assert centre.followups.calls == [
        ("pause", "job"),
        ("resume", "job"),
        ("reschedule", "job"),
        ("cancel", "job"),
    ]
    assert centre.email_policies.calls == [("retry", "cleanup")]


@pytest.mark.asyncio
async def test_email_confirmation_and_decline_delegate_to_existing_authority_boundary(
    tmp_path: Path,
) -> None:
    centre = service(
        tmp_path / "tasks.db",
        bulks=[
            bulk("confirm-cleanup", "awaiting_confirmation"),
            bulk("decline-cleanup", "awaiting_confirmation"),
        ],
    )

    await centre.confirm_email_cleanup(principal_id="aaron", task_id="email_bulk:confirm-cleanup")
    await centre.cancel(
        principal_id="aaron",
        task_id="email_bulk:decline-cleanup",
        request_id="decline",
    )

    assert centre.email_policies.calls == [
        ("execute", "confirm-cleanup"),
        ("cancel", "decline-cleanup"),
    ]


@pytest.mark.asyncio
async def test_multi_provider_cleanup_is_one_task_with_separate_provider_progress(
    tmp_path: Path,
) -> None:
    centre = service(
        tmp_path / "tasks.db",
        bulks=[
            bulk(
                "gmail-cleanup",
                "awaiting_confirmation",
                provider="google_gmail",
                task_group_id="request-1",
            ),
            bulk(
                "outlook-cleanup",
                "interrupted",
                provider="microsoft_outlook",
                task_group_id="request-1",
            ),
        ],
    )

    tasks = await centre.list_tasks(principal_id="aaron", filter_name="ACTIVE")

    assert [item["task_id"] for item in tasks] == ["email_group:request-1"]
    task = tasks[0]
    assert task["status"] == "WAITING_FOR_JARVIS"
    assert task["providers"] == ["google_gmail", "microsoft_outlook"]
    assert {item["title"] for item in task["planned_steps"]} == {"Gmail", "Outlook"}
    assert task["result_summary"] == "No email has been changed yet"


@pytest.mark.asyncio
async def test_provider_reauthentication_is_waiting_for_you_not_fake_progress(
    tmp_path: Path,
) -> None:
    row = bulk("gmail-reconnect", "interrupted")
    row["halt_reason"] = "Gmail needs reconnecting before Jarvis can continue"
    centre = service(tmp_path / "tasks.db", bulks=[row])

    task = await centre.get_task(principal_id="aaron", task_id="email_bulk:gmail-reconnect")

    assert task is not None
    assert task["status"] == "WAITING_FOR_YOU"
    assert task["requires_user_action"] is True
    assert task["user_action_type"] == "provider_reconnect"
    assert task["can_retry"] is False
    assert "reconnecting" in task["waiting_reason"].casefold()


@pytest.mark.asyncio
async def test_retrying_partial_group_does_not_repeat_completed_provider(
    tmp_path: Path,
) -> None:
    centre = service(
        tmp_path / "tasks.db",
        bulks=[
            bulk(
                "gmail-complete",
                "completed",
                provider="google_gmail",
                task_group_id="request-2",
            ),
            bulk(
                "outlook-partial",
                "partial",
                provider="microsoft_outlook",
                task_group_id="request-2",
            ),
        ],
    )

    await centre.retry(principal_id="aaron", task_id="email_group:request-2")

    assert centre.email_policies.calls == [("retry", "outlook-partial")]


@pytest.mark.asyncio
async def test_detail_exposes_plan_evidence_not_hidden_reasoning_or_secrets(tmp_path: Path):
    row = executive()
    row["objective"] = "Check mail sk-secret-value"
    row["last_verified_result"] = "Outlook checked"
    centre = service(tmp_path / "tasks.db", executives=[row], plans=[plan()])
    detail = await centre.get_task(principal_id="aaron", task_id="executive:exec-1")
    rendered = str(detail)
    assert "sk-secret-value" not in rendered
    assert "chain_of_thought" not in rendered
    assert "Check Outlook" in rendered
    assert "Outlook checked" in rendered


@pytest.mark.asyncio
async def test_successful_provider_step_remains_completed_in_partial_plan(tmp_path: Path):
    centre = service(tmp_path / "tasks.db", plans=[plan()])
    tasks = await centre.list_tasks(principal_id="aaron", filter_name="PROBLEMS")
    task = next(item for item in tasks if item["task_id"] == "agent_plan:plan-1")
    outlook = next(item for item in task["planned_steps"] if item["step_id"] == "outlook")
    gmail = next(item for item in task["planned_steps"] if item["step_id"] == "gmail")
    assert outlook["status"] == "succeeded"
    assert gmail["status"] == "blocked"
    assert task["waiting_reason"] == "Gmail unavailable"


def appointment_plan(*, missing_capability: bool = False) -> AgentPlan:
    find = PlanStep(
        step_id="find-slot",
        title="Find an available appointment",
        capability=CapabilityRequirement(
            "appointments.search", CapabilityAccess.READ, EvidenceRequirement.ACCEPTED
        ),
        arguments={},
        depends_on=(),
        risk=RiskLevel.LOW,
        required_confirmation=False,
        confirmation_status=ConfirmationStatus.NOT_REQUIRED,
        max_attempts=2,
        continuation=None,
        action_id="appointment-read",
        status=StepStatus.SUCCEEDED if not missing_capability else StepStatus.BLOCKED,
        result=None if missing_capability else {"summary": "One suitable slot found"},
        failure=(
            StepFailure(
                "capability_missing",
                "No supported booking capability for this provider",
                retryable=False,
            )
            if missing_capability
            else None
        ),
        completed_at=None if missing_capability else NOW,
    )
    steps = [find]
    status = PlanStatus.BLOCKED if missing_capability else PlanStatus.AWAITING_APPROVAL
    if not missing_capability:
        steps.append(
            PlanStep(
                step_id="book-slot",
                title="Book the selected appointment",
                capability=CapabilityRequirement(
                    "appointments.book",
                    CapabilityAccess.WRITE,
                    EvidenceRequirement.VERIFIED,
                ),
                arguments={"slot": {"$from_step": "find-slot", "path": "slot_id"}},
                depends_on=("find-slot",),
                risk=RiskLevel.HIGH,
                required_confirmation=True,
                confirmation_status=ConfirmationStatus.PENDING,
                max_attempts=1,
                continuation=None,
                action_id="appointment-write",
                status=StepStatus.AWAITING_APPROVAL,
            )
        )
    return AgentPlan(
        plan_id="appointment-plan",
        conversation_id="usr:aaron:appointment-chat",
        goal="Book the appointment",
        status=status,
        steps=steps,
        continuation=None,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_unseen_capability_domain_projects_and_uses_generic_confirmation_control(
    tmp_path: Path,
) -> None:
    value = appointment_plan()
    centre = service(tmp_path / "tasks.db", plans=[value])

    task = await centre.get_task(principal_id="aaron", task_id="agent_plan:appointment-plan")

    assert task is not None
    assert task["title"] == "Book the appointment"
    assert task["status"] == "WAITING_FOR_YOU"
    assert task["providers"] == ["appointments"]
    assert task["capabilities"] == ["appointments.search", "appointments.book"]
    assert task["progress_current"] == 1
    assert task["progress_total"] == 2
    assert task["current_step"] == "Book the selected appointment"
    assert task["can_confirm"] is True
    assert task["can_decline"] is True
    assert task["metadata"]["confirmation_step_ids"] == ["book-slot"]
    assert task["planned_steps"][0]["result_summary"] == "One suitable slot found"

    confirmed = await centre.confirm(principal_id="aaron", task_id="agent_plan:appointment-plan")
    assert confirmed is not None
    assert centre.planner.calls == [
        ("approve", "book-slot"),
        ("resume", "appointment-plan"),
    ]


@pytest.mark.asyncio
async def test_unseen_domain_uses_generic_decline_and_task_notification_lifecycle(
    tmp_path: Path,
) -> None:
    value = appointment_plan()
    notifier = FakeNotifier()
    centre = service(tmp_path / "tasks.db", plans=[value], notifier=notifier)
    bound = await centre.bind_notification_from_conversation(
        principal_id="aaron",
        conversation_id="usr:aaron:appointment-chat",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    assert bound["bound"] is True

    declined = await centre.decline(
        principal_id="aaron",
        task_id="agent_plan:appointment-plan",
        request_id="decline-appointment",
    )

    assert declined is not None
    assert centre.planner.calls == [
        ("decline", "book-slot"),
        ("resume", "appointment-plan"),
    ]
    assert await centre.process_notifications_once() == 1
    assert "partially completed" in notifier.messages[0]["message"]

    # A separate generic plan that reaches verified completion uses the same
    # source-neutral, exactly-once notification ledger.
    completed = appointment_plan()
    completed.plan_id = "appointment-complete"
    completed.status = PlanStatus.COMPLETED
    completed.step("book-slot").status = StepStatus.SUCCEEDED
    completed.step("book-slot").confirmation_status = ConfirmationStatus.APPROVED
    completed.step("book-slot").result = {"summary": "Appointment booked and verified"}
    completed.step("book-slot").action_receipt = {"receipt_id": "verified-booking"}
    completed.step("book-slot").completed_at = NOW
    centre.planner.plans[completed.plan_id] = completed
    await centre.set_notification_preference(
        principal_id="aaron",
        task_id="agent_plan:appointment-complete",
        notify_on_completion=True,
        notify_on_failure=False,
    )
    assert await centre.process_notifications_once() == 1
    assert await centre.process_notifications_once() == 0
    assert len(notifier.messages) == 2
    assert "finished" in notifier.messages[1]["message"]


@pytest.mark.asyncio
async def test_unsupported_new_domain_is_truthfully_waiting_for_intervention(
    tmp_path: Path,
) -> None:
    centre = service(tmp_path / "tasks.db", plans=[appointment_plan(missing_capability=True)])

    task = await centre.get_task(principal_id="aaron", task_id="agent_plan:appointment-plan")

    assert task is not None
    assert task["status"] == "WAITING_FOR_YOU"
    assert task["user_action_type"] == "capability_setup_or_intervention"
    assert task["waiting_reason"] == "No supported booking capability for this provider"
    assert task["can_retry"] is False
    assert task["can_confirm"] is False
    assert task["result_summary"] is None

    executive_row = executive("appointment-exec")
    executive_row.update(
        objective="Book the appointment",
        conversation_id="usr:aaron:appointment-chat",
        plan_id="appointment-plan",
        current_step="Book the appointment",
        waiting_reason="No supported booking capability for this provider",
    )
    linked = service(
        tmp_path / "linked-tasks.db",
        executives=[executive_row],
        plans=[appointment_plan(missing_capability=True)],
    )
    executive_task = await linked.get_task(
        principal_id="aaron", task_id="executive:appointment-exec"
    )
    assert executive_task is not None
    assert executive_task["status"] == "WAITING_FOR_YOU"
    assert executive_task["user_action_type"] == "capability_setup_or_intervention"
    assert executive_task["can_retry"] is False
