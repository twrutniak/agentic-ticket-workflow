"""Business rules for agent decisions.

Pure functions: no I/O, no FastAPI, Temporal or LLM imports. They are called from inside the
workflow, so they must stay deterministic.
"""

from __future__ import annotations

import math

from app.domain.models import (
    ActionType,
    Category,
    Customer,
    Decision,
    Invoice,
    Priority,
    ValidationResult,
)

ALLOWED_CATEGORIES = {c.value for c in Category}
ALLOWED_PRIORITIES = {p.value for p in Priority}
ALLOWED_ACTIONS = {a.value for a in ActionType}
REFUNDABLE_INVOICE_STATUSES = {"paid"}


def check_decision(
    decision: Decision,
    customer: Customer,
    invoices: list[Invoice],
    kb_article_ids: list[str],
) -> list[str]:
    """Return a list of rule violations (empty when the decision is allowed)."""
    errors: list[str] = []

    if decision.category not in ALLOWED_CATEGORIES:
        errors.append(f"category {decision.category!r} is not allowed")
    if decision.priority not in ALLOWED_PRIORITIES:
        errors.append(f"priority {decision.priority!r} is not allowed")
    if decision.action not in ALLOWED_ACTIONS:
        errors.append(f"action {decision.action!r} is not allowed")
        return errors

    match ActionType(decision.action):
        case ActionType.REPLY:
            if not (decision.message and decision.message.strip()):
                errors.append("reply needs a non-empty message")
            unknown = [a for a in decision.article_ids if a not in kb_article_ids]
            if unknown:
                errors.append(f"reply cites unknown articles: {', '.join(unknown)}")
        case ActionType.REFUND:
            errors.extend(_check_refund(decision, customer, invoices))
        case ActionType.ESCALATE | ActionType.CLOSE:
            if not (decision.reason and decision.reason.strip()):
                errors.append(f"{decision.action} needs a reason")

    return errors


def _check_refund(decision: Decision, customer: Customer, invoices: list[Invoice]) -> list[str]:
    if not decision.invoice_id:
        return ["refund needs an invoice_id"]
    if decision.amount_eur is None:
        return ["refund needs an amount_eur"]

    # Ownership is checked explicitly too, so the rule holds even if a caller passes
    # invoices of other customers.
    invoice = next((i for i in invoices if i.invoice_id == decision.invoice_id), None)
    if invoice is None:
        return [f"invoice {decision.invoice_id} does not exist for customer {customer.customer_id}"]
    if invoice.customer_id != customer.customer_id:
        return [f"invoice {invoice.invoice_id} belongs to another customer"]

    errors = []
    # NaN compares false with everything, so it would slip past both checks below.
    if not math.isfinite(decision.amount_eur) or decision.amount_eur <= 0:
        errors.append("refund amount must be a positive number")
    if decision.amount_eur > invoice.amount_eur:
        errors.append(
            f"refund amount {decision.amount_eur:.2f} EUR exceeds invoice "
            f"{invoice.invoice_id} amount {invoice.amount_eur:.2f} EUR"
        )
    if invoice.status not in REFUNDABLE_INVOICE_STATUSES:
        errors.append(f"invoice {invoice.invoice_id} has status {invoice.status!r} and cannot be refunded")
    return errors


def escalation(reason: str, category: str = "other", priority: str = "normal") -> Decision:
    """Build an escalation, keeping category/priority only if they are valid values."""
    return Decision(
        category=category if category in ALLOWED_CATEGORIES else Category.OTHER.value,
        priority=priority if priority in ALLOWED_PRIORITIES else Priority.NORMAL.value,
        action=ActionType.ESCALATE.value,
        reason=reason,
    )


def validate_decision(
    decision: Decision,
    customer: Customer,
    invoices: list[Invoice],
    kb_article_ids: list[str],
) -> ValidationResult:
    """Check a decision; a decision that breaks a rule becomes an escalation to a person."""
    errors = check_decision(decision, customer, invoices, kb_article_ids)
    if not errors:
        return ValidationResult(decision=decision)
    reason = "Automatic decision rejected by business rules: " + "; ".join(errors)
    return ValidationResult(
        decision=escalation(reason, decision.category, decision.priority),
        errors=errors,
    )
