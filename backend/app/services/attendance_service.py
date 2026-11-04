from calendar import monthrange
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core import clock
from app.models.attendance import Attendance, DayStatus
from app.models.employee import Employee
from app.models.user import User
from app.services import calendar_service, employee_service, settings_service


class AlreadyCheckedIn(Exception):
    pass


class AlreadyCheckedOut(Exception):
    pass


class NoCheckIn(Exception):
    pass


class NotStarted(Exception):
    pass


class FutureDate(Exception):
    pass


ERRORS = (AlreadyCheckedIn, AlreadyCheckedOut, NoCheckIn, NotStarted, FutureDate)


# ---------- small helpers ----------

def _as_utc(dt: datetime) -> datetime:
    # SQLite (used in tests) returns datetimes without a timezone, PostgreSQL returns them with one
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _local(dt: datetime) -> datetime:
    return _as_utc(dt).astimezone(clock.office_tz())


def _worked(record: Attendance) -> float | None:
    if record.check_out is None:
        return None
    return (_as_utc(record.check_out) - _as_utc(record.check_in)).total_seconds() / 3600


def _final_status(is_late: bool, hours: float, half_day_hours: float) -> DayStatus:
    if hours < half_day_hours:
        return DayStatus.half_day
    return DayStatus.late if is_late else DayStatus.present


def _find_record(db: Session, employee_id: int, day: date) -> Attendance | None:
    return (
        db.query(Attendance)
        .filter(Attendance.employee_id == employee_id, Attendance.date == day)
        .first()
    )


def _blank(day: date, status: DayStatus) -> dict:
    return {
        "date": day, "status": status, "check_in": None, "check_out": None,
        "worked_hours": None, "is_late": False, "source": None,
    }


def _from_record(record: Attendance, today: date) -> dict:
    hours = _worked(record)
    if record.check_out is None and record.date < today:
        status = DayStatus.missing_checkout
    else:
        status = record.status
    return {
        "date": record.date,
        "status": status,
        "check_in": _local(record.check_in),
        "check_out": _local(record.check_out) if record.check_out else None,
        "worked_hours": round(hours, 2) if hours is not None else None,
        "is_late": record.is_late,
        "source": record.source,
    }


def _approved_leave_days(db: Session, employee_id: int, start: date, end: date) -> set[date]:
    # Phase 5 will return the employee's approved leave days here.
    return set()


def _employees_on_leave(db: Session, day: date) -> set[int]:
    # Phase 5 will return the ids of employees on approved leave on this day.
    return set()


def build_day(day, employee, record, weekend_days, holidays, leave_days, today) -> dict | None:
    if record is not None:
        return _from_record(record, today)
    if day < employee.join_date or (employee.exit_date and day > employee.exit_date):
        return None  # not employed on this day
    if day.weekday() in weekend_days:
        return _blank(day, DayStatus.weekend)
    if day in holidays:
        return _blank(day, DayStatus.holiday)
    if day in leave_days:
        return _blank(day, DayStatus.on_leave)
    if day > today:
        return _blank(day, DayStatus.upcoming)
    if day == today:
        return _blank(day, DayStatus.not_checked_in)
    return _blank(day, DayStatus.absent)


def _entries_for_range(db: Session, employee: Employee, start: date, end: date, cfg) -> list[dict]:
    today = clock.today()
    weekend = cfg.weekend_days or []
    holidays = calendar_service.holiday_dates(db, start, end)
    leave_days = _approved_leave_days(db, employee.id, start, end)
    records = {
        r.date: r
        for r in db.query(Attendance).filter(
            Attendance.employee_id == employee.id,
            Attendance.date >= start,
            Attendance.date <= end,
        )
    }
    entries = []
    day = start
    while day <= end:
        entry = build_day(day, employee, records.get(day), weekend, holidays, leave_days, today)
        if entry is not None:
            entries.append(entry)
        day += timedelta(days=1)
    return entries


# ---------- employee actions ----------

