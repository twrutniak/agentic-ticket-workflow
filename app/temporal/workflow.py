"""The ticket workflow: decide -> check -> (approval for refunds) -> act.

Workflow code is deterministic orchestration only. All I/O happens in activities; the business
rules are pure functions, so they run here directly.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.domain import rules
    from app.domain.models import ActionType, Approval, Decision, Step, Ticket, TicketContext, TicketState
    from app.temporal.activities import ActionRequest, TicketActivities

APPROVAL_TIMEOUT = timedelta(hours=24)

LOOKUP_RETRY = RetryPolicy(maximum_attempts=3)
LLM_RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2, maximum_attempts=4)
HELPDESK_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=6,
)


def _cause(error: ActivityError) -> str:
    return str(error.cause) if error.cause else str(error)


@workflow.defn
class TicketWorkflow:
    @workflow.init
    def __init__(self, ticket: Ticket) -> None:
        self._state = TicketState(ticket_id=ticket.ticket_id)

    @workflow.query
    def get_state(self) -> TicketState:
        return self._state

    @workflow.update
    def approve(self, approval: Approval) -> TicketState:
        self._state.approval = approval
        return self._state

    @approve.validator
    def _validate_approve(self, approval: Approval) -> None:
        if self._state.step != Step.AWAITING_APPROVAL or self._state.approval is not None:
            raise ValueError(f"ticket is not waiting for approval (step: {self._state.step})")

    def _log(self, msg: str, **fields) -> None:
        workflow.logger.info(msg, extra={"ticket_id": self._state.ticket_id, "step": self._state.step, **fields})

    def _set_step(self, step: Step) -> None:
        self._state.step = step
        self._log("step changed")

    @workflow.run
    async def run(self, ticket: Ticket) -> TicketState:
        decision = await self._decide(ticket)
        self._state.decision = decision

        if decision.action == ActionType.REFUND:
            decision = await self._wait_for_approval(decision)
            self._state.decision = decision

        self._set_step(Step.ACTING)
        try:
            self._state.result = await workflow.execute_activity_method(
                TicketActivities.execute_action,
                ActionRequest(
                    ticket_id=ticket.ticket_id,
                    # One key per workflow and action: a retried activity reuses it, so the
                    # Helpdesk performs the action once.
                    idempotency_key=f"{workflow.info().workflow_id}:{decision.action}",
                    decision=decision,
                ),
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=HELPDESK_RETRY,
            )
        except ActivityError as e:
            self._state.error = f"helpdesk action failed: {_cause(e)}"
            self._set_step(Step.FAILED)
            raise ApplicationError(self._state.error, non_retryable=True) from e

        self._set_step(Step.COMPLETED)
        return self._state

    async def _decide(self, ticket: Ticket) -> Decision:
        """Load context, ask the agent, check the decision. Anything unusable becomes an escalation."""
        self._set_step(Step.LOADING_CONTEXT)
        try:
            context: TicketContext = await workflow.execute_activity_method(
                TicketActivities.load_context,
                ticket,
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=LOOKUP_RETRY,
            )
        except ActivityError as e:
            self._state.error = _cause(e)
            return rules.escalation(f"Could not process automatically: {_cause(e)}")

        self._set_step(Step.DECIDING)
        try:
            proposed: Decision = await workflow.execute_activity_method(
                TicketActivities.decide,
                ticket,
                start_to_close_timeout=timedelta(minutes=3),
                retry_policy=LLM_RETRY,
            )
        except ActivityError as e:
            self._state.error = _cause(e)
            return rules.escalation(f"Agent could not decide: {_cause(e)}")
        self._state.proposed_decision = proposed

        self._set_step(Step.VALIDATING)
        result = rules.validate_decision(proposed, context.customer, context.invoices, context.kb_article_ids)
        self._state.validation_errors = result.errors
        if not result.ok:
            self._log("decision rejected by rules", errors=result.errors)
        return result.decision

    async def _wait_for_approval(self, decision: Decision) -> Decision:
        self._set_step(Step.AWAITING_APPROVAL)
        try:
            await workflow.wait_condition(lambda: self._state.approval is not None, timeout=APPROVAL_TIMEOUT)
        except TimeoutError:
            self._log("approval timed out")
            return rules.escalation(
                f"Refund of {decision.amount_eur} EUR for {decision.invoice_id} was not approved within 24 hours",
                decision.category,
                decision.priority,
            )
        approval = self._state.approval
        assert approval is not None
        self._log("approval received", approved=approval.approved, reviewer=approval.reviewer)
        if approval.approved:
            return decision
        return rules.escalation(
            f"Refund rejected by {approval.reviewer}: {approval.comment or 'no comment'}",
            decision.category,
            decision.priority,
        )
