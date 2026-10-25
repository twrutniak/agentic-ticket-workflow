import json

import pytest

from app.domain.models import Ticket
from app.services.repository import DATA_DIR


@pytest.fixture(scope="session")
def example_tickets() -> dict[str, Ticket]:
    tickets = json.loads((DATA_DIR / "tickets.json").read_text())
    return {t["ticket_id"]: Ticket(**t) for t in tickets}
