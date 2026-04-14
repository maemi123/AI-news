from __future__ import annotations

import asyncio
import argparse
import logging

from app.bootstrap import seed_default_monitor_sources
from app.database import AsyncSessionLocal, init_db
from app.services.scheduled_push_runner import ScheduledPushRunner
from app.utils.logger import setup_logger

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run one scheduled collection and push job.')
    parser.add_argument('--attempt-slot', type=int, default=1, choices=(1, 2, 3))
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    setup_logger()
    LOGGER.info('Starting one-shot scheduled job runner for slot %s', args.attempt_slot)
    await init_db()

    async with AsyncSessionLocal() as session:
        inserted = await seed_default_monitor_sources(session)
        if inserted:
            LOGGER.info('Seeded %s default monitor source(s)', inserted)

    await ScheduledPushRunner().run(attempt_slot=args.attempt_slot)
    LOGGER.info('One-shot scheduled job runner finished')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(asyncio.run(main()))
    except Exception:
        LOGGER.exception('Scheduled job runner failed')
        raise SystemExit(1) from None
