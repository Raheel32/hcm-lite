from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings


def office_tz() -> ZoneInfo:
    return ZoneInfo(settings.TIMEZONE)


def now() -> datetime:
    """Current time in the office timezone. Tests replace this function to fake the time."""
    return datetime.now(office_tz())


def today() -> date:
    return now().date()


def to_local(value: datetime) -> datetime:
    """A stored time (UTC, with or without tzinfo) shown in office time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(office_tz())