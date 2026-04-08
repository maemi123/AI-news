from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_timezone(name: str) -> timezone | ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        fallback_offsets = {
            'Asia/Shanghai': timezone(timedelta(hours=8)),
            'UTC': timezone.utc,
        }
        return fallback_offsets.get(name, timezone.utc)
