from calendar import month_name, monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.core import clock
from app.core import payroll_rules as rules
from app.models.attendance import DayStatus
from app.models.company import Company
from app.models.employee import Employee, EmployeeStatus
from app.models.leave import LeaveRequest, LeaveStatus
from app.models.leave_config import LeaveType
from app.models.payroll import PayrollRun, Payslip, RunStatus
from app.models.user import Role, User
from app.services import attendance_service as attendance
from app.services import calendar_service, employee_service, settings_service
from app.services.payslip_pdf import build_payslip_pdf
from app.services import audit_service as audit
from app.services import notification_service as notifications



class NotFound(Exception):
    pass


class Invalid(Exception):  # 400
    pass


class Conflict(Exception):  # 409
    pass


ERRORS = (NotFound, Invalid, Conflict)

EMPTY_TOTALS = {
    "employees": 0,
    "gross_salary": 0.0,
    "prorated_gross": 0.0,
    "attendance_deduction": 0.0,
    "tax": 0.0,
    "net_pay": 0.0,
}


# ---------- helpers ----------

def _local(value):
    return attendance.to_local(value) if value is not None else None


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def _unpaid_leave_dates(db: Session, employee_id: int, start: date, end: date) -> set[date]:
    rows = (
        db.query(LeaveRequest.start_date, LeaveRequest.end_date)
        .join(LeaveType, LeaveRequest.leave_type_id == LeaveType.id)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.status == LeaveStatus.approved,
            LeaveType.is_paid.is_(False),
            LeaveRequest.start_date <= end,
            LeaveRequest.end_date >= start,
        )
        .all()
    )
    days = set()
    for leave_start, leave_end in rows:
        day = max(leave_start, start)
        last = min(leave_end, end)
        while day <= last:
            days.add(day)
            day += timedelta(days=1)
    return days


def _count_deductions(db: Session, employee: Employee, start: date, end: date, cfg, holidays: set[date]):
    """Absent, half and unpaid-leave days inside start..end, read the same way the attendance calendar reads them."""
    weekend = cfg.weekend_days or []
    unpaid_dates = _unpaid_leave_dates(db, employee.id, start, end)
    absent = half = unpaid = 0
    for entry in attendance.entries_for_range(db, employee, start, end, cfg):
        day = entry["date"]
        if not calendar_service.is_working_day(day, weekend, holidays):
            continue  # a weekend or holiday never costs anything, even with a check-in
        status = entry["status"]
        if status == DayStatus.absent:
            absent += 1
        elif status == DayStatus.half_day:
            half += 1
        elif status == DayStatus.on_leave and day in unpaid_dates:
            unpaid += 1
    return absent, half, unpaid


