"""Fake Helpdesk API used to carry out the final action.

It fails at random with a temporary error (rate from HELPDESK_FAILURE_RATE). Half of the
failures happen *after* the operation was recorded, like a timeout on a request that did reach
the server, which is exactly the case where a naive retry would refund twice.

Every call takes an idempotency key: a repeated call with the same key returns the first
result instead of doing the work again (as real payment APIs do). The store is in-memory, so it
lives as long as the worker process; a real Helpdesk would keep it server-side.
"""

from __future__ import annotations

import itertools
import random
import threading

from app.domain.models import ActionResult


class TransientHelpdeskError(Exception):
    """A temporary failure: retrying the same call may succeed."""


class FakeHelpdesk:
    def __init__(self, failure_rate: float = 0.0, rng: random.Random | None = None) -> None:
        self.failure_rate = failure_rate
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._results: dict[str, ActionResult] = {}
        self._ids = itertools.count(1)
        self.operations: list[ActionResult] = []  # every operation actually performed

    def _roll(self, idempotency_key: str) -> str | None:
        """Decide whether this call fails: None, "before" or "after" the operation."""
        if self._rng.random() >= self.failure_rate:
            return None
        return "after" if self._rng.random() < 0.5 else "before"

    def _perform(self, action: str, idempotency_key: str, details: dict) -> ActionResult:
        failure = self._roll(idempotency_key)
        if failure == "before":
            raise TransientHelpdeskError("helpdesk temporarily unavailable (before the operation)")
        with self._lock:
            if idempotency_key in self._results:
                return self._results[idempotency_key]
            result = ActionResult(
                action=action,
                helpdesk_reference=f"HD-{next(self._ids):05d}",
                details=details,
            )
            self._results[idempotency_key] = result
            self.operations.append(result)
        if failure == "after":
            raise TransientHelpdeskError("helpdesk timed out (after the operation was recorded)")
        return result

    def send_reply(self, idempotency_key: str, ticket_id: str, message: str, article_ids: list[str]) -> ActionResult:
        return self._perform(
            "reply", idempotency_key, {"ticket_id": ticket_id, "message": message, "article_ids": article_ids}
        )

    def issue_refund(self, idempotency_key: str, ticket_id: str, invoice_id: str, amount_eur: float) -> ActionResult:
        return self._perform(
            "refund", idempotency_key, {"ticket_id": ticket_id, "invoice_id": invoice_id, "amount_eur": amount_eur}
        )

    def escalate(self, idempotency_key: str, ticket_id: str, reason: str) -> ActionResult:
        return self._perform("escalate", idempotency_key, {"ticket_id": ticket_id, "reason": reason})

    def close(self, idempotency_key: str, ticket_id: str, reason: str) -> ActionResult:
        return self._perform("close", idempotency_key, {"ticket_id": ticket_id, "reason": reason})
