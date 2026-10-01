from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.action_proposals import ActionProposalService
from app.agent_planner import AgentPlan, PlanStatus
from app.connectors import CapabilityAccess


class FakeRegistry:
    def __init__(self) -> None:
        self.capabilities = {
            "home.state": SimpleNamespace(capability_id="home.state", access=CapabilityAccess.READ),
            "web.search": SimpleNamespace(capability_id="web.search", access=CapabilityAccess.READ),
            "calendar.read": SimpleNamespace(
                capability_id="calendar.read", access=CapabilityAccess.READ
            ),
            "fixture.inspect": SimpleNamespace(
                capability_id="fixture.inspect", access=CapabilityAccess.READ
            ),
        }

    async def executable_capabilities(self, *, principal_id: str):
        return tuple(self.capabilities.values()) if principal_id == "aaron" else ()


class FakePlannerExecutor:
    def __init__(self) -> None:
        self.principal = None

    @staticmethod
    def scope_conversation(conversation_id: str, principal_id: str) -> str:
        if conversation_id.startswith("usr:"):
            if not conversation_id.startswith(f"usr:{principal_id}:"):
                raise ValueError("principal mismatch")
            return conversation_id
        return f"usr:{principal_id}:{conversation_id}"

    def set_principal(self, principal_id: str):
        previous = self.principal
        self.principal = principal_id
        return previous

    def reset_principal(self, token) -> None:
        self.principal = token


class FakePlanner:
    def __init__(self) -> None:
        self.plans: list[AgentPlan] = []
        self.cancelled: list[str] = []

    async def list_plans(self, *, conversation_id=None, limit=100, status=None):
        return [
            item
            for item in self.plans
            if conversation_id is None or item.conversation_id == conversation_id
        ][:limit]

    async def cancel(self, plan_id: str):
        plan = next(item for item in self.plans if item.plan_id == plan_id)
        plan.status = PlanStatus.CANCELLED
        self.cancelled.append(plan_id)
        return plan


class FakeRuntime:
    def __init__(self) -> None:
        self.registry = FakeRegistry()
        self.planner_executor = FakePlannerExecutor()
        self.planner = FakePlanner()
        self.created: list[dict] = []

    async def create_plan(self, **kwargs):
        self.created.append(kwargs)
        return {"plan_id": f"proposal-{len(self.created)}", "status": "awaiting_approval"}


class FakeTaskCentre:
    def __init__(self) -> None:
        self.tasks: list[dict] = []
        self.confirmed: list[tuple[str, str]] = []
        self.declined: list[tuple[str, str, str]] = []

    async def list_tasks(self, *, principal_id: str, filter_name: str, limit: int):
        return [
            dict(item)
            for item in self.tasks
            if item.get("principal_id", principal_id) == principal_id
        ][:limit]

    async def confirm(self, *, principal_id: str, task_id: str):
        self.confirmed.append((principal_id, task_id))
        task = next((item for item in self.tasks if item["task_id"] == task_id), None)
        if task is None:
            return None
        task.update(status="COMPLETED", can_confirm=False, can_decline=False)
        return dict(task)

    async def decline(self, *, principal_id: str, task_id: str, request_id: str):
        self.declined.append((principal_id, task_id, request_id))
        task = next((item for item in self.tasks if item["task_id"] == task_id), None)
        if task is None:
            return None
        task.update(status="CANCELLED", can_confirm=False, can_decline=False)
        return dict(task)


def pending_task(task_id: str, *, conversation: str = "usr:aaron:conversation") -> dict:
    return {
        "task_id": task_id,
        "principal_id": "aaron",
        "title": "Check the requested evidence",
        "status": "WAITING_FOR_YOU",
        "conversation_id": conversation,
        "can_confirm": True,
        "can_decline": True,
        "result_summary": "Grounded evidence returned",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability_id",
    ("home.state", "web.search", "calendar.read", "fixture.inspect"),
)
async def test_registered_domains_stage_the_same_generic_durable_proposal(capability_id) -> None:
    runtime = FakeRuntime()
    tasks = FakeTaskCentre()
    service = ActionProposalService(runtime=runtime, task_centre=tasks)

    proposal = await service.propose_capability(
        principal_id="aaron",
        conversation_id="conversation",
        capability_id=capability_id,
        arguments={"query": "grounded value"},
        title="Check the requested evidence",
        prompt="Shall I check that now?",
    )

    assert proposal["task_id"] == "agent_plan:proposal-1"
    assert runtime.created[0]["steps"][0]["capability_id"] == capability_id
    assert runtime.created[0]["steps"][0]["requires_confirmation"] is True
    assert runtime.created[0]["continuation"]["action_proposal"]["principal_id"] == "aaron"


