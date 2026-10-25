import json
import logging

import pytest

from app.log import setup_logging, shutdown_logging


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    yield
    shutdown_logging()
    root.handlers[:], root.level = saved


def test_logs_go_through_the_queue_as_json_lines(restore_logging, capsys):
    # Set up inside the test: capsys replaces sys.stdout only while the test itself runs.
    setup_logging("INFO")
    log = logging.getLogger("test")
    log.info("ticket %s started", "T-1", extra={"ticket_id": "T-1", "workflow_id": "ticket-T-1"})
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed", extra={"ticket_id": "T-1"})
    shutdown_logging()  # flush the queue

    first, second = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert first["msg"] == "ticket T-1 started"
    assert (first["ticket_id"], first["workflow_id"]) == ("T-1", "ticket-T-1")
    assert second["msg"] == "failed"  # the traceback stays in its own field
    assert "ValueError: boom" in second["exc"]
