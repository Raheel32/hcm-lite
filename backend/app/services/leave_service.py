from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core import clock
from app.models.employee import Employee
from app.models.leave import LeaveRequest, LeaveStatus
from app.models.leave_config import LeaveType
from app.models.user import User
from app.schemas.leave import LeaveApply
from app.services import attendance_service as attendance
from app.services import calendar_service, employee_service, settings_service
from app.services import audit_service as audit
from app.services import notification_service as notifications

PAST_WINDOW_DAYS = 30
FUTURE_WINDOW_DAYS = 365


class NotFound(Exception):
    pass


class Invalid(Exception):  # 400
    pass


class Unprocessable(Exception):  # 422
    pass


class Conflict(Exception):  # 409
    pass


ERRORS = (NotFound, Invalid, Unprocessable, Conflict)


# ---------- balance ----------

def _totals(db: Session, employee_id: int, year: int, exclude_id: int | None = None) -> dict:
    """Approved and pending days per leave type for one calendar year."""
    query = db.query(LeaveRequest.leave_type_id, LeaveRequest.status, func.sum(LeaveRequest.days)).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status.in_([LeaveStatus.approved, LeaveStatus.pending]),
        LeaveRequest.start_date >= date(year, 1, 1),
        LeaveRequest.start_date <= date(year, 12, 31),
    )
    if exclude_id is not None:
        query = query.filter(LeaveRequest.id != exclude_id)
    rows = query.group_by(LeaveRequest.leave_type_id, LeaveRequest.status).all()
    return {(type_id, LeaveStatus(status)): int(total) for type_id, status, total in rows}


def _available(db: Session, employee_id: int, leave_type: LeaveType, year: int) -> int | None:
    """Days still free to request (approved and pending both count). None means unlimited."""
    if leave_type.yearly_quota is None:
        return None
    totals = _totals(db, employee_id, year)
    used = totals.get((leave_type.id, LeaveStatus.approved), 0)
    pending = totals.get((leave_type.id, LeaveStatus.pending), 0)
    return leave_type.yearly_quota - used - pending


def balance_for(db: Session, employee: Employee, year: int | None) -> dict:
    year = year or clock.today().year
    totals = _totals(db, employee.id, year)
    leave_types = (
        db.query(LeaveType).filter(LeaveType.is_active.is_(True)).order_by(func.lower(LeaveType.name)).all()
    )
    items = []
    for lt in leave_types:
        used = totals.get((lt.id, LeaveStatus.approved), 0)
        pending = totals.get((lt.id, LeaveStatus.pending), 0)
        unlimited = lt.yearly_quota is None
        items.append({
            "leave_type_id": lt.id,
            "leave_type": lt.name,
            "is_paid": lt.is_paid,
            "is_unlimited": unlimited,
            "yearly_quota": lt.yearly_quota,
            "used": used,
            "pending": pending,
            "remaining": None if unlimited else max(0, lt.yearly_quota - used - pending),
        })
    return {"year": year, "items": items}


def my_balance(db: Session, user: User, year: int | None) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    return balance_for(db, employee, year)


def employee_balance(db: Session, employee_id: int, year: int | None) -> dict:
    employee = employee_service.get_employee(db, employee_id)
    return balance_for(db, employee, year)


# ---------- the rules for a request ----------

def _overlapping(db: Session, employee_id: int, start: date, end: date, exclude_id: int | None = None) -> bool:
    query = db.query(LeaveRequest.id).filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status.in_([LeaveStatus.pending, LeaveStatus.approved]),
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    )
    if exclude_id is not None:
        query = query.filter(LeaveRequest.id != exclude_id)
    return query.first() is not None


