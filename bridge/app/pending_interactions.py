"""Durable, principal-scoped conversational interactions.

Pending interactions represent questions whose answer is needed before Jarvis
can continue.  They are persisted in the existing dialogue store and projected
into Task Centre; executable actions continue to use the planner-backed
``ActionProposalService`` so clarification never becomes execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Sequence
from uuid import uuid4

from app.connectors.credentials import redact_secrets, redact_text
from app.dialogue_manager import DialogueManager, bare_confirmation, normalized_command
from app.working_context import merge_projection


_PENDING_GOAL = "pending_interaction"


class PendingInteractionKind(str, Enum):
    CLARIFICATION = "clarification"
    SELECTION = "selection"
    ACTION_PROPOSAL = "action_proposal"
    AUTHORITY_CONFIRMATION = "authority_confirmation"
    MISSING_INFORMATION = "missing_information"


InteractionHandler = Callable[[Mapping[str, Any], str, str], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class PendingInteractionResolution:
    handled: bool
    kind: str
    response: str | None = None
    result: Mapping[str, Any] | None = None
    interaction_id: str | None = None
    answer: str | None = None


class PendingInteractionService:
    """Persist and resolve generic conversational questions before prose."""

    def __init__(
        self,
        *,
        dialogue: DialogueManager,
        action_proposals: Any,
        ttl_seconds: int = 600,
    ) -> None:
        self.dialogue = dialogue
        self.action_proposals = action_proposals
        self.ttl_seconds = max(60, min(int(ttl_seconds), 86_400))
        self._runtime_id = str(uuid4())
        self._handlers: dict[str, InteractionHandler] = {}

    def register_handler(self, handler_id: str, handler: InteractionHandler) -> None:
        identifier = str(handler_id or "").strip()
        if not identifier or not callable(handler):
            raise ValueError("A pending interaction handler requires an identifier")
        self._handlers[identifier] = handler

    @staticmethod
    def _scoped_conversation(conversation_id: str, principal_id: str) -> str:
        conversation = str(conversation_id or "").strip()
        principal = str(principal_id or "").strip()
        if not conversation or not principal:
            raise ValueError("A pending interaction requires principal and conversation")
        if conversation.startswith("usr:"):
            if not conversation.startswith(f"usr:{principal}:"):
                raise ValueError("Pending interaction principal mismatch")
            return conversation
        return f"usr:{principal}:{conversation}"

    @staticmethod
    def _safe_options(options: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for index, raw in enumerate(options[:25]):
            option_id = str(raw.get("option_id") or f"option-{index + 1}").strip()
            label = " ".join(str(raw.get("label") or "").split()).strip()
            if not option_id or not label:
                continue
            output.append(
                {
                    "option_id": option_id[:120],
                    "label": redact_text(label[:200]),
                    "value": redact_secrets(raw.get("value")),
                    "evidence": redact_secrets(raw.get("evidence")),
                }
            )
        return output

    async def begin(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        kind: PendingInteractionKind | str,
        goal: str,
        prompt: str,
        unresolved_slot: str,
        handler_id: str,
        context: Mapping[str, Any] | None = None,
        options: Sequence[Mapping[str, Any]] = (),
        proposed_value: Any = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        principal = str(principal_id or "").strip()
        conversation = self._scoped_conversation(conversation_id, principal)
        interaction_kind = (
            kind if isinstance(kind, PendingInteractionKind) else PendingInteractionKind(str(kind))
        )
        if interaction_kind in {
            PendingInteractionKind.ACTION_PROPOSAL,
            PendingInteractionKind.AUTHORITY_CONFIRMATION,
        }:
            raise ValueError("Executable authority must use the planner-backed proposal path")
        safe_goal = " ".join(str(goal or "").split()).strip()
        safe_prompt = " ".join(str(prompt or "").split()).strip()
        slot = str(unresolved_slot or "").strip()
        handler = str(handler_id or "").strip()
        if not safe_goal or not safe_prompt or not slot or not handler:
            raise ValueError("A pending interaction requires goal, prompt, slot and handler")
        if handler not in self._handlers:
            raise ValueError("The pending interaction handler is not registered")
        existing = await self.dialogue.get(conversation)
        if existing.active_goal and existing.active_goal != _PENDING_GOAL:
            raise ValueError("Another structured dialogue goal is already active")
        if existing.active_goal == _PENDING_GOAL:
            await self.dialogue.clear_goal(conversation, outcome="superseded")
        interaction_id = str(uuid4())
        record = {
            "version": 1,
            "interaction_id": interaction_id,
            "kind": interaction_kind.value,
            "principal_id": principal,
            "conversation_id": conversation,
            "goal": redact_text(safe_goal[:500]),
            "prompt": redact_text(safe_prompt[:1_000]),
            "unresolved_slot": slot[:120],
            "handler_id": handler,
            "context": dict(redact_secrets(dict(context or {}))),
            "options": self._safe_options(options),
            "proposed_value": redact_secrets(proposed_value),
            "status": "pending",
        }
        await self.dialogue.begin_goal(
            conversation,
            _PENDING_GOAL,
            status="awaiting_slot",
            slots={"pending_interaction": record},
            missing_slots=(slot,),
            prompt=record["prompt"],
            ttl_seconds=ttl_seconds or self.ttl_seconds,
        )
        state = await self.dialogue.get(conversation)
        state.working_context = merge_projection(
            state.working_context,
            principal_id=principal,
            conversation_id=conversation,
            objects=(),
            goal=record["goal"],
            active_interaction_id=interaction_id,
        )
        await self.dialogue.save(
            state,
            "pending_interaction_context_linked",
            {"interaction_id": interaction_id},
        )
        return {
            **record,
            "created_at": state.updated_at,
            "updated_at": state.updated_at,
            "expires_at": state.goal_expires_at,
            "task_id": f"interaction:{interaction_id}",
        }

    @staticmethod
    def _record_from_state(state: Any) -> dict[str, Any] | None:
        if getattr(state, "active_goal", None) != _PENDING_GOAL:
            return None
        slots = getattr(state, "slots", {})
        raw = slots.get("pending_interaction") if isinstance(slots, Mapping) else None
        if not isinstance(raw, Mapping):
            return None
        record = dict(raw)
        record.update(
            created_at=getattr(state, "created_at", None),
            updated_at=getattr(state, "updated_at", None),
            expires_at=getattr(state, "goal_expires_at", None),
        )
        return record

    async def current(self, *, principal_id: str, conversation_id: str) -> dict[str, Any] | None:
        principal = str(principal_id or "").strip()
        try:
            conversation = self._scoped_conversation(conversation_id, principal)
        except ValueError:
            return None
        record = self._record_from_state(await self.dialogue.get(conversation))
        if record is None:
            return None
        if (
            str(record.get("principal_id") or "") != principal
            or str(record.get("conversation_id") or "") != conversation
        ):
            return None
        return record

    async def list_for_principal(
        self, *, principal_id: str, limit: int = 250
    ) -> list[dict[str, Any]]:
        states = await self.dialogue.list_active_goals(
            goal=_PENDING_GOAL,
            principal_id=principal_id,
            limit=limit,
        )
        return [record for state in states if (record := self._record_from_state(state))]

    @staticmethod
    def _match_option(
        answer: str, options: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any] | None:
        command = normalized_command(answer)
        ordinals = {
            "first": 0,
            "the first one": 0,
            "second": 1,
            "the second one": 1,
            "third": 2,
            "the third one": 2,
        }
        index = ordinals.get(command or "")
        if index is not None:
            return options[index] if index < len(options) else None
        matches = [
            item
            for item in options
            if command
            in {
                normalized_command(str(item.get("option_id") or "")),
                normalized_command(str(item.get("label") or "")),
            }
        ]
        return matches[0] if len(matches) == 1 else None

    async def _claim(
        self,
        *,
        conversation_id: str,
        interaction_id: str,
        request_id: str,
        answer: str,
    ) -> bool:
        state = await self.dialogue.get(conversation_id)
        record = self._record_from_state(state)
        if record is None or str(record.get("interaction_id") or "") != interaction_id:
            return False
        if str(record.get("status") or "pending") != "pending":
            return False
        record.update(
            status="resolving",
            resolution_request_id=request_id,
            resolution_runtime_id=self._runtime_id,
            answer=answer,
        )
        state.slots = {**state.slots, "pending_interaction": record}
        await self.dialogue.save(
            state,
            "pending_interaction_claimed",
            {"interaction_id": interaction_id, "kind": record.get("kind")},
        )
        return True

    async def _finish(self, *, conversation_id: str, interaction_id: str, outcome: str) -> None:
        state = await self.dialogue.get(conversation_id)
        record = self._record_from_state(state)
        if record is not None and str(record.get("interaction_id") or "") == interaction_id:
            state.working_context = merge_projection(
                state.working_context,
                principal_id=str(record.get("principal_id") or ""),
                conversation_id=conversation_id,
                objects=(),
                active_interaction_id="",
            )
            await self.dialogue.save(
                state,
                "pending_interaction_context_cleared",
                {"interaction_id": interaction_id, "outcome": outcome},
            )
            await self.dialogue.clear_goal(conversation_id, outcome=outcome)

    async def resolve(
        self,
        *,
        principal_id: str,
        conversation_id: str,
        answer: str,
        request_id: str,
    ) -> PendingInteractionResolution | None:
        try:
            conversation = self._scoped_conversation(conversation_id, principal_id)
        except ValueError:
            return None
        dialogue_state = await self.dialogue.get(conversation)
        if dialogue_state.active_goal and dialogue_state.active_goal != _PENDING_GOAL:
            return None
        record = await self.current(
            principal_id=principal_id,
            conversation_id=conversation,
        )
        confirmation = bare_confirmation(answer)
        if record is None:
            if confirmation:
                proposal = await self.action_proposals.resolve(
                    principal_id=principal_id,
                    conversation_id=conversation,
                    confirmation=confirmation,
                    request_id=request_id,
                )
                if proposal is not None and proposal.handled:
                    return PendingInteractionResolution(
                        handled=True,
                        kind=PendingInteractionKind.ACTION_PROPOSAL.value,
                        response=proposal.response,
                        result=proposal.as_result(),
                        interaction_id=proposal.proposal_id,
                    )
            return None

        interaction_id = str(record.get("interaction_id") or "")
        status = str(record.get("status") or "pending")
        if (
            status == "resolving"
            and str(record.get("resolution_runtime_id") or "") != self._runtime_id
        ):
            # Non-authoritative interactions may only continue reads or fill
            # slots. Replaying one after a process crash is safe; executable
            # authority is deliberately handled by ActionProposal receipts.
            record.update(status="pending")
            dialogue_state.slots = {
                **dialogue_state.slots,
                "pending_interaction": record,
            }
            await self.dialogue.save(
                dialogue_state,
                "pending_interaction_recovered",
                {"interaction_id": interaction_id},
            )
            status = "pending"
        if status != "pending":
            return PendingInteractionResolution(
                handled=True,
                kind=str(record.get("kind") or "clarification"),
                response="I’m already applying that answer.",
                interaction_id=interaction_id,
            )
        if confirmation == "negative":
            await self._finish(
                conversation_id=conversation,
                interaction_id=interaction_id,
                outcome="declined",
            )
            return PendingInteractionResolution(
                handled=True,
                kind=str(record.get("kind") or "clarification"),
                response="Okay — I won’t continue that request.",
                interaction_id=interaction_id,
            )

        resolved_answer = " ".join(str(answer or "").split()).strip()
        options = [item for item in record.get("options") or () if isinstance(item, Mapping)]
        selected: Mapping[str, Any] | None = None
        if confirmation == "affirmative" and record.get("proposed_value") is not None:
            resolved_answer = str(record["proposed_value"])
        elif options:
            selected = self._match_option(resolved_answer, options)
            if selected is None:
                labels = " or ".join(str(item.get("label") or "that option") for item in options)
                return PendingInteractionResolution(
                    handled=True,
                    kind=str(record.get("kind") or "selection"),
                    response=f"Which one do you mean — {labels}?",
                    interaction_id=interaction_id,
                )
            resolved_answer = str(selected.get("value") or selected.get("label") or "").strip()
        elif confirmation == "affirmative":
            return PendingInteractionResolution(
                handled=True,
                kind=str(record.get("kind") or "clarification"),
                response=str(record.get("prompt") or "Please clarify that choice."),
                interaction_id=interaction_id,
            )
        if not resolved_answer:
            return PendingInteractionResolution(
                handled=True,
                kind=str(record.get("kind") or "missing_information"),
                response=str(record.get("prompt") or "Please provide the missing detail."),
                interaction_id=interaction_id,
            )

        if not await self._claim(
            conversation_id=conversation,
            interaction_id=interaction_id,
            request_id=request_id,
            answer=resolved_answer,
        ):
            return PendingInteractionResolution(
                handled=True,
                kind=str(record.get("kind") or "clarification"),
                response="That answer has already been handled.",
                interaction_id=interaction_id,
            )
        handler = self._handlers.get(str(record.get("handler_id") or ""))
        if handler is None:
            await self._finish(
                conversation_id=conversation,
                interaction_id=interaction_id,
                outcome="invalid",
            )
            return PendingInteractionResolution(
                handled=True,
                kind=str(record.get("kind") or "clarification"),
                response="I can’t safely continue that clarification.",
                interaction_id=interaction_id,
            )
        try:
            result = dict(await handler(record, resolved_answer, request_id))
        except Exception:
            state = await self.dialogue.get(conversation)
            current = self._record_from_state(state)
            if current and str(current.get("interaction_id") or "") == interaction_id:
                current.update(status="pending")
                state.slots = {**state.slots, "pending_interaction": current}
                await self.dialogue.save(
                    state,
                    "pending_interaction_failed",
                    {"interaction_id": interaction_id},
                )
            raise
        if result.get("interaction_pending") is True:
            state = await self.dialogue.get(conversation)
            current = self._record_from_state(state)
            if current and str(current.get("interaction_id") or "") == interaction_id:
                current.update(
                    status="pending",
                    prompt=str(result.get("response") or current.get("prompt") or "").strip(),
                )
                state.prompt = str(current["prompt"])
                state.slots = {**state.slots, "pending_interaction": current}
                await self.dialogue.save(
                    state,
                    "pending_interaction_waiting",
                    {"interaction_id": interaction_id},
                )
        else:
            await self._finish(
                conversation_id=conversation,
                interaction_id=interaction_id,
                outcome="completed",
            )
        return PendingInteractionResolution(
            handled=True,
            kind=str(record.get("kind") or "clarification"),
            response=str(result.get("response") or "").strip() or None,
            result=result,
            interaction_id=interaction_id,
            answer=resolved_answer,
        )


__all__ = [
    "PendingInteractionKind",
    "PendingInteractionResolution",
    "PendingInteractionService",
]