def _populate(db: Session, run: PayrollRun, cfg) -> None:
    """(Re)build every payslip of a draft run from the current data."""
    month_start, month_end = _month_bounds(run.year, run.month)
    weekend = cfg.weekend_days or []
    holidays = calendar_service.holiday_dates(db, month_start, month_end)
    working_days = calendar_service.count_working_days(month_start, month_end, weekend, holidays)
    if working_days == 0:
        raise Invalid("This month has no working days")

    db.flush()  # gives a new run its id
    db.query(Payslip).filter(Payslip.run_id == run.id).delete(synchronize_session=False)

    employees = (
        db.query(Employee)
        .filter(Employee.join_date <= month_end)
        .filter(or_(Employee.exit_date.is_(None), Employee.exit_date >= month_start))
        .options(joinedload(Employee.department), joinedload(Employee.designation))
        .order_by(Employee.id)
        .all()
    )

    created = 0
    for emp in employees:
        start = max(month_start, emp.join_date)
        end = min(month_end, emp.exit_date) if emp.exit_date else month_end
        payable_days = calendar_service.count_working_days(start, end, weekend, holidays)
        if payable_days == 0:
            continue  # employed only on weekends or holidays this month

        absent, half, unpaid = _count_deductions(db, emp, start, end, cfg, holidays)
        deduction_days = Decimal(absent + unpaid) + Decimal("0.5") * half
        parts = rules.salary_components(emp.basic_salary)
        pay = rules.calculate_pay(parts["gross"], working_days, payable_days, deduction_days)

        db.add(Payslip(
            run_id=run.id,
            employee_id=emp.id,
            employee_code=emp.employee_code,
            full_name=emp.full_name,
            department_id=emp.department_id,
            department_name=emp.department.name,
            designation_name=emp.designation.name,
            basic=float(parts["basic"]),
            hra=float(parts["hra"]),
            medical=float(parts["medical"]),
            gross_salary=float(parts["gross"]),
            working_days=working_days,
            payable_days=payable_days,
            absent_days=absent,
            unpaid_days=unpaid,
            half_days=half,
            deduction_days=float(deduction_days),
            per_day_rate=float(pay["per_day_rate"]),
            prorated_gross=float(pay["prorated_gross"]),
            attendance_deduction=float(pay["attendance_deduction"]),
            tax=float(pay["tax"]),
            net_pay=float(pay["net_pay"]),
        ))
        created += 1

    if created == 0:
        raise Invalid("There are no employees to pay for this month")

    run.working_days = working_days
    run.as_of_date = clock.today()
    run.calculated_at = datetime.now(timezone.utc)


# ---------- output ----------

def _totals(db: Session, run_ids: list[int]) -> dict[int, dict]:
    if not run_ids:
        return {}
    rows = (
        db.query(
            Payslip.run_id,
            func.count(Payslip.id),
            func.sum(Payslip.gross_salary),
            func.sum(Payslip.prorated_gross),
            func.sum(Payslip.attendance_deduction),
            func.sum(Payslip.tax),
            func.sum(Payslip.net_pay),
        )
        .filter(Payslip.run_id.in_(run_ids))
        .group_by(Payslip.run_id)
        .all()
    )
    result = {}
    for run_id, count, gross, prorated, deduction, tax, net in rows:
        result[run_id] = {
            "employees": int(count),
            "gross_salary": round(float(gross or 0), 2),
            "prorated_gross": round(float(prorated or 0), 2),
            "attendance_deduction": round(float(deduction or 0), 2),
            "tax": round(float(tax or 0), 2),
            "net_pay": round(float(net or 0), 2),
        }
    return result


def _run_dict(run: PayrollRun, totals: dict) -> dict:
    _, month_end = _month_bounds(run.year, run.month)
    return {
        "id": run.id,
        "year": run.year,
        "month": run.month,
        "period": f"{run.year}-{run.month:02d}",
        "status": run.status,
        "working_days": run.working_days,
        "as_of_date": run.as_of_date,
        "month_in_progress": run.as_of_date <= month_end,  # the month was not over when it was calculated
        "created_by": run.created_by.email if run.created_by else None,
        "created_at": _local(run.created_at),
        "calculated_at": _local(run.calculated_at),
        "finalized_by": run.finalized_by.email if run.finalized_by else None,
        "finalized_at": _local(run.finalized_at),
        "totals": totals,
    }


def run_out(db: Session, run: PayrollRun) -> dict:
    return _run_dict(run, _totals(db, [run.id]).get(run.id, EMPTY_TOTALS))


def payslip_out(slip: Payslip) -> dict:
    run = slip.run
    return {
        "id": slip.id,
        "run_id": run.id,
        "year": run.year,
        "month": run.month,
        "period": f"{run.year}-{run.month:02d}",
        "run_status": run.status,
        "employee": {"id": slip.employee_id, "employee_code": slip.employee_code, "full_name": slip.full_name},
        "department": slip.department_name,
        "designation": slip.designation_name,
        "basic": slip.basic,
        "hra": slip.hra,
        "medical": slip.medical,
        "gross_salary": slip.gross_salary,
        "working_days": slip.working_days,
        "payable_days": slip.payable_days,
        "absent_days": slip.absent_days,
        "unpaid_days": slip.unpaid_days,
        "half_days": slip.half_days,
        "deduction_days": slip.deduction_days,
        "per_day_rate": slip.per_day_rate,
        "prorated_gross": slip.prorated_gross,
        "attendance_deduction": slip.attendance_deduction,
        "tax": slip.tax,
        "net_pay": slip.net_pay,
    }


