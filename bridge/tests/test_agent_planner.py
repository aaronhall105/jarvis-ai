from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from collections import defaultdict, deque

from app.agent_planner import (
    CapabilityAccess,
    CapabilityExecutionRequest,
    CapabilityExecutionResult,
    CapabilityRequirement,
    CapabilityState,
    ConfirmationStatus,
    DirectExecutionRequired,
    EvidenceRequirement,
    ExecutionStatus,
    PersonalAgentPlanner,
    PlanStatus,
    PlanValidationError,
    ProposedStep,
    RequestRoute,
    RiskLevel,
    SQLitePlanStore,
    StepStatus,
)


def capability(
    capability_id: str,
    *,
    write: bool = False,
    verify: bool = False,
    available: bool = True,
    healthy: bool = True,
    confirmation: bool = False,
) -> CapabilityState:
    return CapabilityState(
        capability_id=capability_id,
        available=available,
        healthy=healthy,
        readable=not write,
        writable=write,
        requires_confirmation=confirmation,
        supports_verification=verify,
    )


def read_step(
    step_id: str,
    capability_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    max_attempts: int = 1,
) -> ProposedStep:
    return ProposedStep(
        step_id=step_id,
        title=f"Read {step_id}",
        capability=CapabilityRequirement(capability_id),
        depends_on=depends_on,
        max_attempts=max_attempts,
    )


def write_step(
    step_id: str,
    capability_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    risk: RiskLevel = RiskLevel.MODERATE,
    evidence: EvidenceRequirement = EvidenceRequirement.VERIFIED,
    max_attempts: int = 1,
) -> ProposedStep:
    return ProposedStep(
        step_id=step_id,
        title=f"Write {step_id}",
        capability=CapabilityRequirement(
            capability_id,
            access=CapabilityAccess.WRITE,
            evidence=evidence,
        ),
        depends_on=depends_on,
        risk=risk,
        max_attempts=max_attempts,
    )


def accepted_result(**payload):
    return CapabilityExecutionResult(
        status=ExecutionStatus.SUCCEEDED,
        result=payload,
        accepted=True,
    )


def verified_write(reference: str):
    return CapabilityExecutionResult(
        status=ExecutionStatus.SUCCEEDED,
        result={"provider_reference": reference},
        accepted=True,
        verified=True,
        action_receipt={"action_id": reference, "status": "verified"},
    )


class FakeExecutor:
    def __init__(self, states=None):
        self.states = dict(states or {})
        self.results = defaultdict(deque)
        self.calls: list[CapabilityExecutionRequest] = []
        self.reconciliations = {}
        self.reconcile_calls: list[CapabilityExecutionRequest] = []
        self.snapshot_calls = 0
        self.delay = 0.0
        self.active_reads = 0
        self.max_active_reads = 0
        self.active_writes = 0
        self.max_active_writes = 0

    async def snapshot(self):
        self.snapshot_calls += 1
        return dict(self.states)

    async def execute(self, request):
        self.calls.append(request)
        is_read = request.access is CapabilityAccess.READ
        if is_read:
            self.active_reads += 1
            self.max_active_reads = max(self.max_active_reads, self.active_reads)
        else:
            self.active_writes += 1
            self.max_active_writes = max(self.max_active_writes, self.active_writes)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            queued = self.results[request.capability_id]
            if not queued:
                raise AssertionError(f"No result queued for {request.capability_id}")
            result = queued.popleft()
            if isinstance(result, BaseException):
                raise result
            return result
        finally:
            if is_read:
                self.active_reads -= 1
            else:
                self.active_writes -= 1

    async def reconcile(self, request):
        self.reconcile_calls.append(request)
        return self.reconciliations.get(request.action_id)


class AgentPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = f"{self.temporary.name}/plans.db"
        self.store = SQLitePlanStore(self.database_path)
        self.executor = FakeExecutor()
        self.planner = PersonalAgentPlanner(self.store, self.executor, max_parallel_reads=3)

    async def asyncTearDown(self):
        self.temporary.cleanup()

    async def create(self, steps, *, continuation=None):
        return await self.planner.create(
            route=RequestRoute.MULTI_STEP,
            conversation_id="conversation-1",
            goal="Complete a useful multi-step goal",
            proposed_steps=steps,
            continuation=continuation,
        )

    async def test_simple_direct_request_is_rejected_without_snapshot_or_plan(self):
        self.executor.states["home.turn_off"] = capability("home.turn_off", write=True)

        with self.assertRaises(DirectExecutionRequired):
            await self.planner.create(
                route=RequestRoute.SIMPLE_DIRECT,
                conversation_id="conversation-1",
                goal="Turn the TV off",
                proposed_steps=[write_step("off", "home.turn_off")],
            )

        self.assertEqual(0, self.executor.snapshot_calls)
        self.assertEqual([], await self.planner.list())

    async def test_dependency_graph_is_validated_and_persisted_in_wal(self):
        for name in ("calendar.read", "web.search", "calendar.create"):
            self.executor.states[name] = capability(
                name,
                write=name == "calendar.create",
                verify=name == "calendar.create",
            )
        plan = await self.create(
            [
                read_step("availability", "calendar.read"),
                read_step("research", "web.search"),
                write_step(
                    "create_event",
                    "calendar.create",
                    depends_on=("availability", "research"),
                ),
            ],
            continuation={"decision": "choose venue"},
        )

        restarted_store = SQLitePlanStore(self.database_path)
        loaded = await restarted_store.get(plan.plan_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(("availability", "research"), loaded.step("create_event").depends_on)
        self.assertEqual({"decision": "choose venue"}, loaded.continuation)
        connection = sqlite3.connect(self.database_path)
        try:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual("wal", mode.casefold())

        with self.assertRaisesRegex(PlanValidationError, "cycle"):
            await self.create(
                [
                    read_step("one", "calendar.read", depends_on=("two",)),
                    read_step("two", "web.search", depends_on=("one",)),
                ]
            )

    async def test_write_steps_cannot_lower_evidence_to_provider_acceptance(self):
        self.executor.states["email.send"] = capability("email.send", write=True, verify=True)
        with self.assertRaisesRegex(PlanValidationError, "Write steps must require verified"):
            await self.create(
                [
                    write_step(
                        "send",
                        "email.send",
                        evidence=EvidenceRequirement.ACCEPTED,
                    )
                ]
            )
        self.assertEqual([], await self.planner.list())

    async def test_plan_inputs_reject_secrets_and_provider_results_are_redacted(self):
        self.executor.states["web.search"] = capability("web.search")
        secret_step = ProposedStep(
            step_id="search",
            title="Search",
            capability=CapabilityRequirement("web.search"),
            arguments={"access_token": "must-not-persist"},
        )
        with self.assertRaisesRegex(PlanValidationError, "credentials or secrets"):
            await self.create([secret_step])

        self.executor.results["web.search"].append(
            CapabilityExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                result={
                    "summary": "Provider evidence",
                    "access_token": "provider-secret",
                },
                accepted=True,
            )
        )
        plan = await self.create([read_step("safe", "web.search")])
        completed = await self.planner.resume(plan.plan_id)
        rendered = repr(completed.as_dict())
        self.assertNotIn("provider-secret", rendered)
        self.assertIn("[REDACTED]", rendered)
        with sqlite3.connect(self.database_path) as connection:
            payload = connection.execute(
                "SELECT payload_json FROM agent_plans WHERE plan_id=?",
                (plan.plan_id,),
            ).fetchone()[0]
        self.assertNotIn("provider-secret", payload)

    async def test_missing_capability_blocks_execution_then_can_resume_when_configured(self):
        plan = await self.create([read_step("search", "web.search")])

        self.assertEqual(PlanStatus.BLOCKED, plan.status)
        self.assertEqual(StepStatus.BLOCKED, plan.step("search").status)
        self.assertEqual("capability_missing", plan.step("search").failure.code)
        blocked = await self.planner.resume(plan.plan_id)
        self.assertEqual(PlanStatus.BLOCKED, blocked.status)
        self.assertEqual([], self.executor.calls)

        self.executor.states["web.search"] = capability("web.search")
        self.executor.results["web.search"].append(accepted_result(results=["real-source"]))
        completed = await self.planner.resume(plan.plan_id)
        self.assertEqual(PlanStatus.COMPLETED, completed.status)
        self.assertEqual(["real-source"], completed.step("search").result["results"])

    async def test_mid_plan_failure_preserves_completed_work_and_resume_retries_only_remaining(
        self,
    ):
        for name in ("calendar.read", "web.search", "research.synthesise"):
            self.executor.states[name] = capability(name)
        self.executor.results["calendar.read"].append(accepted_result(free=True))
        self.executor.results["web.search"].extend(
            [
                CapabilityExecutionResult(
                    status=ExecutionStatus.FAILED,
                    error_code="temporary_provider_failure",
                    error="Search provider unavailable",
                    retryable=True,
                ),
                accepted_result(options=["A", "B"]),
            ]
        )
        self.executor.results["research.synthesise"].append(accepted_result(choice="A"))
        plan = await self.create(
            [
                read_step("calendar", "calendar.read"),
                read_step("search", "web.search", depends_on=("calendar",), max_attempts=2),
                read_step("compare", "research.synthesise", depends_on=("search",)),
            ]
        )

        partial = await self.planner.resume(plan.plan_id)
        self.assertEqual(PlanStatus.PARTIAL, partial.status)
        self.assertEqual(StepStatus.SUCCEEDED, partial.step("calendar").status)
        self.assertEqual(StepStatus.FAILED, partial.step("search").status)
        self.assertEqual(StepStatus.BLOCKED, partial.step("compare").status)
        first_action_id = partial.step("search").action_id

        restarted = PersonalAgentPlanner(SQLitePlanStore(self.database_path), self.executor)
        completed = await restarted.resume(plan.plan_id)
        self.assertEqual(PlanStatus.COMPLETED, completed.status)
        self.assertEqual(1, completed.step("calendar").attempts)
        self.assertEqual(2, completed.step("search").attempts)
        self.assertEqual(first_action_id, completed.step("search").action_id)
        self.assertEqual(1, completed.step("compare").attempts)
        capability_calls = [call.capability_id for call in self.executor.calls]
        self.assertEqual(
            ["calendar.read", "web.search", "web.search", "research.synthesise"],
            capability_calls,
        )

    async def test_dependent_step_resolves_only_explicit_ancestor_evidence(self):
        self.executor.states.update(
            {
                "web.search": capability("web.search"),
                "calendar.create": capability("calendar.create", write=True, verify=True),
            }
        )
        self.executor.results["web.search"].append(
            accepted_result(venues=[{"name": "The Evidence Room"}])
        )
        self.executor.results["calendar.create"].append(verified_write("event-1"))
        plan = await self.create(
            [
                read_step("research", "web.search"),
                ProposedStep(
                    step_id="calendar",
                    title="Create the calendar event",
                    capability=CapabilityRequirement(
                        "calendar.create",
                        CapabilityAccess.WRITE,
                        EvidenceRequirement.VERIFIED,
                    ),
                    arguments={
                        "title": {
                            "$from_step": "research",
                            "path": "venues.0.name",
                        }
                    },
                    depends_on=("research",),
                ),
            ]
        )

        completed = await self.planner.resume(plan.plan_id)

        self.assertEqual(PlanStatus.COMPLETED, completed.status)
        calendar_call = next(call for call in self.executor.calls if call.step_id == "calendar")
        self.assertEqual({"title": "The Evidence Room"}, dict(calendar_call.arguments))
        self.assertEqual(
            {"$from_step": "research", "path": "venues.0.name"},
            completed.step("calendar").arguments["title"],
        )

    async def test_step_result_reference_must_target_an_ancestor(self):
        self.executor.states.update(
            {
                "web.search": capability("web.search"),
                "calendar.create": capability("calendar.create", write=True, verify=True),
            }
        )
        with self.assertRaisesRegex(PlanValidationError, "ancestor"):
            await self.create(
                [
                    read_step("research", "web.search"),
                    ProposedStep(
                        step_id="calendar",
                        title="Create calendar event",
                        capability=CapabilityRequirement(
                            "calendar.create",
                            CapabilityAccess.WRITE,
                            EvidenceRequirement.VERIFIED,
                        ),
                        arguments={
                            "title": {
                                "$from_step": "research",
                                "path": "venues.0.name",
                            }
                        },
                    ),
                ]
            )

    async def test_safe_replan_preserves_completed_evidence_and_replaces_unstarted_work(self):
        self.executor.states["web.search"] = capability("web.search")
        self.executor.results["web.search"].append(
            accepted_result(venues=[{"name": "Evidence Cafe"}])
        )
        plan = await self.create(
            [
                read_step("research", "web.search"),
                write_step(
                    "calendar",
                    "calendar.create",
                    depends_on=("research",),
                ),
            ]
        )
        partial = await self.planner.resume(plan.plan_id)
        self.assertEqual(PlanStatus.PARTIAL, partial.status)
        self.assertEqual(StepStatus.SUCCEEDED, partial.step("research").status)

        self.executor.states["calendar.alternative"] = capability(
            "calendar.alternative", write=True, verify=True
        )
        self.executor.results["calendar.alternative"].append(verified_write("event-alternative"))
        replanned = await self.planner.replan(
            plan.plan_id,
            proposed_steps=[
                read_step("research", "web.search"),
                write_step(
                    "calendar_alternative",
                    "calendar.alternative",
                    depends_on=("research",),
                ),
            ],
        )
        completed = await self.planner.resume(replanned.plan_id)

        self.assertEqual(PlanStatus.COMPLETED, completed.status)
        self.assertEqual(1, completed.step("research").attempts)
        self.assertEqual(
            ["web.search", "calendar.alternative"],
            [call.capability_id for call in self.executor.calls],
        )

    async def test_replan_cannot_rewrite_completed_or_uncertain_history(self):
        self.executor.states["web.search"] = capability("web.search")
        self.executor.results["web.search"].append(accepted_result(found=True))
        plan = await self.create(
            [
                read_step("research", "web.search"),
                write_step("blocked", "calendar.create", depends_on=("research",)),
            ]
        )
        await self.planner.resume(plan.plan_id)

        with self.assertRaisesRegex(PlanValidationError, "remain unchanged"):
            await self.planner.replan(
                plan.plan_id,
                proposed_steps=[
                    ProposedStep(
                        step_id="research",
                        title="Rewrite completed evidence",
                        capability=CapabilityRequirement("web.search"),
                    ),
                    write_step("replacement", "calendar.create"),
                ],
            )

    async def test_verified_write_records_result_receipt_and_completion(self):
        self.executor.states["calendar.create"] = capability(
            "calendar.create", write=True, verify=True
        )
        self.executor.results["calendar.create"].append(verified_write("event-123"))
        plan = await self.create([write_step("event", "calendar.create")])

        completed = await self.planner.resume(plan.plan_id)

        step = completed.step("event")
        self.assertEqual(PlanStatus.COMPLETED, completed.status)
        self.assertEqual(StepStatus.SUCCEEDED, step.status)
        self.assertEqual("event-123", step.result["provider_reference"])
        self.assertEqual("event-123", step.action_receipt["action_id"])
        self.assertEqual(step.action_id, self.executor.calls[0].idempotency_key)

    async def test_unsafe_write_pauses_for_explicit_approval(self):
        self.executor.states.update(
            {
                "web.search": capability("web.search"),
                "email.send": capability("email.send", write=True, verify=True),
            }
        )
        self.executor.results["web.search"].append(accepted_result(summary="evidence"))
        self.executor.results["email.send"].append(verified_write("message-1"))
        plan = await self.create(
            [
                read_step("research", "web.search"),
                write_step(
                    "send",
                    "email.send",
                    depends_on=("research",),
                    risk=RiskLevel.HIGH,
                ),
            ]
        )

        paused = await self.planner.resume(plan.plan_id)
        self.assertEqual(PlanStatus.AWAITING_APPROVAL, paused.status)
        self.assertEqual(StepStatus.SUCCEEDED, paused.step("research").status)
        self.assertEqual(StepStatus.AWAITING_APPROVAL, paused.step("send").status)
        self.assertEqual(ConfirmationStatus.PENDING, paused.step("send").confirmation_status)
        self.assertEqual(["web.search"], [call.capability_id for call in self.executor.calls])

        approved = await self.planner.approve(plan.plan_id, "send")
        self.assertEqual(ConfirmationStatus.APPROVED, approved.step("send").confirmation_status)
        completed = await self.planner.resume(plan.plan_id)
        self.assertEqual(PlanStatus.COMPLETED, completed.status)
        self.assertEqual(
            ["web.search", "email.send"],
            [call.capability_id for call in self.executor.calls],
        )

    async def test_provider_confirmation_policy_cannot_be_lowered_by_proposed_step(self):
        self.executor.states["social.publish"] = capability(
            "social.publish", write=True, verify=True, confirmation=True
        )
        plan = await self.create([write_step("publish", "social.publish")])

        paused = await self.planner.resume(plan.plan_id)

        self.assertTrue(paused.step("publish").required_confirmation)
        self.assertEqual(PlanStatus.AWAITING_APPROVAL, paused.status)
        self.assertEqual([], self.executor.calls)

    async def test_success_without_evidence_cannot_complete_a_read(self):
        self.executor.states["web.search"] = capability("web.search")
        self.executor.results["web.search"].append(
            CapabilityExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                result={"claim": "latest price is 10"},
            )
        )
        plan = await self.create([read_step("current_price", "web.search")])

        failed = await self.planner.resume(plan.plan_id)

        self.assertEqual(PlanStatus.FAILED, failed.status)
        self.assertEqual(StepStatus.FAILED, failed.step("current_price").status)
        self.assertEqual("required_evidence_missing", failed.step("current_price").failure.code)
        self.assertFalse(failed.completed)

    async def test_malformed_string_failure_status_cannot_be_treated_as_success(self):
        self.executor.states["web.search"] = capability("web.search")
        self.executor.results["web.search"].append(
            CapabilityExecutionResult(
                status="failed",
                result={"claim": "untrusted"},
                accepted=True,
            )
        )
        plan = await self.create([read_step("search", "web.search")])

        failed = await self.planner.resume(plan.plan_id)

        self.assertEqual(PlanStatus.FAILED, failed.status)
        self.assertEqual(StepStatus.FAILED, failed.step("search").status)
        self.assertEqual("invalid_executor_result", failed.step("search").failure.code)

    async def test_successful_write_without_receipt_is_outcome_unknown_and_never_retried(self):
        self.executor.states["social.publish"] = capability(
            "social.publish", write=True, verify=True
        )
        self.executor.results["social.publish"].append(
            CapabilityExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                result={"post": "maybe-created"},
                accepted=True,
                verified=True,
                action_receipt=None,
            )
        )
        plan = await self.create([write_step("publish", "social.publish", max_attempts=3)])

        blocked = await self.planner.resume(plan.plan_id)
        self.assertEqual(PlanStatus.BLOCKED, blocked.status)
        self.assertEqual(StepStatus.OUTCOME_UNKNOWN, blocked.step("publish").status)
        self.assertEqual("action_receipt_missing", blocked.step("publish").failure.code)
        resumed = await self.planner.resume(plan.plan_id)
        self.assertEqual(StepStatus.OUTCOME_UNKNOWN, resumed.step("publish").status)
        self.assertEqual(1, len(self.executor.calls))

    async def test_write_with_unverified_receipt_cannot_complete(self):
        self.executor.states["email.send"] = capability("email.send", write=True, verify=True)
        self.executor.results["email.send"].append(
            CapabilityExecutionResult(
                status=ExecutionStatus.SUCCEEDED,
                result={"message_id": "provider-message-1"},
                accepted=True,
                verified=True,
                action_receipt={"action_id": "receipt-1", "status": "accepted_unverified"},
            )
        )
        plan = await self.create([write_step("send", "email.send")])

        blocked = await self.planner.resume(plan.plan_id)

        self.assertEqual(StepStatus.OUTCOME_UNKNOWN, blocked.step("send").status)
        self.assertEqual("action_receipt_not_verified", blocked.step("send").failure.code)
        resumed = await self.planner.resume(plan.plan_id)
        self.assertEqual(StepStatus.OUTCOME_UNKNOWN, resumed.step("send").status)
        self.assertEqual(1, len(self.executor.calls))

    async def test_executor_exception_on_write_is_not_automatically_retried(self):
        self.executor.states["calendar.create"] = capability(
            "calendar.create", write=True, verify=True
        )
        self.executor.results["calendar.create"].append(TimeoutError("timed out"))
        plan = await self.create([write_step("create", "calendar.create", max_attempts=3)])

        blocked = await self.planner.resume(plan.plan_id)
        resumed = await self.planner.resume(plan.plan_id)

        self.assertEqual(StepStatus.OUTCOME_UNKNOWN, blocked.step("create").status)
        self.assertEqual(StepStatus.OUTCOME_UNKNOWN, resumed.step("create").status)
        self.assertEqual(1, len(self.executor.calls))

    async def test_interrupted_write_reconciles_durable_receipt_without_provider_retry(self):
        self.executor.states["calendar.create"] = capability(
            "calendar.create", write=True, verify=True
        )
        plan = await self.create([write_step("create", "calendar.create")])
        interrupted = await self.store.get(plan.plan_id)
        assert interrupted is not None
        step = interrupted.step("create")
        step.status = StepStatus.RUNNING
        step.attempts = 1
        self.executor.reconciliations[step.action_id] = verified_write("event-1")
        await self.store.save(interrupted)

        recovered = await self.planner.resume(plan.plan_id)

        self.assertEqual(PlanStatus.COMPLETED, recovered.status)
        self.assertEqual(StepStatus.SUCCEEDED, recovered.step("create").status)
        self.assertEqual("verified", recovered.step("create").action_receipt["status"])
        self.assertEqual([], self.executor.calls)
        self.assertEqual(1, len(self.executor.reconcile_calls))

    async def test_independent_reads_are_bounded_concurrent_and_writes_are_serial(self):
        for name in ("read.one", "read.two", "write.one", "write.two"):
            is_write = name.startswith("write")
            self.executor.states[name] = capability(name, write=is_write, verify=is_write)
            self.executor.results[name].append(
                verified_write(name) if is_write else accepted_result(value=name)
            )
        self.executor.delay = 0.02
        plan = await self.create(
            [
                read_step("read_one", "read.one"),
                read_step("read_two", "read.two"),
                write_step("write_one", "write.one", depends_on=("read_one",)),
                write_step("write_two", "write.two", depends_on=("read_two",)),
            ]
        )

        completed = await self.planner.resume(plan.plan_id)

        self.assertEqual(PlanStatus.COMPLETED, completed.status)
        self.assertEqual(2, self.executor.max_active_reads)
        self.assertEqual(1, self.executor.max_active_writes)

    async def test_cancel_preserves_completed_steps_and_stops_remaining_work(self):
        self.executor.states.update(
            {
                "web.search": capability("web.search"),
                "email.send": capability("email.send", write=True, verify=True),
            }
        )
        self.executor.results["web.search"].append(accepted_result(found=True))
        plan = await self.create(
            [
                read_step("research", "web.search"),
                write_step(
                    "send",
                    "email.send",
                    depends_on=("research",),
                    risk=RiskLevel.HIGH,
                ),
            ]
        )
        await self.planner.resume(plan.plan_id)

        cancelled = await self.planner.cancel(plan.plan_id)
        resumed = await self.planner.resume(plan.plan_id)

        self.assertEqual(PlanStatus.CANCELLED, cancelled.status)
        self.assertEqual(StepStatus.SUCCEEDED, cancelled.step("research").status)
        self.assertEqual(StepStatus.CANCELLED, cancelled.step("send").status)
        self.assertEqual(PlanStatus.CANCELLED, resumed.status)
        self.assertEqual(1, len(self.executor.calls))


if __name__ == "__main__":
    unittest.main()
