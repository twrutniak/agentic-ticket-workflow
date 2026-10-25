import pytest
from fastapi.testclient import TestClient

from app.api.main import app, get_gateway
from app.domain.models import Approval, Step, Ticket, TicketState
from app.temporal.gateway import ApprovalNotAccepted, StartResult, TicketNotFound, TicketView


class FakeGateway:
    def __init__(self) -> None:
        self.states: dict[str, TicketState] = {}

    async def start(self, ticket: Ticket) -> StartResult:
        created = ticket.ticket_id not in self.states
        if created:
            self.states[ticket.ticket_id] = TicketState(ticket_id=ticket.ticket_id, step=Step.AWAITING_APPROVAL)
        return StartResult(ticket_id=ticket.ticket_id, workflow_id=f"ticket-{ticket.ticket_id}", created=created)

    async def get(self, ticket_id: str) -> TicketView:
        if ticket_id not in self.states:
            raise TicketNotFound(ticket_id)
        return TicketView(
            ticket_id=ticket_id, workflow_id=f"ticket-{ticket_id}", status="RUNNING", state=self.states[ticket_id]
        )

    async def approve(self, ticket_id: str, approval: Approval) -> TicketState:
        state = self.states.get(ticket_id)
        if state is None:
            raise TicketNotFound(ticket_id)
        if state.approval is not None:
            raise ApprovalNotAccepted("ticket is not waiting for approval")
        state.approval = approval
        return state


@pytest.fixture
def client():
    gateway = FakeGateway()
    app.dependency_overrides[get_gateway] = lambda: gateway
    # Not entering the TestClient context, so the lifespan (Temporal connection) does not run.
    yield TestClient(app)
    app.dependency_overrides.clear()


TICKET = {"ticket_id": "T-2", "customer_id": "C-100", "subject": "Charged twice", "body": "Please refund."}


def test_post_ticket_starts_once_and_returns_existing_on_duplicate(client):
    first = client.post("/tickets", json=TICKET)
    assert first.status_code == 202
    assert first.json() == {"ticket_id": "T-2", "workflow_id": "ticket-T-2", "created": True}

    second = client.post("/tickets", json=TICKET)
    assert second.status_code == 200
    assert second.json()["created"] is False


def test_get_ticket(client):
    assert client.get("/tickets/T-2").status_code == 404
    client.post("/tickets", json=TICKET)
    body = client.get("/tickets/T-2").json()
    assert body["state"]["step"] == "awaiting_approval"


def test_approval(client):
    assert client.post("/tickets/T-2/approval", json={"approved": True}).status_code == 404
    client.post("/tickets", json=TICKET)
    ok = client.post("/tickets/T-2/approval", json={"approved": True, "reviewer": "anna"})
    assert ok.status_code == 200
    assert ok.json()["approval"]["reviewer"] == "anna"
    again = client.post("/tickets/T-2/approval", json={"approved": False})
    assert again.status_code == 409


def test_invalid_ticket_is_rejected(client):
    assert client.post("/tickets", json={"ticket_id": "bad id!", "customer_id": "C-1"}).status_code == 422
