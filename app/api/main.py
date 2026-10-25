"""HTTP API. It only starts, reads and signals workflows; all processing happens in the worker."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from app.config import get_settings
from app.domain.models import Approval, Ticket, TicketState
from app.log import setup_logging, workflow_id_for
from app.temporal.client import connect
from app.temporal.gateway import (
    ApprovalNotAccepted,
    StartResult,
    TemporalTicketGateway,
    TicketGateway,
    TicketNotFound,
    TicketView,
)

log = logging.getLogger("app.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    client = await connect(settings)
    app.state.gateway = TemporalTicketGateway(client, settings.task_queue)
    log.info("api started", extra={"temporal_address": settings.temporal_address})
    yield


app = FastAPI(title="Northdesk ticket workflow", lifespan=lifespan)


def get_gateway(request: Request) -> TicketGateway:
    return request.app.state.gateway


def _ids(ticket_id: str) -> dict[str, str]:
    return {"ticket_id": ticket_id, "workflow_id": workflow_id_for(ticket_id)}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/tickets", status_code=status.HTTP_202_ACCEPTED)
async def create_ticket(ticket: Ticket, response: Response, gateway: TicketGateway = Depends(get_gateway)) -> StartResult:
    result = await gateway.start(ticket)
    if not result.created:
        response.status_code = status.HTTP_200_OK
    log.info("ticket started" if result.created else "ticket already exists", extra=_ids(ticket.ticket_id))
    return result


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, gateway: TicketGateway = Depends(get_gateway)) -> TicketView:
    try:
        return await gateway.get(ticket_id)
    except TicketNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"ticket {ticket_id} not found") from None


@app.post("/tickets/{ticket_id}/approval")
async def approve_ticket(
    ticket_id: str, approval: Approval, gateway: TicketGateway = Depends(get_gateway)
) -> TicketState:
    try:
        state = await gateway.approve(ticket_id, approval)
    except TicketNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"ticket {ticket_id} not found") from None
    except ApprovalNotAccepted as e:
        log.warning("approval refused", extra=_ids(ticket_id) | {"reason": str(e)})
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None
    log.info(
        "approval sent", extra=_ids(ticket_id) | {"approved": approval.approved, "reviewer": approval.reviewer}
    )
    return state
