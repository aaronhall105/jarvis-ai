"""Durable, provider-neutral conversational action proposals.

An ActionProposal is a deliberately paused one-step agent plan.  The existing
planner remains authoritative for capability validation, approval, execution,
receipts, restart recovery, and idempotency; Task Centre remains the common UI
projection and confirmation boundary.  This module only resolves which exact
pending task a conversational ``Yes`` or ``No`` refers to.

Generated prose can never create a proposal.  Callers must supply a registered
capability and structured arguments before presenting an executable offer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.agent_planner import AgentPlan, PlanStatus
from app.connectors.credentials import redact_text


_PROPOSAL_KIND = "action_proposal"
_PENDING_STATUSES = {PlanStatus.PENDING, PlanStatus.AWAITING_APPROVAL, PlanStatus.BLOCKED}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _proposal_metadata(plan: AgentPlan) -> dict[str, Any] | None:
    continuation = plan.continuation
    if not isinstance(continuation, Mapping):
        return None
    value = continuation.get(_PROPOSAL_KIND)
    return dict(value) if isinstance(value, Mapping) else None


@dataclass(frozen=True)
class ProposalResolution:
    handled: bool
    response: str
    intent: str
    task_id: str | None = None
    proposal_id: str | None = None
    action_outcome: str = "not_applicable"
    tool_called: bool = False

    def as_result(self) -> dict[str, object]:
        return {
            "success": True,
            "turn_handled": True,
            "response": self.response,
            "intent": self.intent,
            "task_id": self.task_id,
            "proposal_id": self.proposal_id,
            "action_outcome": self.action_outcome,
            "tool_called": self.tool_called,
        }


class ActionProposalService:
    """Create and resolve durable offers through planner + Task Centre state."""

    def __init__(self, *, runtime: Any, task_centre: Any, ttl_seconds: int = 600) -> None:
        self.runtime = runtime
        self.planner = runtime.planner
        self.task_centre = task_centre
        self.ttl_seconds = max(60, min(int(ttl_seconds), 86_400))

    async def propose_capability(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        capability_id: str,
        arguments: Mapping[str, Any],
        title: str,
        prompt: str,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Persist an exact registered action before its offer is rendered."""

        principal = str(principal_id or "").strip()
        conversation = str(conversation_id or "").strip()
        capability = str(capability_id or "").strip()
        safe_title = " ".join(str(title or "").split()).strip()
        safe_prompt = " ".join(str(prompt or "").split()).strip()
        if not principal or not conversation or not capability or not safe_title or not safe_prompt:
            raise ValueError("A proposal requires principal, conversation, capability and prompt")

        executable = {
            str(item.capability_id): item
            for item in await self.runtime.registry.executable_capabilities(
                principal_id=principal,
            )
        }
        metadata = executable.get(capability)
        if metadata is None:
            raise ValueError("The proposed capability is not currently executable")

        scoped = self.runtime.planner_executor.scope_conversation(conversation, principal)
        await self._supersede(scoped)
        lifetime = max(60, min(int(ttl_seconds or self.ttl_seconds), 86_400))
        expires_at = (_now() + timedelta(seconds=lifetime)).isoformat()
        access = str(getattr(metadata.access, "value", metadata.access))
        plan = await self.runtime.create_plan(
            conversation_id=conversation,
            principal_id=principal,
            goal=safe_title,
            steps=(
                {
                    "step_id": "proposed_action",
                    "title": safe_title,
                    "capability_id": capability,
                    "access": access,
                    "evidence": "verified" if access == "write" else "accepted",
                    "arguments": dict(arguments),
                    "risk": "moderate" if access == "write" else "low",
                    "requires_confirmation": True,
                    "max_attempts": 1,
                },
            ),
            continuation={
                _PROPOSAL_KIND: {
                    "version": 1,
                    "principal_id": principal,
                    "conversation_id": scoped,
                    "capability_id": capability,
                    "prompt": safe_prompt,
                    "expires_at": expires_at,
                }
            },
            start=True,
        )
        if str(plan.get("status") or "") != PlanStatus.AWAITING_APPROVAL.value:
            raise RuntimeError("The structured action could not be staged for confirmation")
        return {
            "proposal_id": str(plan["plan_id"]),
            "task_id": f"agent_plan:{plan['plan_id']}",
            "prompt": safe_prompt,
            "expires_at": expires_at,
            "status": "pending",
        }

    async def _supersede(self, conversation_id: str) -> None:
        plans = await self.planner.list_plans(conversation_id=conversation_id, limit=100)
        for plan in plans:
            if plan.status not in _PENDING_STATUSES or _proposal_metadata(plan) is None:
                continue
            await self.planner.cancel(plan.plan_id)

    async def _expire_proposals(self, conversation_id: str) -> None:
        plans = await self.planner.list_plans(conversation_id=conversation_id, limit=100)
        current = _now()
        for plan in plans:
            metadata = _proposal_metadata(plan)
            if metadata is None or plan.status not in _PENDING_STATUSES:
                continue
            expires_at = _parse_time(metadata.get("expires_at"))
            if expires_at is None or expires_at <= current:
                await self.planner.cancel(plan.plan_id)

    async def _candidates(
        self,
        *,
        principal_id: str,
        conversation_id: str,
    ) -> list[dict[str, Any]]:
        scoped = self.runtime.planner_executor.scope_conversation(conversation_id, principal_id)
        await self._expire_proposals(scoped)
        tasks = await self.task_centre.list_tasks(
            principal_id=principal_id,
            filter_name="WAITING_FOR_YOU",
            limit=250,
        )
        return [
            task
            for task in tasks
            if str(task.get("conversation_id") or "") == scoped
            and (task.get("can_confirm") or task.get("can_decline"))
        ]

    async def resolve(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        confirmation: str,
        request_id: str,
    ) -> ProposalResolution | None:
        """Resolve one unambiguous current proposal without inferring authority."""

        candidates = await self._candidates(
            principal_id=principal_id,
            conversation_id=conversation_id,
        )
        if not candidates:
            return None
        if len(candidates) > 1:
            labels = [str(item.get("title") or "that task") for item in candidates[:3]]
            choices = " or ".join(labels) if len(labels) <= 2 else ", ".join(labels)
            return ProposalResolution(
                handled=True,
                response=f"Which task do you mean — {choices}?",
                intent="action_proposal_ambiguous",
            )

        task = candidates[0]
        task_id = str(task["task_id"])
        proposal_id = task_id.split(":", 1)[1] if task_id.startswith("agent_plan:") else None
        if confirmation == "negative":
            token = self.runtime.planner_executor.set_principal(principal_id)
            try:
                declined = await self.task_centre.decline(
                    principal_id=principal_id,
                    task_id=task_id,
                    request_id=request_id,
                )
            finally:
                self.runtime.planner_executor.reset_principal(token)
            if declined is None:
                return ProposalResolution(
                    handled=True,
                    response="That action is no longer waiting for a decision.",
                    intent="action_proposal_stale",
                    task_id=task_id,
                    proposal_id=proposal_id,
                )
            return ProposalResolution(
                handled=True,
                response=f"Okay — I won't {self._action_phrase(task)}.",
                intent="action_proposal_declined",
                task_id=task_id,
                proposal_id=proposal_id,
                action_outcome="cancelled",
            )

        token = self.runtime.planner_executor.set_principal(principal_id)
        try:
            completed = await self.task_centre.confirm(
                principal_id=principal_id,
                task_id=task_id,
            )
        finally:
            self.runtime.planner_executor.reset_principal(token)
        if completed is None:
            return ProposalResolution(
                handled=True,
                response="That action is no longer waiting for confirmation.",
                intent="action_proposal_stale",
                task_id=task_id,
                proposal_id=proposal_id,
            )
        status = str(completed.get("status") or "")
        if status == "COMPLETED":
            summary = self._result_summary(completed)
            response = (
                f"Done — {summary}"
                if summary
                else "Done — the action completed with verified provider evidence."
            )
            outcome = "completed"
        elif status in {"PARTIAL", "FAILED", "WAITING_FOR_JARVIS", "WAITING_FOR_YOU"}:
            reason = str(
                completed.get("error_summary")
                or completed.get("waiting_reason")
                or "the action could not be verified"
            ).strip()
            response = f"I couldn't complete {self._action_phrase(task)}: {reason}."
            outcome = "partial" if status == "PARTIAL" else "failed"
        else:
            response = (
                f"I accepted that request; {self._action_phrase(task)} is now {status.lower()}."
            )
            outcome = "started"
        return ProposalResolution(
            handled=True,
            response=response,
            intent="action_proposal_accepted",
            task_id=task_id,
            proposal_id=proposal_id,
            action_outcome=outcome,
            tool_called=True,
        )

    @staticmethod
    def _action_phrase(task: Mapping[str, Any]) -> str:
        title = " ".join(str(task.get("title") or "that action").split()).strip()
        return redact_text(title[:200]).casefold()

    @staticmethod
    def _result_summary(task: Mapping[str, Any]) -> str | None:
        value = " ".join(str(task.get("result_summary") or "").split()).strip()
        return redact_text(value[:500]) if value else None


__all__ = ["ActionProposalService", "ProposalResolution"]
