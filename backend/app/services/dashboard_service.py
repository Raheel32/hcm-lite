from datetime import date, timedelta

from sqlalchemy.orm import Session, contains_eager

from app.core import clock
from app.models.attendance import Attendance
from app.models.payroll import PayrollRun, Payslip, RunStatus
from app.models.user import User
from app.services import announcement_service, audit_service, calendar_service
from app.services import attendance_service as attendance
from app.services import employee_service, leave_service
from app.services import metrics_service as metrics
from app.services import notification_service, settings_service

WEEK_TARGET_HOURS = 40.0


def _range(start: date, end: date, department_id: int | None) -> dict:
    return {"date_from": start, "date_to": end, "department_id": department_id}


def _latest_announcements(db: Session, user: User) -> list[dict]:
    return announcement_service.list_announcements(db, user, False, 5, 0)["items"]


# ---------- Admin ----------

def admin_dashboard(db: Session, user: User, date_from, date_to, department_id) -> dict:
    start, end = metrics.resolve_range(date_from, date_to)
    metrics.check_department(db, department_id)
    payroll = metrics.payroll_summary(db, start, end, department_id)
    return {
        "range": _range(start, end, department_id),
        "kpis": {
            "headcount": metrics.headcount(db, end, department_id),
            "attrition_ytd": metrics.attrition(db, date(end.year, 1, 1), end, department_id),
            "payroll_cost": payroll["totals"],
            "average_salary": metrics.average_gross(db, end, department_id),
        },
        "headcount_by_department": metrics.headcount_by_department(db, end, department_id),
        "payroll_by_department": payroll["by_department"],
        "payroll_by_month": payroll["by_month"],
        "average_salary_by_department": metrics.average_gross_by_department(db, end, department_id),
        "average_salary_by_designation": metrics.average_gross_by_designation(db, end, department_id),
        "gender": metrics.gender_breakdown(db, end, department_id),
        "tenure": metrics.tenure_breakdown(db, end, department_id),
        "audit_feed": audit_service.list_entries(db, None, None, None, None, None, None, None, 5, 0)["items"],
        "announcements": _latest_announcements(db, user),
    }


# ---------- HR ----------

def hr_dashboard(db: Session, user: User, date_from, date_to, department_id) -> dict:
    start, end = metrics.resolve_range(date_from, date_to)
    metrics.check_department(db, department_id)
    snapshot = metrics.today_snapshot(db, department_id)
    period = metrics.attendance_summary(db, start, end, department_id)
    return {
        "range": _range(start, end, department_id),
        "kpis": {
            "date": snapshot["date"],
            "headcount": snapshot["headcount"],
            "present": snapshot["present"],
            "late_today": snapshot["late"],
            "on_leave": snapshot["on_leave"],
            "not_checked_in": snapshot["not_checked_in"],
            "pending_approvals": metrics.pending_approvals(db, department_id),
        },
        "period": {
            "counts": period["counts"],
            "attendance_rate": period["attendance_rate"],
            "late_percent": period["late_percent"],
            "absenteeism_percent": period["absenteeism_percent"],
            "expected_working_days": period["expected_working_days"],
        },
        "attendance_trend": period["trend"],
        "absenteeism_by_department": period["by_department"],
        "top_late_comers": period["top_late_comers"],
        "leave_by_type": metrics.leave_by_type(db, start, end, department_id),
        "leave_year": end.year,
        "leave_utilization": metrics.leave_utilization(db, end, department_id),
        "announcements": _latest_announcements(db, user),
    }


# ---------- Employee ----------

def _week_hours(db: Session, employee_id: int, today: date) -> dict:
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = week_start + timedelta(days=6)
    now = attendance.as_utc(clock.now())
    worked = 0.0
    records = db.query(Attendance).filter(
        Attendance.employee_id == employee_id, Attendance.date >= week_start, Attendance.date <= week_end
    )
    for record in records:
        start = attendance.as_utc(record.check_in)
        if record.check_out is not None:
            worked += max(0.0, (attendance.as_utc(record.check_out) - start).total_seconds() / 3600)
        elif record.date == today:  # still at work: count the hours so far
            worked += max(0.0, (now - start).total_seconds() / 3600)
        # a past day with no check-out has unknown hours, so it adds nothing
    worked = round(worked, 2)
    return {
        "week_start": week_start,
        "week_end": week_end,
        "worked_hours": worked,
        "target_hours": WEEK_TARGET_HOURS,
        "percent": round(worked / WEEK_TARGET_HOURS * 100, 1),
    }


def _late_count_this_month(db: Session, employee_id: int, today: date) -> int:
    cfg = settings_service.get_settings(db)
    month_start = today.replace(day=1)
    holidays = calendar_service.holiday_dates(db, month_start, today)
    rows = db.query(Attendance.date).filter(
        Attendance.employee_id == employee_id,
        Attendance.is_late.is_(True),
        Attendance.date >= month_start,
        Attendance.date <= today,
    )
    weekend = cfg.weekend_days or []
    return sum(1 for (day,) in rows if calendar_service.is_working_day(day, weekend, holidays))


def _last_payslip(db: Session, employee_id: int) -> dict | None:
    slip = (
        db.query(Payslip)
        .join(PayrollRun, Payslip.run_id == PayrollRun.id)
        .filter(Payslip.employee_id == employee_id, PayrollRun.status == RunStatus.finalized)
        .options(contains_eager(Payslip.run))
        .order_by(PayrollRun.year.desc(), PayrollRun.month.desc())
        .first()
    )
    if slip is None:
        return None
    return {
        "id": slip.id,
        "period": f"{slip.run.year}-{slip.run.month:02d}",
        "net_pay": slip.net_pay,
        "gross_pay": slip.prorated_gross,
    }


def employee_dashboard(db: Session, user: User) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    today = clock.today()
    try:
        today_entry = attendance.today_entry(db, user)
    except attendance.NotStarted:
        today_entry = None  # the joining date has not arrived yet
    return {
        "employee": {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
        },
        "today": today_entry,
        "week": _week_hours(db, employee.id, today),
        "month": f"{today.year}-{today.month:02d}",
        "late_count_month": _late_count_this_month(db, employee.id, today),
        "leave_balance": leave_service.my_balance(db, user, None),
        "last_payslip": _last_payslip(db, employee.id),
        "announcements": _latest_announcements(db, user),
        "unread_notifications": notification_service.unread_count(db, user),
    }