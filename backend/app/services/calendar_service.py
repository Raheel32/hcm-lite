from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models.leave_config import Holiday


def holiday_dates(db: Session, start: date, end: date) -> set[date]:
    rows = db.query(Holiday.date).filter(Holiday.date >= start, Holiday.date <= end).all()
    return {row[0] for row in rows}


def is_working_day(day: date, weekend_days: list[int], holidays: set[date]) -> bool:
    return day.weekday() not in weekend_days and day not in holidays


def count_working_days(start: date, end: date, weekend_days: list[int], holidays: set[date]) -> int:
    total = 0
    day = start
    while day <= end:
        if is_working_day(day, weekend_days, holidays):
            total += 1
        day += timedelta(days=1)
    return total