def _check_request(db: Session, employee: Employee, leave_type_id: int, start: date, end: date):
    """Every rule for a new request. Returns (leave type, working days, days available before)."""
    leave_type = db.get(LeaveType, leave_type_id)
    if leave_type is None or not leave_type.is_active:
        raise Invalid("Unknown or inactive leave type")
    if end < start:
        raise Invalid("The end date cannot be before the start date")
    if start.year != end.year:
        raise Unprocessable("Leave cannot span two calendar years, please apply for each year separately")

    today = clock.today()
    if start < today - timedelta(days=PAST_WINDOW_DAYS):
        raise Invalid(f"Leave can only be requested up to {PAST_WINDOW_DAYS} days back")
    if end > today + timedelta(days=FUTURE_WINDOW_DAYS):
        raise Invalid("Leave cannot be requested more than a year ahead")
    if start < employee.join_date:
        raise Invalid("The leave starts before the joining date")
    if employee.exit_date and end > employee.exit_date:
        raise Invalid("The leave goes past the exit date")

    cfg = settings_service.get_settings(db)
    holidays = calendar_service.holiday_dates(db, start, end)
    days = calendar_service.count_working_days(start, end, cfg.weekend_days or [], holidays)
    if days == 0:
        raise Unprocessable("These dates have no working days (weekends and holidays are skipped)")

    if _overlapping(db, employee.id, start, end):
        raise Unprocessable("You already have leave (pending or approved) on some of these dates")

    available = _available(db, employee.id, leave_type, start.year)
    if available is not None and days > available:
        raise Unprocessable(
            f"Not enough balance: {days} working days requested, {max(available, 0)} available"
        )
    return leave_type, days, available


# ---------- output ----------

def _local(value):
    return attendance.to_local(value) if value is not None else None


def to_out(leave: LeaveRequest) -> dict:
    employee = leave.employee
    leave_type = leave.leave_type
    return {
        "id": leave.id,
        "employee": {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
        },
        "leave_type": {"id": leave_type.id, "name": leave_type.name, "is_paid": leave_type.is_paid},
        "start_date": leave.start_date,
        "end_date": leave.end_date,
        "days": leave.days,
        "reason": leave.reason,
        "status": leave.status,
        "review_comment": leave.review_comment,
        "reviewed_by": leave.reviewer.email if leave.reviewer else None,
        "reviewed_at": _local(leave.reviewed_at),
        "created_at": _local(leave.created_at),
    }


# ---------- employee ----------

def preview(db: Session, user: User, leave_type_id: int, start: date, end: date) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    leave_type, days, available = _check_request(db, employee, leave_type_id, start, end)
    return {
        "working_days": days,
        "yearly_quota": leave_type.yearly_quota,
        "available_before": available,
        "available_after": None if available is None else available - days,
    }


def apply_leave(db: Session, user: User, data: LeaveApply) -> LeaveRequest:
    employee = employee_service.get_employee_by_user(db, user.id)
    leave_type, days, _ = _check_request(db, employee, data.leave_type_id, data.start_date, data.end_date)
    leave = LeaveRequest(
        employee_id=employee.id,
        leave_type_id=leave_type.id,
        start_date=data.start_date,
        end_date=data.end_date,
        days=days,
        reason=data.reason,
        status=LeaveStatus.pending,
    )
    db.add(leave)
    db.flush()
    notifications.notify_hr(
        db, "leave.submitted", "New leave request",
        f"{employee.full_name} requested {days} working day(s) of {leave_type.name} leave "
        f"({data.start_date.isoformat()} to {data.end_date.isoformat()}).",
        entity_type="leave_request", entity_id=leave.id,
    )
    db.commit()
    db.refresh(leave)
    return leave


def cancel_leave(db: Session, user: User, leave_id: int) -> LeaveRequest:
    employee = employee_service.get_employee_by_user(db, user.id)
    leave = (
        db.query(LeaveRequest)
        .filter(LeaveRequest.id == leave_id, LeaveRequest.employee_id == employee.id)
        .first()
    )
    if leave is None:
        raise NotFound()  # someone else's request looks the same as a missing one

    if leave.status == LeaveStatus.approved:
        if leave.start_date <= clock.today():
            raise Conflict("Approved leave can only be cancelled before it starts")
    elif leave.status != LeaveStatus.pending:
        raise Conflict("Only pending or upcoming approved leave can be cancelled")

    leave.status = LeaveStatus.cancelled
    db.commit()
    db.refresh(leave)
    return leave


# ---------- lists ----------

