"""Domain models. Plain Pydantic, no dependency on FastAPI, Temporal or the LLM provider."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Category(StrEnum):
    ACCOUNT = "account"
    BILLING = "billing"
    TECHNICAL = "technical"
    OTHER = "other"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ActionType(StrEnum):
    REPLY = "reply"
    REFUND = "refund"
    ESCALATE = "escalate"
    CLOSE = "close"


class Step(StrEnum):
    RECEIVED = "received"
    LOADING_CONTEXT = "loading_context"
    DECIDING = "deciding"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTING = "acting"
    COMPLETED = "completed"
    FAILED = "failed"


class Ticket(BaseModel):
    ticket_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    customer_id: str = Field(min_length=1, max_length=64)
    subject: str = Field(max_length=500)
    body: str = Field(max_length=10_000)


class Customer(BaseModel):
    customer_id: str
    name: str
    plan: str
    status: str


class Invoice(BaseModel):
    invoice_id: str
    customer_id: str
    month: str
    amount_eur: float
    status: str


class KBArticle(BaseModel):
    article_id: str
    title: str
    text: str


class TicketContext(BaseModel):
    """Trusted data about the ticket's customer, loaded by code (not by the model)."""

    customer: Customer
    invoices: list[Invoice]
    kb_article_ids: list[str]


class Decision(BaseModel):
    """A decision as proposed by the agent.

    Fields are plain strings on purpose: the model may return values outside the allowed
    sets, and that must be caught by the business rules (and turned into an escalation),
    not crash the parser.
    """

    category: str
    priority: str
    action: str
    message: str | None = None  # reply
    article_ids: list[str] = Field(default_factory=list)  # reply
    invoice_id: str | None = None  # refund
    amount_eur: float | None = None  # refund
    reason: str | None = None  # escalate / close


class ValidationResult(BaseModel):
    decision: Decision  # the decision to carry out (original, or an escalation replacing it)
    errors: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Approval(BaseModel):
    approved: bool
    reviewer: str = Field(default="unknown", max_length=100)
    comment: str | None = Field(default=None, max_length=2000)


class ActionResult(BaseModel):
    action: str
    helpdesk_reference: str
    details: dict[str, str | float | list[str] | None] = Field(default_factory=dict)


class TicketState(BaseModel):
    ticket_id: str
    step: Step = Step.RECEIVED
    proposed_decision: Decision | None = None
    decision: Decision | None = None
    validation_errors: list[str] = Field(default_factory=list)
    approval: Approval | None = None
    result: ActionResult | None = None
    error: str | None = None
