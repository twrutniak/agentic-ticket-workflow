"""Temporal worker: runs the ticket workflow and its activities."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timedelta

from temporalio.worker import Worker

from app.config import get_settings
from app.log import setup_logging
from app.services.helpdesk import FakeHelpdesk
from app.services.llm import build_llm
from app.services.repository import get_repository
from app.temporal.activities import TicketActivities
from app.temporal.client import connect
from app.temporal.workflow import TicketWorkflow

log = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    client = await connect(settings)
    activities = TicketActivities(
        repository=get_repository(),
        llm=build_llm(settings),
        helpdesk=FakeHelpdesk(failure_rate=settings.helpdesk_failure_rate),
    )
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[TicketWorkflow],
        activities=[activities.load_context, activities.decide, activities.execute_action],
        # On shutdown, running activities get this long to finish; after that they are
        # cancelled and Temporal retries them on another worker.
        graceful_shutdown_timeout=timedelta(seconds=settings.worker_shutdown_grace_seconds),
    )

    # Docker sends SIGTERM on stop; without a handler Python would die at once, dropping
    # in-flight activities and the log lines still queued.
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with worker:  # leaving the block stops polling and waits for running activities
        log.info(
            "worker started",
            extra={
                "task_queue": settings.task_queue,
                "llm_mode": settings.llm_mode,
                "helpdesk_failure_rate": settings.helpdesk_failure_rate,
            },
        )
        await stop.wait()
        log.info("worker stopping", extra={"grace_seconds": settings.worker_shutdown_grace_seconds})
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
