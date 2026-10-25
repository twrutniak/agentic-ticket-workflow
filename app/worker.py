"""Temporal worker: runs the ticket workflow and its activities."""

from __future__ import annotations

import asyncio
import logging

from temporalio.worker import Worker

from app.config import get_settings
from app.log import setup_logging
from app.services.helpdesk import FakeHelpdesk
from app.services.llm import build_llm
from app.services.repository import get_repository
from app.temporal.activities import TicketActivities
from app.temporal.client import connect
from app.temporal.workflow import TicketWorkflow


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
    )
    logging.getLogger(__name__).info(
        "worker started",
        extra={
            "task_queue": settings.task_queue,
            "llm_mode": settings.llm_mode,
            "helpdesk_failure_rate": settings.helpdesk_failure_rate,
        },
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
