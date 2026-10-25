"""Download Temporal's time-skipping test server once, so later test runs work offline."""

import asyncio

from temporalio.testing import WorkflowEnvironment


async def main() -> None:
    env = await WorkflowEnvironment.start_time_skipping()
    await env.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