# ---------- runs (HR and Admin) ----------

def _get_run(db: Session, run_id: int) -> PayrollRun:
    run = db.get(PayrollRun, run_id)
    if run is None:
        raise NotFound()
    return run


def _require_draft(run: PayrollRun) -> None:
    if run.status == RunStatus.finalized:
        raise Conflict("This payroll run is finalized and locked")


def create_run(db: Session, user: User, year: int, month: int) -> PayrollRun:
    today = clock.today()
    if (year, month) > (today.year, today.month):
        raise Invalid("Payroll cannot be run for a future month")
    exists = db.query(PayrollRun.id).filter(PayrollRun.year == year, PayrollRun.month == month).first()
    if exists is not None:
        raise Conflict(f"A payroll run for {year}-{month:02d} already exists")

    cfg = settings_service.get_settings(db)
    run = PayrollRun(
        year=year,
        month=month,
        status=RunStatus.draft,
        working_days=0,
        as_of_date=today,
        created_by_id=user.id,
        calculated_at=datetime.now(timezone.utc),
    )
    db.add(run)
    try:
        _populate(db, run, cfg)
        totals = _totals(db, [run.id]).get(run.id, EMPTY_TOTALS)
        audit.record(
            db, "payroll.create_run", "payroll_run", run.id,
            f"Created the payroll draft for {year}-{month:02d}",
            after={"period": f"{year}-{month:02d}", "employees": totals["employees"], "net_pay": totals["net_pay"]},
        )
        db.commit()
    except IntegrityError:  # two requests for the same month at the same moment
        db.rollback()
        raise Conflict(f"A payroll run for {year}-{month:02d} already exists")
    except Exception:
        db.rollback()
        raise
    db.refresh(run)
    return run


