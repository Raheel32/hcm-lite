from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core import clock
from app.models.attendance import Attendance, DayStatus
from app.models.correction import AttendanceCorrection, CorrectionStatus
from app.models.user import User
from app.schemas.correction import CorrectionCreate
from app.services import attendance_service as attendance
from app.services import employee_service, settings_service
from app.services import audit_service as audit
from app.services import notification_service as notifications

CORRECTION_WINDOW_DAYS = 30


class CorrectionNotFound(Exception):
    pass


class DuplicatePending(Exception):
    pass


class NotPending(Exception):
    pass


class BadDate(Exception):
    pass


class BadTimes(Exception):
    pass


class CannotApply(Exception):
    pass


ERRORS = (CorrectionNotFound, DuplicatePending, NotPending, BadDate, BadTimes, CannotApply)

def _attendance_snapshot(record) -> dict | None:
    """How an attendance record looks, for the audit log."""
    if record is None:
        return None
    return {
        "check_in": attendance.to_local(record.check_in).isoformat(),
        "check_out": attendance.to_local(record.check_out).isoformat() if record.check_out else None,
        "status": record.status.value,
        "is_late": record.is_late,
    }


# ---------- rules ----------

def _employment_problem(employee, day) -> str | None:
    if day < employee.join_date:
        return "The date is before the employee's joining date"
    if employee.exit_date and day > employee.exit_date:
        return "The date is after the employee's exit date"
    return None


def _request_date_problem(employee, day, today) -> str | None:
    if day > today:
        return "The date cannot be in the future"
    if day < today - timedelta(days=CORRECTION_WINDOW_DAYS):
        return f"Corrections can only be requested for the last {CORRECTION_WINDOW_DAYS} days"
    return _employment_problem(employee, day)


def _merged(record, check_in, check_out):
    """The day as it would look after the change: requested times win, the record fills the gaps."""
    new_in = check_in if check_in is not None else (record.check_in if record else None)
    new_out = check_out if check_out is not None else (record.check_out if record else None)
    return new_in, new_out


def _result_problem(record, check_in, check_out) -> str | None:
    if record is None and (check_in is None or check_out is None):
        return "This day has no attendance record, so give both a check-in and a check-out time"
    new_in, new_out = _merged(record, check_in, check_out)
    if new_in is not None and new_out is not None and attendance.as_utc(new_out) <= attendance.as_utc(new_in):
        return "The check-out time must be after the check-in time"
    return None


# ---------- output ----------

def to_out(db: Session, correction: AttendanceCorrection, cfg=None) -> dict:
    cfg = cfg or settings_service.get_settings(db)
    employee = correction.employee
    entries = attendance.entries_for_range(db, employee, correction.date, correction.date, cfg)

    def local(value):
        return attendance.to_local(value) if value is not None else None

    return {
        "id": correction.id,
        "employee": {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
        },
        "date": correction.date,
        "requested_check_in": local(correction.requested_check_in),
        "requested_check_out": local(correction.requested_check_out),
        "reason": correction.reason,
        "status": correction.status,
        "review_comment": correction.review_comment,
        "reviewed_by": correction.reviewer.email if correction.reviewer else None,
        "reviewed_at": local(correction.reviewed_at),
        "created_at": local(correction.created_at),
        "current": entries[0] if entries else None,
    }


# ---------- employee ----------

def create_correction(db: Session, user: User, data: CorrectionCreate) -> AttendanceCorrection:
    employee = employee_service.get_employee_by_user(db, user.id)
    now = clock.now()

    problem = _request_date_problem(employee, data.date, now.date())
    if problem:
        raise BadDate(problem)

    tz = clock.office_tz()
    check_in = datetime.combine(data.date, data.check_in, tzinfo=tz) if data.check_in is not None else None
    check_out = datetime.combine(data.date, data.check_out, tzinfo=tz) if data.check_out is not None else None
    if (check_in is not None and check_in > now) or (check_out is not None and check_out > now):
        raise BadTimes("A time cannot be in the future")

    record = attendance.find_record(db, employee.id, data.date)
    problem = _result_problem(record, check_in, check_out)
    if problem:
        raise BadTimes(problem)

    already = (
        db.query(AttendanceCorrection.id)
        .filter(
            AttendanceCorrection.employee_id == employee.id,
            AttendanceCorrection.date == data.date,
            AttendanceCorrection.status == CorrectionStatus.pending,
        )
        .first()
    )
    if already is not None:
        raise DuplicatePending()

    correction = AttendanceCorrection(
        employee_id=employee.id,
        date=data.date,
        requested_check_in=check_in.astimezone(timezone.utc) if check_in is not None else None,
        requested_check_out=check_out.astimezone(timezone.utc) if check_out is not None else None,
        reason=data.reason,
        status=CorrectionStatus.pending,
    )
    db.add(correction)
    db.flush()
    notifications.notify_hr(
        db, "correction.submitted", "New attendance correction request",
        f"{employee.full_name} asked to correct their attendance for {data.date.isoformat()}.",
        entity_type="attendance_correction", entity_id=correction.id,
    )
    db.commit()
    db.refresh(correction)
    return correction


