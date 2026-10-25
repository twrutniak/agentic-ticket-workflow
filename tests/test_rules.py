import pytest

from app.domain.models import Customer, Decision, Invoice
from app.domain.rules import validate_decision

CUSTOMER = Customer(customer_id="C-100", name="Northwind Bakery", plan="business", status="active")
INVOICES = [
    Invoice(invoice_id="INV-1001", customer_id="C-100", month="2026-09", amount_eur=49.0, status="paid"),
    Invoice(invoice_id="INV-1002", customer_id="C-100", month="2026-09", amount_eur=49.0, status="paid"),
    Invoice(invoice_id="INV-1003", customer_id="C-100", month="2026-10", amount_eur=49.0, status="overdue"),
    Invoice(invoice_id="INV-3001", customer_id="C-300", month="2026-09", amount_eur=990.0, status="paid"),
]
KB = ["KB-1", "KB-2", "KB-3", "KB-4"]


def validate(**fields):
    base = {"category": "billing", "priority": "normal"}
    return validate_decision(Decision(**(base | fields)), CUSTOMER, INVOICES, KB)


def test_valid_reply_passes():
    result = validate(action="reply", message="Use Forgot password.", article_ids=["KB-1"])
    assert result.ok
    assert result.decision.action == "reply"


def test_valid_refund_passes():
    result = validate(action="refund", invoice_id="INV-1002", amount_eur=49.0)
    assert result.ok
    assert result.decision.action == "refund"


@pytest.mark.parametrize("action", ["escalate", "close"])
def test_escalate_and_close_need_reason(action):
    assert validate(action=action, reason="Needs a human").ok
    assert not validate(action=action).ok
    assert not validate(action=action, reason="   ").ok


@pytest.mark.parametrize(
    "fields, error",
    [
        ({"category": "sales", "action": "close", "reason": "x"}, "category 'sales'"),
        ({"priority": "critical", "action": "close", "reason": "x"}, "priority 'critical'"),
        ({"action": "delete_account"}, "action 'delete_account'"),
        ({"action": "reply", "message": ""}, "non-empty message"),
        ({"action": "reply", "message": "hi", "article_ids": ["KB-99"]}, "unknown articles: KB-99"),
        ({"action": "refund", "amount_eur": 10.0}, "needs an invoice_id"),
        ({"action": "refund", "invoice_id": "INV-1001"}, "needs an amount_eur"),
        ({"action": "refund", "invoice_id": "INV-9999", "amount_eur": 1.0}, "does not exist"),
        ({"action": "refund", "invoice_id": "INV-3001", "amount_eur": 1.0}, "belongs to another customer"),
        ({"action": "refund", "invoice_id": "INV-1001", "amount_eur": 5000.0}, "exceeds invoice"),
        ({"action": "refund", "invoice_id": "INV-1001", "amount_eur": 0.0}, "must be positive"),
        ({"action": "refund", "invoice_id": "INV-1003", "amount_eur": 10.0}, "cannot be refunded"),
    ],
)
def test_rule_violation_becomes_escalation(fields, error):
    result = validate(**fields)
    assert not result.ok
    assert any(error in e for e in result.errors), result.errors
    assert result.decision.action == "escalate"
    assert error in result.decision.reason


def test_escalation_keeps_valid_category_and_priority():
    result = validate(priority="high", action="refund", invoice_id="INV-1001", amount_eur=5000.0)
    assert result.decision.category == "billing"
    assert result.decision.priority == "high"


def test_escalation_replaces_invalid_category_and_priority():
    result = validate(category="hack", priority="now", action="refund")
    assert result.decision.category == "other"
    assert result.decision.priority == "normal"
