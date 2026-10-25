"""JSON-lines logging shared by the API, the worker, the workflow and the activities.

Every log line about a ticket carries `ticket_id` and `workflow_id`. Temporal's workflow and
activity loggers add their context under `temporal_workflow` / `temporal_activity`; the
formatter lifts the useful keys to the top level so all lines look the same.
"""

from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import queue
import sys
from datetime import UTC, datetime

from temporalio import activity, workflow

_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}
_TEMPORAL_KEYS = ("workflow_id", "run_id", "workflow_type", "activity_type", "attempt")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS:
                continue
            if key in ("temporal_workflow", "temporal_activity") and isinstance(value, dict):
                entry.update({k: value[k] for k in _TEMPORAL_KEYS if k in value})
            else:
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


_listener: logging.handlers.QueueListener | None = None


def setup_logging(level: str = "INFO") -> None:
    """Log JSON lines to stdout without blocking the caller (and the event loop) on the write.

    The QueueHandler formats the record into a JSON line in the calling thread and puts it on an
    in-memory queue; a QueueListener thread writes the lines to stdout. A slow stdout (e.g. a log
    collector that cannot keep up) then delays the listener thread, not the request or activity.
    """
    global _listener
    shutdown_logging()

    log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    queue_handler = logging.handlers.QueueHandler(log_queue)
    queue_handler.setFormatter(JsonFormatter())  # prepare() turns the record into the final line
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    _listener = logging.handlers.QueueListener(log_queue, stdout_handler)
    _listener.start()

    root = logging.getLogger()
    root.handlers[:] = [queue_handler]
    root.setLevel(level)
    # Keep the context in `extra` only, not appended to the message text.
    activity.logger.activity_info_on_message = False
    workflow.logger.workflow_info_on_message = False


@atexit.register
def shutdown_logging() -> None:
    """Write out the lines still in the queue and stop the listener thread."""
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


def workflow_id_for(ticket_id: str) -> str:
    return f"ticket-{ticket_id}"