def list_runs(db: Session, status: RunStatus | None, year: int | None, limit: int, offset: int) -> dict:
    query = db.query(PayrollRun)
    if status is not None:
        query = query.filter(PayrollRun.status == status)
    if year is not None:
        query = query.filter(PayrollRun.year == year)
    total = query.count()
    rows = (
        query.options(joinedload(PayrollRun.created_by), joinedload(PayrollRun.finalized_by))
        .order_by(PayrollRun.year.desc(), PayrollRun.month.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    totals = _totals(db, [row.id for row in rows])
    return {"items": [_run_dict(row, totals.get(row.id, EMPTY_TOTALS)) for row in rows], "total": total}


def get_run(db: Session, run_id: int) -> dict:
    return run_out(db, _get_run(db, run_id))


def list_run_payslips(
    db: Session,
    run_id: int,
    department_id: int | None,
    search: str | None,
    limit: int,
    offset: int,
) -> dict:
    _get_run(db, run_id)
    query = db.query(Payslip).filter(Payslip.run_id == run_id)
    if department_id is not None:
        query = query.filter(Payslip.department_id == department_id)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(Payslip.full_name.ilike(pattern), Payslip.employee_code.ilike(pattern)))
    total = query.count()
    rows = (
        query.options(joinedload(Payslip.run))
        .order_by(func.lower(Payslip.full_name), Payslip.id)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {"items": [payslip_out(row) for row in rows], "total": total}


def recalculate_run(db: Session, run_id: int) -> PayrollRun:
    run = _get_run(db, run_id)
    _require_draft(run)
    cfg = settings_service.get_settings(db)
    before = _totals(db, [run.id]).get(run.id, EMPTY_TOTALS)
    try:
        _populate(db, run, cfg)
        after = _totals(db, [run.id]).get(run.id, EMPTY_TOTALS)
        audit.record(
            db, "payroll.recalculate", "payroll_run", run.id,
            f"Recalculated the payroll draft for {run.year}-{run.month:02d}",
            before={"employees": before["employees"], "net_pay": before["net_pay"]},
            after={"employees": after["employees"], "net_pay": after["net_pay"]},
        )
        db.commit()
    except Exception:
        db.rollback()  # the old payslips stay as they were
        raise
    db.refresh(run)
    return run


def delete_run(db: Session, run_id: int) -> None:
    run = _get_run(db, run_id)
    _require_draft(run)
    db.delete(run)
    db.commit()


def finalize_run(db: Session, admin: User, run_id: int) -> PayrollRun:
    run = _get_run(db, run_id)
    if run.status == RunStatus.finalized:
        raise Conflict("This payroll run is already finalized")
    run.status = RunStatus.finalized
    run.finalized_by_id = admin.id
    run.finalized_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


# ---------- payslips (everyone, with scope) ----------

def _load_payslip(db: Session, user: User, payslip_id: int) -> Payslip:
    slip = db.query(Payslip).options(joinedload(Payslip.run)).filter(Payslip.id == payslip_id).first()
    if slip is None:
        raise NotFound()
    if user.role == Role.employee:
        employee = employee_service.get_employee_by_user(db, user.id)
        # someone else's payslip, or a draft, looks exactly like a missing one
        if slip.employee_id != employee.id or slip.run.status != RunStatus.finalized:
            raise NotFound()
    return slip


def get_payslip(db: Session, user: User, payslip_id: int) -> dict:
    return payslip_out(_load_payslip(db, user, payslip_id))


def list_my_payslips(db: Session, user: User, year: int | None, limit: int, offset: int) -> dict:
    employee = employee_service.get_employee_by_user(db, user.id)
    query = (
        db.query(Payslip)
        .join(PayrollRun, Payslip.run_id == PayrollRun.id)
        .filter(Payslip.employee_id == employee.id, PayrollRun.status == RunStatus.finalized)
    )
    if year is not None:
        query = query.filter(PayrollRun.year == year)
    total = query.count()
    rows = (
        query.options(contains_eager(Payslip.run))
        .order_by(PayrollRun.year.desc(), PayrollRun.month.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {"items": [payslip_out(row) for row in rows], "total": total}


def payslip_pdf(db: Session, user: User, payslip_id: int) -> tuple[bytes, str]:
    slip = _load_payslip(db, user, payslip_id)
    company = db.query(Company).first()
    content = build_payslip_pdf(slip, company.name if company else "HCM Lite")
    return content, f"payslip-{slip.employee_code}-{slip.run.year}-{slip.run.month:02d}.pdf"


# ---------- salary structure ----------

def _structure_out(employee: Employee) -> dict:
    parts = rules.salary_components(employee.basic_salary)
    return {
        "employee": {
            "id": employee.id,
            "employee_code": employee.employee_code,
            "full_name": employee.full_name,
        },
        "basic_salary": float(parts["basic"]),
        "hra": float(parts["hra"]),
        "medical": float(parts["medical"]),
        "gross_salary": float(parts["gross"]),
        "annual_gross": float(parts["gross"] * 12),
        "estimated_monthly_tax": float(rules.monthly_tax(parts["gross"])),
    }


def salary_structure(db: Session, employee_id: int) -> dict:
    return _structure_out(employee_service.get_employee(db, employee_id))


def update_salary_structure(db: Session, employee_id: int, basic_salary: float) -> dict:
    employee = employee_service.get_employee(db, employee_id)
    if employee.status == EmployeeStatus.exited:
        raise Conflict("This employee has already exited")
    employee.basic_salary = basic_salary
    db.commit()
    db.refresh(employee)
    return _structure_out(employee)