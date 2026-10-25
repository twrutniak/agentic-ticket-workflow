"""Tools the agent can call, and their JSON schemas.

The tools are read-only and bound to the ticket's customer: the model cannot pass a
customer_id, so a ticket cannot make it read another customer's data.
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.models import ActionType, Category, Priority
from app.services.repository import Repository

SUBMIT_DECISION = "submit_decision"


def _function(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []},
        },
    }


TOOL_SPECS: list[dict[str, Any]] = [
    _function("get_customer", "Get the plan and status of the customer who sent the ticket.", {}),
    _function("get_invoices", "List the invoices of the customer who sent the ticket.", {}),
    _function(
        "search_kb",
        "Search the knowledge base for articles that answer a question.",
        {"query": {"type": "string", "description": "What to look for, in a few words."}},
        ["query"],
    ),
    _function(
        SUBMIT_DECISION,
        "Submit the final decision for the ticket. Call this exactly once, at the end.",
        {
            "category": {"type": "string", "enum": [c.value for c in Category]},
            "priority": {"type": "string", "enum": [p.value for p in Priority]},
            "action": {"type": "string", "enum": [a.value for a in ActionType]},
            "message": {"type": "string", "description": "reply: the message to send to the customer."},
            "article_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "reply: IDs of the knowledge base articles the message uses.",
            },
            "invoice_id": {"type": "string", "description": "refund: the invoice to refund."},
            "amount_eur": {"type": "number", "description": "refund: the amount to refund in EUR."},
            "reason": {"type": "string", "description": "escalate/close: why."},
        },
        ["category", "priority", "action"],
    ),
]


class ToolExecutor:
    """Runs the read-only tools for one ticket's customer."""

    def __init__(self, repository: Repository, customer_id: str) -> None:
        self._repo = repository
        self._customer_id = customer_id

    def run(self, name: str, arguments: dict[str, Any]) -> str:
        match name:
            case "get_customer":
                customer = self._repo.get_customer(self._customer_id)
                result: Any = customer.model_dump() if customer else {"error": "customer not found"}
            case "get_invoices":
                result = [i.model_dump() for i in self._repo.get_invoices(self._customer_id)]
            case "search_kb":
                query = str(arguments.get("query", ""))
                result = [a.model_dump() for a in self._repo.search_kb(query)]
            case _:
                result = {"error": f"unknown tool {name!r}"}
        return json.dumps(result)