@pytest.mark.asyncio
async def test_yes_and_no_use_the_same_task_centre_authority_path_after_restart() -> None:
    runtime = FakeRuntime()
    tasks = FakeTaskCentre()
    tasks.tasks = [pending_task("agent_plan:proposal-read")]
    restarted = ActionProposalService(runtime=runtime, task_centre=tasks)

    accepted = await restarted.resolve(
        principal_id="aaron",
        conversation_id="conversation",
        confirmation="affirmative",
        request_id="yes-1",
    )

    assert accepted is not None and accepted.intent == "action_proposal_accepted"
    assert accepted.response == "Done — Grounded evidence returned"
    assert tasks.confirmed == [("aaron", "agent_plan:proposal-read")]
    duplicate = await restarted.resolve(
        principal_id="aaron",
        conversation_id="conversation",
        confirmation="affirmative",
        request_id="yes-duplicate",
    )
    assert duplicate is None
    assert tasks.confirmed == [("aaron", "agent_plan:proposal-read")]

    tasks.tasks = [pending_task("agent_plan:proposal-decline")]
    declined = await restarted.resolve(
        principal_id="aaron",
        conversation_id="conversation",
        confirmation="negative",
        request_id="no-1",
    )
    assert declined is not None and declined.intent == "action_proposal_declined"
    assert tasks.declined == [("aaron", "agent_plan:proposal-decline", "no-1")]


@pytest.mark.asyncio
async def test_proposals_are_principal_and_conversation_scoped_and_ambiguous_fail_closed() -> None:
    runtime = FakeRuntime()
    tasks = FakeTaskCentre()
    tasks.tasks = [
        pending_task("agent_plan:one"),
        pending_task("agent_plan:two"),
        pending_task("agent_plan:other", conversation="usr:aaron:other"),
        {**pending_task("agent_plan:amber"), "principal_id": "amber"},
    ]
    service = ActionProposalService(runtime=runtime, task_centre=tasks)

    ambiguous = await service.resolve(
        principal_id="aaron",
        conversation_id="conversation",
        confirmation="affirmative",
        request_id="ambiguous",
    )
    other = await service.resolve(
        principal_id="aaron",
        conversation_id="missing",
        confirmation="affirmative",
        request_id="missing",
    )

    assert ambiguous is not None and ambiguous.intent == "action_proposal_ambiguous"
    assert tasks.confirmed == []
    assert other is None


@pytest.mark.asyncio
async def test_expired_and_superseded_proposals_cannot_execute() -> None:
    runtime = FakeRuntime()
    tasks = FakeTaskCentre()
    expired = AgentPlan(
        plan_id="expired",
        conversation_id="usr:aaron:conversation",
        goal="Expired action",
        status=PlanStatus.AWAITING_APPROVAL,
        steps=[],
        continuation={
            "action_proposal": {
                "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            }
        },
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    runtime.planner.plans.append(expired)
    service = ActionProposalService(runtime=runtime, task_centre=tasks)

    result = await service.resolve(
        principal_id="aaron",
        conversation_id="conversation",
        confirmation="affirmative",
        request_id="expired",
    )

    assert result is None
    assert runtime.planner.cancelled == ["expired"]
    assert tasks.confirmed == []


@pytest.mark.asyncio
async def test_unregistered_capability_cannot_become_model_prose_authority() -> None:
    service = ActionProposalService(runtime=FakeRuntime(), task_centre=FakeTaskCentre())
    with pytest.raises(ValueError, match="not currently executable"):
        await service.propose_capability(
            principal_id="aaron",
            conversation_id="conversation",
            capability_id="invented.delete_everything",
            arguments={},
            title="Invented action",
            prompt="Shall I do that?",
        )