def cancel_correction(db: Session, user: User, correction_id: int) -> AttendanceCorrection:
    employee = employee_service.get_employee_by_user(db, user.id)
    correction = (
        db.query(AttendanceCorrection)
        .filter(AttendanceCorrection.id == correction_id, AttendanceCorrection.employee_id == employee.id)
        .first()
    )
    if correction is None:
        raise CorrectionNotFound()  # someone else's request looks the same as a missing one
    if correction.status != CorrectionStatus.pending:
        raise NotPending()
    correction.status = CorrectionStatus.cancelled
    db.commit()
    db.refresh(correction)
    return correction


# ---------- lists ----------

def _list(db: Session, query, limit: int, offset: int) -> dict:
    total = query.count()
    rows = (
        query.options(joinedload(AttendanceCorrection.employee), joinedload(AttendanceCorrection.reviewer))
        .order_by(AttendanceCorrection.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    cfg = settings_service.get_settings(db)
    return {"items": [to_out(db, row, cfg) for row in rows], "total": total}


def list_mine(db: Session, user: User, status: CorrectionStatus | None, limit: int, offset: int) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    query = db.query(AttendanceCorrection).filter(AttendanceCorrection.employee_id == employee.id)
    if status is not None:
        query = query.filter(AttendanceCorrection.status == status)
    return _list(db, query, limit, offset)


def list_all(db: Session, status: CorrectionStatus | None, employee_id: int | None, limit: int, offset: int) -> dict:
    query = db.query(AttendanceCorrection)
    if status is not None:
        query = query.filter(AttendanceCorrection.status == status)
    if employee_id is not None:
        query = query.filter(AttendanceCorrection.employee_id == employee_id)
    return _list(db, query, limit, offset)


# ---------- HR decisions ----------

def _get_pending(db: Session, correction_id: int) -> AttendanceCorrection:
    correction = db.get(AttendanceCorrection, correction_id)
    if correction is None:
        raise CorrectionNotFound()
    if correction.status != CorrectionStatus.pending:
        raise NotPending()
    return correction


def _stamp(correction: AttendanceCorrection, reviewer: User, status: CorrectionStatus, comment: str | None) -> None:
    correction.status = status
    correction.reviewed_by_id = reviewer.id
    correction.review_comment = comment
    correction.reviewed_at = datetime.now(timezone.utc)


def approve_correction(db: Session, reviewer: User, correction_id: int, comment: str | None) -> AttendanceCorrection:
    correction = _get_pending(db, correction_id)
    employee = correction.employee
    day = correction.date

    # Everything is checked again: the record may have changed since the request was made
    record = attendance.find_record(db, employee.id, day)
    before = _attendance_snapshot(record)  # taken now, before the record is changed
    problem = _employment_problem(employee, day) or _result_problem(
        record, correction.requested_check_in, correction.requested_check_out
    )
    if problem:
        raise CannotApply(f"This request can no longer be applied: {problem}")

    cfg = settings_service.get_settings(db)
    new_in, new_out = _merged(record, correction.requested_check_in, correction.requested_check_out)

    is_late = attendance.is_late_arrival(cfg, attendance.to_local(new_in))
    if new_out is not None:
        hours = (attendance.as_utc(new_out) - attendance.as_utc(new_in)).total_seconds() / 3600
        status = attendance.final_status(is_late, hours, cfg.half_day_hours)
    else:
        status = DayStatus.late if is_late else DayStatus.present

    if record is None:
        record = Attendance(employee_id=employee.id, date=day)
        db.add(record)
    record.check_in = attendance.as_utc(new_in)
    record.check_out = attendance.as_utc(new_out) if new_out is not None else None
    record.status = status
    record.is_late = is_late
    record.source = "correction"

    audit.record(
        db, "correction.approve", "attendance_correction", correction.id,
        f"Approved the attendance correction of {employee.full_name} for {day.isoformat()}",
        before=before, after=_attendance_snapshot(record),
    )
    notifications.notify(
        db, employee.user_id, "correction.approved", "Attendance correction approved",
        f"Your attendance for {day.isoformat()} was corrected."
        + (f" Note from HR: {comment}" if comment else ""),
        entity_type="attendance_correction", entity_id=correction.id,
    )
    _stamp(correction, reviewer, CorrectionStatus.approved, comment)
    try:
        db.commit()
    except IntegrityError:  # the employee checked in at the same moment
        db.rollback()
        raise CannotApply("The attendance record changed while approving, please try again")
    db.refresh(correction)
    return correction


def reject_correction(db: Session, reviewer: User, correction_id: int, comment: str) -> AttendanceCorrection:
    correction = _get_pending(db, correction_id)
    audit.record(
        db, "correction.reject", "attendance_correction", correction.id,
        f"Rejected the attendance correction of {correction.employee.full_name} for {correction.date.isoformat()}",
        before={"status": "pending"}, after={"status": "rejected", "comment": comment},
    )
    notifications.notify(
        db, correction.employee.user_id, "correction.rejected", "Attendance correction rejected",
        f"Your correction request for {correction.date.isoformat()} was rejected: {comment}",
        entity_type="attendance_correction", entity_id=correction.id,
    )
    _stamp(correction, reviewer, CorrectionStatus.rejected, comment)
    db.commit()
    db.refresh(correction)
    return correction