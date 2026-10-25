"""Workflow tests on Temporal's time-skipping test server, with fake activities."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import timedelta

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHandle, WorkflowUpdateFailedError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.domain.models import ActionResult, Approval, Decision, Step, Ticket, TicketContext, TicketState
from app.services.repository import Repository
from app.temporal.activities import ActionRequest
from app.temporal.workflow import TicketWorkflow

REPO = Repository()
REPLY = Decision(category="account", priority="normal", action="reply", message="Reset it.", article_ids=["KB-1"])
REFUND = Decision(category="billing", priority="normal", action="refund", invoice_id="INV-1002", amount_eur=49.0)


@pytest.fixture(scope="module")
async def env():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        yield env


class FakeActivities:
    """Stand-ins for the real activities, registered under the same names."""

    def __init__(self, decision: Decision | Callable[[], Decision], action_failures: int = 0) -> None:
        self._decision = decision
        self.action_failures = action_failures
        self.action_attempts = 0
        self.actions: list[ActionRequest] = []

    @activity.defn(name="load_context")
    async def load_context(self, ticket: Ticket) -> TicketContext:
        customer = REPO.get_customer(ticket.customer_id)
        if customer is None:
            raise ApplicationError(f"unknown customer {ticket.customer_id}", non_retryable=True)
        return TicketContext(
            customer=customer, invoices=REPO.get_invoices(customer.customer_id), kb_article_ids=REPO.kb_article_ids()
        )

    @activity.defn(name="decide")
    async def decide(self, ticket: Ticket) -> Decision:
        return self._decision() if callable(self._decision) else self._decision

    @activity.defn(name="execute_action")
    async def execute_action(self, request: ActionRequest) -> ActionResult:
        self.action_attempts += 1
        if self.action_attempts <= self.action_failures:
            raise RuntimeError("helpdesk temporarily unavailable")
        self.actions.append(request)
        return ActionResult(action=request.decision.action, helpdesk_reference=f"HD-{self.action_attempts}")


async def run_ticket(env, fakes: FakeActivities, ticket: Ticket, interact=None) -> TicketState:
    task_queue = f"test-{uuid.uuid4()}"
    async with Worker(
        env.client,
        task_queue=task_queue,
        workflows=[TicketWorkflow],
        activities=[fakes.load_context, fakes.decide, fakes.execute_action],
    ):
        handle = await env.client.start_workflow(
            TicketWorkflow.run, ticket, id=f"ticket-{ticket.ticket_id}-{uuid.uuid4()}", task_queue=task_queue
        )
        if interact:
            await interact(handle)
        return await handle.result()


async def wait_for_step(handle: WorkflowHandle, step: Step) -> None:
    for _ in range(100):
        if (await handle.query(TicketWorkflow.get_state)).step == step:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow never reached {step}")


async def test_ticket_is_answered(env, example_tickets):
    fakes = FakeActivities(REPLY)
    state = await run_ticket(env, fakes, example_tickets["T-1"])
    assert state.step == Step.COMPLETED
    assert state.decision == REPLY
    assert state.result.action == "reply"
    assert len(fakes.actions) == 1


async def test_refund_is_approved(env, example_tickets):
    async def approve(handle):
        await wait_for_step(handle, Step.AWAITING_APPROVAL)
        await handle.execute_update(TicketWorkflow.approve, Approval(approved=True, reviewer="anna"))

    fakes = FakeActivities(REFUND)
    state = await run_ticket(env, fakes, example_tickets["T-2"], approve)
    assert state.result.action == "refund"
    assert state.approval.reviewer == "anna"
    assert fakes.actions[0].decision == REFUND


async def test_refund_is_rejected_and_escalated(env, example_tickets):
    async def reject(handle):
        await wait_for_step(handle, Step.AWAITING_APPROVAL)
        await handle.execute_update(TicketWorkflow.approve, Approval(approved=False, reviewer="anna", comment="no"))

    state = await run_ticket(env, FakeActivities(REFUND), example_tickets["T-2"], reject)
    assert state.result.action == "escalate"
    assert "rejected by anna" in state.decision.reason


async def test_refund_without_approval_is_escalated_after_24_hours(env, example_tickets):
    async def wait_25_hours(handle):
        await wait_for_step(handle, Step.AWAITING_APPROVAL)
        await env.sleep(timedelta(hours=25))

    state = await run_ticket(env, FakeActivities(REFUND), example_tickets["T-2"], wait_25_hours)
    assert state.result.action == "escalate"
    assert "not approved within 24 hours" in state.decision.reason


async def test_approval_is_refused_when_not_waiting(env, example_tickets):
    release = asyncio.Event()

    class SlowDecide(FakeActivities):
        @activity.defn(name="decide")
        async def decide(self, ticket: Ticket) -> Decision:
            await release.wait()
            return REFUND

    async def approve_too_early(handle):
        await wait_for_step(handle, Step.DECIDING)
        with pytest.raises(WorkflowUpdateFailedError) as info:
            await handle.execute_update(TicketWorkflow.approve, Approval(approved=True))
        assert "not waiting for approval" in str(info.value.cause)
        release.set()
        await env.sleep(timedelta(hours=25))

    state = await run_ticket(env, SlowDecide(REFUND), example_tickets["T-2"], approve_too_early)
    assert state.approval is None  # the early approval was not stored
    assert state.result.action == "escalate"


async def test_failing_activity_succeeds_after_retry(env, example_tickets):
    fakes = FakeActivities(REPLY, action_failures=2)
    state = await run_ticket(env, fakes, example_tickets["T-1"])
    assert state.step == Step.COMPLETED
    assert fakes.action_attempts == 3
    assert len(fakes.actions) == 1
    # every attempt used the same idempotency key
    assert fakes.actions[0].idempotency_key.endswith(":reply")


async def test_activity_failing_past_retry_limit_fails_with_reason(env, example_tickets):
    fakes = FakeActivities(REPLY, action_failures=100)
    with pytest.raises(WorkflowFailureError) as info:
        await run_ticket(env, fakes, example_tickets["T-1"])
    assert "helpdesk action failed" in str(info.value.cause)
    assert fakes.action_attempts == 6  # retry limit, not forever


async def test_unknown_customer_is_escalated(env, example_tickets):
    fakes = FakeActivities(REPLY)
    state = await run_ticket(env, fakes, example_tickets["T-7"])
    assert state.result.action == "escalate"
    assert "unknown customer C-999" in state.decision.reason
    assert state.proposed_decision is None  # the agent was never asked


async def test_decision_breaking_rules_is_escalated(env, example_tickets):
    injected = REFUND.model_copy(update={"invoice_id": "INV-1001", "amount_eur": 5000.0})
    fakes = FakeActivities(injected)
    state = await run_ticket(env, fakes, example_tickets["T-6"])
    assert state.proposed_decision == injected
    assert state.validation_errors
    assert state.result.action == "escalate"  # no approval step, no refund
    assert [a.decision.action for a in fakes.actions] == ["escalate"]


async def test_agent_error_that_cannot_be_retried_is_escalated(env, example_tickets):
    def unreadable() -> Decision:
        raise ApplicationError("model gave no valid decision", non_retryable=True)

    state = await run_ticket(env, FakeActivities(unreadable), example_tickets["T-5"])
    assert state.result.action == "escalate"
    assert "Agent could not decide" in state.decision.reason
