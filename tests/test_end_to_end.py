"""The real gateway, workflow and activities (fake LLM, flaky fake Helpdesk) on the test server."""

import uuid
from collections import Counter
from datetime import timedelta

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.domain.models import Approval, Step
from app.services.helpdesk import FakeHelpdesk
from app.services.llm.fake import FakeLLM
from app.services.repository import Repository
from app.temporal.activities import TicketActivities
from app.temporal.gateway import TemporalTicketGateway
from app.temporal.workflow import TicketWorkflow

EXPECTED_ACTIONS = {
    "T-1": "reply",
    "T-2": "refund",
    "T-3": "escalate",
    "T-4": "reply",
    "T-5": "close",
    "T-6": "escalate",  # injected 5000 EUR refund rejected by the rules
    "T-7": "escalate",  # unknown customer
}


class FailTwiceHelpdesk(FakeHelpdesk):
    """Each action first times out *after* it was recorded, then fails before it, then works."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()

    def _roll(self, idempotency_key: str) -> str | None:
        self.calls[idempotency_key] += 1
        return {1: "after", 2: "before"}.get(self.calls[idempotency_key])


@pytest.fixture
async def system():
    async with await WorkflowEnvironment.start_time_skipping(data_converter=pydantic_data_converter) as env:
        task_queue = f"e2e-{uuid.uuid4()}"
        helpdesk = FailTwiceHelpdesk()
        activities = TicketActivities(Repository(), FakeLLM(), helpdesk)
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[TicketWorkflow],
            activities=[activities.load_context, activities.decide, activities.execute_action],
        ):
            yield TemporalTicketGateway(env.client, task_queue), env, helpdesk


async def wait_until(env, gateway, ticket_id, step):
    for _ in range(200):
        view = await gateway.get(ticket_id)
        if view.state.step == step:
            return view
        await env.sleep(timedelta(seconds=1))  # skips time, so retry back-offs pass quickly
    raise AssertionError(f"{ticket_id} never reached {step}: {view.state}")


async def test_example_tickets_end_to_end(system, example_tickets):
    gateway, env, helpdesk = system

    for ticket in example_tickets.values():
        assert (await gateway.start(ticket)).created
    # Sending the same ticket again does not start a second workflow.
    assert not (await gateway.start(example_tickets["T-1"])).created

    await wait_until(env, gateway, "T-2", Step.AWAITING_APPROVAL)
    await gateway.approve("T-2", Approval(approved=True, reviewer="anna"))

    for ticket_id, action in EXPECTED_ACTIONS.items():
        handle = env.client.get_workflow_handle_for(TicketWorkflow.run, f"ticket-{ticket_id}")
        state = await handle.result()
        assert state.step == Step.COMPLETED, ticket_id
        assert state.result.action == action, (ticket_id, state)

    # Every action was attempted 3 times (one failure after the operation), yet ran exactly once.
    assert set(helpdesk.calls.values()) == {3}
    performed = sorted(op.details["ticket_id"] for op in helpdesk.operations)
    assert performed == sorted(EXPECTED_ACTIONS)
    assert [op.details["invoice_id"] for op in helpdesk.operations if op.action == "refund"] == ["INV-1002"]
