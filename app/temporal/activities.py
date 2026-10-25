"""Temporal activities: everything that does I/O or is non-deterministic (data lookup, LLM, Helpdesk).

Errors a retry can fix are raised as-is and retried by the activity retry policy. Errors a
retry cannot fix are raised as non-retryable ApplicationErrors, so the workflow can escalate.
"""

from __future__ import annotations

from pydantic import BaseModel
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.agent.agent import AgentError, run_agent
from app.agent.tools import ToolExecutor
from app.domain.models import ActionResult, ActionType, Decision, Ticket, TicketContext
from app.services.helpdesk import FakeHelpdesk
from app.services.llm.base import LLMClient, PermanentLLMError
from app.services.repository import Repository


class ActionRequest(BaseModel):
    ticket_id: str
    idempotency_key: str
    decision: Decision


def _extra(ticket_id: str, **fields) -> dict:
    return {"ticket_id": ticket_id, **fields}


class TicketActivities:
    def __init__(self, repository: Repository, llm: LLMClient, helpdesk: FakeHelpdesk) -> None:
        self._repo = repository
        self._llm = llm
        self._helpdesk = helpdesk

    @activity.defn
    async def load_context(self, ticket: Ticket) -> TicketContext:
        customer = self._repo.get_customer(ticket.customer_id)
        if customer is None:
            activity.logger.warning("unknown customer", extra=_extra(ticket.ticket_id, customer_id=ticket.customer_id))
            raise ApplicationError(f"unknown customer {ticket.customer_id}", type="UnknownCustomer", non_retryable=True)
        return TicketContext(
            customer=customer,
            invoices=self._repo.get_invoices(customer.customer_id),
            kb_article_ids=self._repo.kb_article_ids(),
        )

    @activity.defn
    async def decide(self, ticket: Ticket) -> Decision:
        info = activity.info()
        activity.logger.info("asking the agent", extra=_extra(ticket.ticket_id))
        try:
            return await run_agent(
                self._llm,
                ticket,
                ToolExecutor(self._repo, ticket.customer_id),
                log_extra=_extra(ticket.ticket_id, workflow_id=info.workflow_id, attempt=info.attempt),
            )
        except (AgentError, PermanentLLMError) as e:
            raise ApplicationError(str(e), type=type(e).__name__, non_retryable=True) from e
        # TransientLLMError and anything unexpected propagate and are retried.

    @activity.defn
    async def execute_action(self, request: ActionRequest) -> ActionResult:
        d, key, ticket_id = request.decision, request.idempotency_key, request.ticket_id
        activity.logger.info("executing action", extra=_extra(ticket_id, action=d.action, idempotency_key=key))
        match ActionType(d.action):
            case ActionType.REPLY:
                result = self._helpdesk.send_reply(key, ticket_id, d.message or "", d.article_ids)
            case ActionType.REFUND:
                assert d.invoice_id is not None and d.amount_eur is not None  # guaranteed by the rules
                result = self._helpdesk.issue_refund(key, ticket_id, d.invoice_id, d.amount_eur)
            case ActionType.ESCALATE:
                result = self._helpdesk.escalate(key, ticket_id, d.reason or "")
            case ActionType.CLOSE:
                result = self._helpdesk.close(key, ticket_id, d.reason or "")
        activity.logger.info(
            "action done", extra=_extra(ticket_id, action=d.action, helpdesk_reference=result.helpdesk_reference)
        )
        return result
