"""JSON-lines logging shared by the API, the worker, the workflow and the activities.

Every log line about a ticket carries `ticket_id` and `workflow_id`. Temporal's workflow and
activity loggers add their context under `temporal_workflow` / `temporal_activity`; the
formatter lifts the useful keys to the top level so all lines look the same.
"""

from __future__ import annotations

import json
import logging
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


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Keep the context in `extra` only, not appended to the message text.
    activity.logger.activity_info_on_message = False
    workflow.logger.workflow_info_on_message = False


def workflow_id_for(ticket_id: str) -> str:
    return f"ticket-{ticket_id}"