def check_in(db: Session, user: User) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    cfg = settings_service.get_settings(db)
    local = clock.now()
    today = local.date()

    if today < employee.join_date:
        raise NotStarted()
    if _find_record(db, employee.id, today) is not None:
        raise AlreadyCheckedIn()
    is_late = is_late_arrival(cfg, local)

    #start = datetime.combine(today, cfg.office_start).replace(tzinfo=clock.office_tz())
    #is_late = local > start + timedelta(minutes=cfg.grace_minutes)

    record = Attendance(
        employee_id=employee.id,
        date=today,
        check_in=local.astimezone(timezone.utc),
        status=DayStatus.late if is_late else DayStatus.present,
        is_late=is_late,
        source="web",
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:  # two requests at the same moment: the database says no
        db.rollback()
        raise AlreadyCheckedIn()
    db.refresh(record)
    return _from_record(record, today)


def check_out(db: Session, user: User) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    cfg = settings_service.get_settings(db)
    local = clock.now()
    today = local.date()

    record = _find_record(db, employee.id, today)
    if record is None:
        raise NoCheckIn()
    if record.check_out is not None:
        raise AlreadyCheckedOut()

    record.check_out = local.astimezone(timezone.utc)
    record.status = _final_status(record.is_late, _worked(record), cfg.half_day_hours)
    db.commit()
    db.refresh(record)
    return _from_record(record, today)


def today_entry(db: Session, user: User) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    cfg = settings_service.get_settings(db)
    today = clock.today()
    entries = _entries_for_range(db, employee, today, today, cfg)
    if not entries:
        raise NotStarted()
    return entries[0]


# ---------- calendars ----------

def _summarize(entries: list[dict]) -> dict:
    counts = Counter(e["status"].value for e in entries if e["status"] != DayStatus.upcoming)
    return {
        "counts": dict(counts),
        "late_count": sum(1 for e in entries if e["is_late"]),
        "total_hours": round(sum(e["worked_hours"] or 0 for e in entries), 2),
    }


def month_calendar(db: Session, employee: Employee, month: str | None) -> dict:
    today = clock.today()
    if month:
        year, mon = int(month[:4]), int(month[5:7])
    else:
        year, mon = today.year, today.month
    last_day = monthrange(year, mon)[1]
    cfg = settings_service.get_settings(db)
    entries = _entries_for_range(db, employee, date(year, mon, 1), date(year, mon, last_day), cfg)
    return {"month": f"{year}-{mon:02d}", "days": entries, "summary": _summarize(entries)}


def my_calendar(db: Session, user: User, month: str | None) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    return month_calendar(db, employee, month)


def employee_calendar(db: Session, employee_id: int, month: str | None) -> dict:
    employee = employee_service.get_employee(db, employee_id)
    result = month_calendar(db, employee, month)
    result["employee"] = {
        "id": employee.id,
        "employee_code": employee.employee_code,
        "full_name": employee.full_name,
    }
    return result


# ---------- daily board (HR and Admin) ----------

def daily_board(
    db: Session,
    day: date | None,
    department_id: int | None,
    search: str | None,
    status: DayStatus | None,
    limit: int,
    offset: int,
) -> dict:
    today = clock.today()
    day = day or today
    if day > today:
        raise FutureDate()

    cfg = settings_service.get_settings(db)
    weekend = cfg.weekend_days or []
    holidays = calendar_service.holiday_dates(db, day, day)
    on_leave = _employees_on_leave(db, day)

    query = (
        db.query(Employee)
        .join(User, Employee.user_id == User.id)
        .filter(Employee.join_date <= day)
        .filter(or_(Employee.exit_date.is_(None), Employee.exit_date >= day))
    )
    if department_id is not None:
        query = query.filter(Employee.department_id == department_id)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Employee.full_name.ilike(pattern),
                Employee.employee_code.ilike(pattern),
                User.email.ilike(pattern),
            )
        )
    employees = (
        query.options(joinedload(Employee.department))
        .order_by(func.lower(Employee.full_name), Employee.id)
        .all()
    )

    records = {r.employee_id: r for r in db.query(Attendance).filter(Attendance.date == day)}

    items = []
    for emp in employees:
        leave_days = {day} if emp.id in on_leave else set()
        entry = build_day(day, emp, records.get(emp.id), weekend, holidays, leave_days, today)
        if entry is None:
            continue
        items.append({
            **entry,
            "employee_id": emp.id,
            "employee_code": emp.employee_code,
            "full_name": emp.full_name,
            "department": {"id": emp.department.id, "name": emp.department.name},
        })

    summary = dict(Counter(item["status"].value for item in items))
    if status is not None:
        items = [item for item in items if item["status"] == status]

    return {
        "date": day,
        "summary": summary,
        "items": items[offset: offset + limit],
        "total": len(items),
    }

# ---------- shared with the corrections service ----------

as_utc = _as_utc
to_local = _local
final_status = _final_status
find_record = _find_record
entries_for_range = _entries_for_range


def is_late_arrival(cfg, local_dt: datetime) -> bool:
    """The single place that decides if an arrival is late: after office start + grace."""
    start = datetime.combine(local_dt.date(), cfg.office_start).replace(tzinfo=clock.office_tz())
    return local_dt > start + timedelta(minutes=cfg.grace_minutes)