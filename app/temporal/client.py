from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter

from app.config import Settings

log = logging.getLogger(__name__)


async def connect(settings: Settings, attempts: int = 30) -> Client:
    """Connect to Temporal, waiting for it to come up (useful under docker compose)."""
    for attempt in range(1, attempts + 1):
        try:
            return await Client.connect(
                settings.temporal_address,
                namespace=settings.temporal_namespace,
                data_converter=pydantic_data_converter,
            )
        except RuntimeError as e:
            if attempt == attempts:
                raise
            log.warning("temporal not reachable yet", extra={"attempt": attempt, "error": str(e)})
            await asyncio.sleep(2)
    raise AssertionError("unreachable")
