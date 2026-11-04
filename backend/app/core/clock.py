from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def office_tz() -> ZoneInfo:
    return ZoneInfo(settings.TIMEZONE)


def now() -> datetime:
    """Current time in the office timezone. Tests replace this function to fake the time."""
    return datetime.now(office_tz())


def today() -> date:
    return now().date()