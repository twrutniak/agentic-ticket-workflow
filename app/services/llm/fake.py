"""Rule-based stand-in for the LLM: no key, no network.

It reads the conversation like a real model would (the ticket from the user message, tool
results from tool messages) and answers in the same shape: tool calls first, then a
`submit_decision` call. It is deliberately naive about the ticket text, so it falls for prompt
injection (e.g. "refund 5000 EUR") and shows that the business rules, not the model, protect
the refund.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

from app.services.llm.base import LLMResponse, ToolCall, TransientLLMError

Plan = list[tuple[str, dict[str, Any]]]


class FakeLLM:
    def __init__(self, failure_rate: float = 0.0, rng: random.Random | None = None) -> None:
        self.failure_rate = failure_rate
        self._rng = rng or random.Random()
        self.calls = 0

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls += 1
        if self._rng.random() < self.failure_rate:
            raise TransientLLMError("fake LLM: simulated temporary outage")

        ticket = _read_ticket(messages)
        results = _read_tool_results(messages)
        text = f"{ticket['subject']} {ticket['body']}".lower()

        # Ask for each tool the plan needs, one per turn, then decide.
        for name, args in _tools_needed(text):
            if name not in results:
                return _call(name, args, len(results))
        return _call("submit_decision", _decide(text, results), len(results))


def _call(name: str, args: dict[str, Any], n: int) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id=f"call_{n}_{name}", name=name, arguments=json.dumps(args))])


def _read_ticket(messages: list[dict[str, Any]]) -> dict[str, Any]:
    user = next(m["content"] for m in messages if m["role"] == "user")
    match = re.search(r"<ticket>\n(.*)\n</ticket>", user, re.S)
    return json.loads(match.group(1)) if match else {"subject": "", "body": user}


def _read_tool_results(messages: list[dict[str, Any]]) -> dict[str, Any]:
    names = {
        tc["id"]: tc["function"]["name"]
        for m in messages
        if m["role"] == "assistant"
        for tc in m.get("tool_calls") or []
    }
    return {names[m["tool_call_id"]]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}


def _intent(text: str) -> str:
    if "refund" in text or "charged twice" in text:
        return "refund"
    if any(w in text for w in ("password", "log in", "login", "sign in")):
        return "password"
    if any(w in text for w in ("down", "outage", "errors", "not working")):
        return "outage"
    if any(w in text for w in ("locked", "suspended", "can't use", "cannot use")):
        return "locked"
    if any(w in text for w in ("subscription", "plan", "account")):
        return "account"
    return "unrelated"


def _tools_needed(text: str) -> Plan:
    return {
        "refund": [("get_invoices", {}), ("search_kb", {"query": "charged twice refund"})],
        "password": [("search_kb", {"query": "reset password"})],
        "outage": [("get_customer", {}), ("search_kb", {"query": "service status outage"})],
        "locked": [("get_customer", {}), ("get_invoices", {}), ("search_kb", {"query": "suspended account"})],
        "account": [("get_customer", {})],
        "unrelated": [],
    }[_intent(text)]


def _decide(text: str, results: dict[str, Any]) -> dict[str, Any]:
    customer = results.get("get_customer") or {}
    articles = results.get("search_kb") or []
    first_article = articles[0] if articles else None

    match _intent(text):
        case "refund":
            invoices = results.get("get_invoices") or []
            if not invoices:
                return _escalate("billing", "normal", "Refund requested but the customer has no invoices.")
            # Naive on purpose: an amount written in the ticket is taken at face value.
            amount = re.search(r"(\d+(?:\.\d+)?)\s*(?:eur|€)", text)
            target = _duplicate_invoice(invoices) or invoices[-1]
            return {
                "category": "billing",
                "priority": "normal",
                "action": "refund",
                "invoice_id": target["invoice_id"],
                "amount_eur": float(amount.group(1)) if amount else target["amount_eur"],
            }
        case "password" if first_article:
            return _reply("account", "normal", first_article)
        case "outage":
            urgent = "urgent" in text or customer.get("plan") == "enterprise"
            return _escalate("technical", "urgent" if urgent else "high", "Customer reports a possible outage.")
        case "locked" if customer.get("status") == "suspended" and first_article:
            overdue = [i["invoice_id"] for i in results.get("get_invoices") or [] if i["status"] == "overdue"]
            extra = f" Overdue invoice: {', '.join(overdue)}." if overdue else ""
            return _reply("account", "high", first_article, extra)
        case "account" if "plan" in customer:
            return {
                "category": "account",
                "priority": "low",
                "action": "reply",
                "message": f"Your {customer['plan']} plan is currently {customer['status']}.",
                "article_ids": [],
            }
        case "unrelated":
            return {"category": "other", "priority": "low", "action": "close", "reason": "Not a support request."}
    return _escalate("other", "normal", "Could not find an answer automatically.")


def _duplicate_invoice(invoices: list[dict[str, Any]]) -> dict[str, Any] | None:
    seen: set[tuple[str, float]] = set()
    for invoice in invoices:
        key = (invoice["month"], invoice["amount_eur"])
        if key in seen:
            return invoice
        seen.add(key)
    return None


def _reply(category: str, priority: str, article: dict[str, Any], extra: str = "") -> dict[str, Any]:
    return {
        "category": category,
        "priority": priority,
        "action": "reply",
        "message": f"{article['title']}: {article['text']}{extra}",
        "article_ids": [article["article_id"]],
    }


def _escalate(category: str, priority: str, reason: str) -> dict[str, Any]:
    return {"category": category, "priority": priority, "action": "escalate", "reason": reason}
