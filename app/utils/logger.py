from __future__ import annotations

import logging
from pathlib import Path


def get_log_dir() -> Path:
    log_dir = Path(__file__).resolve().parents[2] / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_main_log_file() -> Path:
    return get_log_dir() / 'ai_news.log'


def get_fail_log_file() -> Path:
    return get_log_dir() / 'scheduled_job_fail.log'


def append_failure_log(message: str) -> None:
    fail_log = get_fail_log_file()
    with fail_log.open('a', encoding='utf-8') as handle:
        handle.write(message.rstrip() + '\n')


def setup_logger() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(get_main_log_file(), encoding='utf-8'),
        ],
        force=True,
    )
