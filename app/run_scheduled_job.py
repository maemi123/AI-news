from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import logging
from pathlib import Path
import sys
import traceback

from app.utils.logger import append_failure_log, get_fail_log_file, setup_logger

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run one scheduled collection and push job.')
    parser.add_argument('--attempt-slot', type=int, default=1, choices=(1, 2, 3))
    return parser.parse_args()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _check_for_bom() -> list[str]:
    problems: list[str] = []
    for path in _project_root().rglob('*.py'):
        try:
            if path.read_bytes().startswith(b'\xef\xbb\xbf'):
                problems.append(str(path))
        except OSError as exc:
            problems.append(f'{path} (read failed: {exc})')
    return problems


def run_preflight_checks() -> None:
    problems: list[str] = []

    cwd = Path.cwd()
    root = _project_root()
    if cwd != root:
        problems.append(f'Working directory mismatch: cwd={cwd} project_root={root}')

    env_path = root / '.env'
    if not env_path.exists():
        problems.append(f'Missing .env file at {env_path}')

    bom_files = _check_for_bom()
    if bom_files:
        preview = '; '.join(bom_files[:5])
        extra = '' if len(bom_files) <= 5 else f' ... and {len(bom_files) - 5} more'
        problems.append(f'Python source files still contain BOM: {preview}{extra}')

    if problems:
        raise RuntimeError('Preflight failed: ' + ' | '.join(problems))


def record_failure(exc: BaseException) -> None:
    timestamp = datetime.now().isoformat(timespec='seconds')
    fail_log = get_fail_log_file()
    message = (
        f'[{timestamp}] Scheduled job failed\n'
        f'python={sys.executable}\n'
        f'cwd={Path.cwd()}\n'
        f'fail_log={fail_log}\n'
        f'error={exc!r}\n'
        f'{traceback.format_exc()}'
    )
    append_failure_log(message)


async def main() -> int:
    args = parse_args()
    setup_logger()
    LOGGER.info('Starting one-shot scheduled job runner for slot %s', args.attempt_slot)
    run_preflight_checks()

    from app.bootstrap import seed_default_monitor_sources
    from app.database import AsyncSessionLocal, init_db
    from app.services.scheduled_push_runner import ScheduledPushRunner

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
    except Exception as exc:
        try:
            setup_logger()
            LOGGER.exception('Scheduled job runner failed')
        finally:
            record_failure(exc)
        raise SystemExit(1) from None
