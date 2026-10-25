"""Read-only access to customers, invoices and the knowledge base (JSON files in app/data)."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from app.domain.models import Customer, Invoice, KBArticle

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_STOPWORDS = {
    "a", "an", "and", "are", "can", "do", "for", "how", "i", "is", "it", "my", "of", "on",
    "or", "the", "to", "we", "what", "why", "you", "your",
}  # fmt: skip


def _tokens(text: str) -> set[str]:
    # Crude stemming (first 5 letters) so "suspended"/"suspend" or "charged"/"charge" match.
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w[:5] for w in words if w not in _STOPWORDS and len(w) > 2}


class Repository:
    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        load = lambda name: json.loads((data_dir / name).read_text())  # noqa: E731
        self._customers = {c["customer_id"]: Customer(**c) for c in load("customers.json")}
        self._invoices = [Invoice(**i) for i in load("invoices.json")]
        self._kb = [KBArticle(**a) for a in load("kb.json")]

    def get_customer(self, customer_id: str) -> Customer | None:
        return self._customers.get(customer_id)

    def get_invoices(self, customer_id: str) -> list[Invoice]:
        return [i for i in self._invoices if i.customer_id == customer_id]

    def kb_article_ids(self) -> list[str]:
        return [a.article_id for a in self._kb]

    def search_kb(self, query: str, limit: int = 3) -> list[KBArticle]:
        """Keyword search: rank articles by how many query words they share."""
        query_tokens = _tokens(query)
        scored = [(len(query_tokens & _tokens(f"{a.title} {a.text}")), a) for a in self._kb]
        scored = [(s, a) for s, a in scored if s > 0]
        scored.sort(key=lambda sa: -sa[0])
        return [a for _, a in scored[:limit]]


@lru_cache
def get_repository() -> Repository:
    return Repository()