def _page(query, limit: int, offset: int) -> dict:
    total = query.count()
    rows = (
        query.options(
            joinedload(LeaveRequest.employee),
            joinedload(LeaveRequest.leave_type),
            joinedload(LeaveRequest.reviewer),
        )
        .order_by(LeaveRequest.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {"items": [to_out(row) for row in rows], "total": total}


def list_mine(db: Session, user: User, status: LeaveStatus | None, year: int | None, limit: int, offset: int) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    query = db.query(LeaveRequest).filter(LeaveRequest.employee_id == employee.id)
    if status is not None:
        query = query.filter(LeaveRequest.status == status)
    if year is not None:
        query = query.filter(LeaveRequest.start_date >= date(year, 1, 1), LeaveRequest.start_date <= date(year, 12, 31))
    return _page(query, limit, offset)


def list_all(
    db: Session,
    status: LeaveStatus | None,
    employee_id: int | None,
    leave_type_id: int | None,
    department_id: int | None,
    limit: int,
    offset: int,
) -> dict:
    query = db.query(LeaveRequest)
    if status is not None:
        query = query.filter(LeaveRequest.status == status)
    if employee_id is not None:
        query = query.filter(LeaveRequest.employee_id == employee_id)
    if leave_type_id is not None:
        query = query.filter(LeaveRequest.leave_type_id == leave_type_id)
    if department_id is not None:
        query = query.join(Employee, LeaveRequest.employee_id == Employee.id).filter(
            Employee.department_id == department_id
        )
    return _page(query, limit, offset)


# ---------- HR decisions ----------

def _get_pending(db: Session, leave_id: int) -> LeaveRequest:
    leave = db.get(LeaveRequest, leave_id)
    if leave is None:
        raise NotFound()
    if leave.status != LeaveStatus.pending:
        raise Conflict("Only pending requests can be decided")
    return leave


def _stamp(leave: LeaveRequest, reviewer: User, status: LeaveStatus, comment: str | None) -> None:
    leave.status = status
    leave.reviewed_by_id = reviewer.id
    leave.review_comment = comment
    leave.reviewed_at = datetime.now(timezone.utc)


def approve_leave(db: Session, reviewer: User, leave_id: int, comment: str | None) -> LeaveRequest:
    leave = _get_pending(db, leave_id)
    employee = leave.employee
    leave_type = leave.leave_type

    # Re-check: things may have changed since the request was made
    if employee.exit_date and leave.end_date > employee.exit_date:
        raise Conflict("This leave goes past the employee's exit date")
    if _overlapping(db, employee.id, leave.start_date, leave.end_date, exclude_id=leave.id):
        raise Conflict("This leave overlaps with other leave of the same employee")
    if leave_type.yearly_quota is not None:
        totals = _totals(db, employee.id, leave.start_date.year, exclude_id=leave.id)
        used = totals.get((leave_type.id, LeaveStatus.approved), 0)
        if used + leave.days > leave_type.yearly_quota:
            raise Conflict(
                f"Approving would exceed the yearly quota of {leave_type.yearly_quota} days "
                f"({used} already approved)"
            )

    audit.record(
        db, "leave.approve", "leave_request", leave.id,
        f"Approved {leave.days} day(s) of {leave_type.name} leave for {employee.full_name} "
        f"({leave.start_date.isoformat()} to {leave.end_date.isoformat()})",
        before={"status": "pending"}, after={"status": "approved", "comment": comment},
    )
    notifications.notify(
        db, employee.user_id, "leave.approved", "Leave approved",
        f"Your {leave_type.name} leave from {leave.start_date.isoformat()} to {leave.end_date.isoformat()} was approved."
        + (f" Note from HR: {comment}" if comment else ""),
        entity_type="leave_request", entity_id=leave.id,
    )
    _stamp(leave, reviewer, LeaveStatus.approved, comment)
    db.commit()
    db.refresh(leave)
    return leave


def reject_leave(db: Session, reviewer: User, leave_id: int, comment: str) -> LeaveRequest:
    leave = _get_pending(db, leave_id)
    audit.record(
        db, "leave.reject", "leave_request", leave.id,
        f"Rejected {leave.days} day(s) of {leave.leave_type.name} leave for {leave.employee.full_name} "
        f"({leave.start_date.isoformat()} to {leave.end_date.isoformat()})",
        before={"status": "pending"}, after={"status": "rejected", "comment": comment},
    )
    notifications.notify(
        db, leave.employee.user_id, "leave.rejected", "Leave rejected",
        f"Your {leave.leave_type.name} leave from {leave.start_date.isoformat()} to "
        f"{leave.end_date.isoformat()} was rejected: {comment}",
        entity_type="leave_request", entity_id=leave.id,
    )
    _stamp(leave, reviewer, LeaveStatus.rejected, comment)
    db.commit()
    db.refresh(leave)
    return leave