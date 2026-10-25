import random

import pytest

from app.services.helpdesk import FakeHelpdesk, TransientHelpdeskError
from app.services.repository import Repository


def test_search_kb_finds_matching_articles():
    repo = Repository()
    assert repo.search_kb("reset my password")[0].article_id == "KB-1"
    assert repo.search_kb("account suspended")[0].article_id == "KB-4"
    assert repo.search_kb("pizza") == []


def test_helpdesk_idempotency_key_prevents_double_refund():
    helpdesk = FakeHelpdesk()
    first = helpdesk.issue_refund("wf-1:refund", "T-2", "INV-1002", 49.0)
    second = helpdesk.issue_refund("wf-1:refund", "T-2", "INV-1002", 49.0)
    assert first == second
    assert len(helpdesk.operations) == 1


def test_helpdesk_failure_after_commit_does_not_duplicate_on_retry():
    helpdesk = FakeHelpdesk(failure_rate=1.0, rng=random.Random(0))
    attempts = 0
    while True:
        attempts += 1
        try:
            result = helpdesk.issue_refund("wf-2:refund", "T-2", "INV-1002", 49.0)
            break
        except TransientHelpdeskError:
            if attempts == 3:
                helpdesk.failure_rate = 0.0  # the outage ends
    assert result.action == "refund"
    assert len(helpdesk.operations) == 1


@pytest.mark.parametrize("rate", [0.0, 1.0])
def test_helpdesk_fails_at_configured_rate(rate):
    helpdesk = FakeHelpdesk(failure_rate=rate)
    failures = 0
    for i in range(20):
        try:
            helpdesk.close(f"k{i}", "T-5", "spam")
        except TransientHelpdeskError:
            failures += 1
    assert failures == 20 * rate
