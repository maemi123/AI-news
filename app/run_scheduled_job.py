from __future__ import annotations

import asyncio
import logging

from app.bootstrap import seed_default_monitor_sources
from app.database import AsyncSessionLocal, init_db
from app.scheduler import run_daily_collection_job
from app.utils.logger import setup_logger

LOGGER = logging.getLogger(__name__)


async def main() -> int:
    setup_logger()
    LOGGER.info('Starting one-shot scheduled job runner')
    await init_db()

    async with AsyncSessionLocal() as session:
        inserted = await seed_default_monitor_sources(session)
        if inserted:
            LOGGER.info('Seeded %s default monitor source(s)', inserted)

    await run_daily_collection_job()
    LOGGER.info('One-shot scheduled job runner finished')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception:
        LOGGER.exception('Scheduled job runner failed')
        raise SystemExit(1) from None
