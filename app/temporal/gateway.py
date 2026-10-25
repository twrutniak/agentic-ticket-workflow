"""What the HTTP API needs from Temporal, behind a small interface (easy to fake in API tests)."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel
from temporalio.client import (
    Client,
    WorkflowExecutionStatus,
    WorkflowUpdateFailedError,
)
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from app.domain.models import Approval, Ticket, TicketState
from app.log import workflow_id_for
from app.temporal.workflow import TicketWorkflow


class TicketNotFound(Exception):
    pass


class ApprovalNotAccepted(Exception):
    pass


class StartResult(BaseModel):
    ticket_id: str
    workflow_id: str
    created: bool


class TicketView(BaseModel):
    ticket_id: str
    workflow_id: str
    status: str  # Temporal execution status: RUNNING, COMPLETED, FAILED, ...
    state: TicketState


class TicketGateway(Protocol):
    async def start(self, ticket: Ticket) -> StartResult: ...
    async def get(self, ticket_id: str) -> TicketView: ...
    async def approve(self, ticket_id: str, approval: Approval) -> TicketState: ...


class TemporalTicketGateway:
    def __init__(self, client: Client, task_queue: str) -> None:
        self._client = client
        self._task_queue = task_queue

    async def start(self, ticket: Ticket) -> StartResult:
        workflow_id = workflow_id_for(ticket.ticket_id)
        try:
            await self._client.start_workflow(
                TicketWorkflow.run,
                ticket,
                id=workflow_id,
                task_queue=self._task_queue,
                # The workflow ID is derived from the ticket ID, so Temporal itself rejects a
                # second start for the same ticket, whether the first run is still going or done.
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            )
            created = True
        except WorkflowAlreadyStartedError:
            created = False
        return StartResult(ticket_id=ticket.ticket_id, workflow_id=workflow_id, created=created)

    async def _status(self, ticket_id: str) -> WorkflowExecutionStatus | None:
        handle = self._client.get_workflow_handle(workflow_id_for(ticket_id))
        try:
            return (await handle.describe()).status
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                raise TicketNotFound(ticket_id) from e
            raise

    async def get(self, ticket_id: str) -> TicketView:
        status = await self._status(ticket_id)
        handle = self._client.get_workflow_handle_for(TicketWorkflow.run, workflow_id_for(ticket_id))
        state = await handle.query(TicketWorkflow.get_state)
        return TicketView(
            ticket_id=ticket_id,
            workflow_id=handle.id,
            status=status.name if status else "UNKNOWN",
            state=state,
        )

    async def approve(self, ticket_id: str, approval: Approval) -> TicketState:
        if await self._status(ticket_id) != WorkflowExecutionStatus.RUNNING:
            raise ApprovalNotAccepted("ticket is no longer being processed")
        handle = self._client.get_workflow_handle_for(TicketWorkflow.run, workflow_id_for(ticket_id))
        try:
            return await handle.execute_update(TicketWorkflow.approve, approval)
        except WorkflowUpdateFailedError as e:
            raise ApprovalNotAccepted(str(e.cause or e)) from e
