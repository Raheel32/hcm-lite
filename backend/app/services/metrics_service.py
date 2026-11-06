from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import and_, case, distinct, func, or_
from sqlalchemy.orm import Session, joinedload

from app.core import clock
from app.core import payroll_rules as rules
from app.models.attendance import Attendance
from app.models.correction import AttendanceCorrection, CorrectionStatus
from app.models.employee import Employee
from app.models.leave import LeaveRequest, LeaveStatus
from app.models.leave_config import LeaveType
from app.models.org import Department, Designation
from app.models.payroll import PayrollRun, Payslip, RunStatus
from app.services import attendance_service as attendance
from app.services import calendar_service, settings_service


class Invalid(Exception):  # 400
    pass


class NotFound(Exception):  # 404
    pass


MAX_RANGE_DAYS = 366
WORKED = ("present", "late", "half_day", "missing_checkout")   # the person was there
COUNTED = WORKED + ("absent", "on_leave")                      # statuses of a working day that is over


# ---------- inputs ----------

def resolve_range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    end = date_to or clock.today()
    start = date_from or end.replace(day=1)
    if start.year < 2000 or end.year > 2100:
        raise Invalid("Dates must be between the years 2000 and 2100")
    if start > end:
        raise Invalid("date_from cannot be after date_to")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise Invalid(f"The date range can be at most {MAX_RANGE_DAYS} days")
    return start, end


def check_department(db: Session, department_id: int | None) -> None:
    if department_id is not None and db.get(Department, department_id) is None:
        raise NotFound()


def _percent(part, whole) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def _employed_on(day: date):
    return and_(Employee.join_date <= day, or_(Employee.exit_date.is_(None), Employee.exit_date >= day))


def _scope(query, department_id: int | None):
    return query if department_id is None else query.filter(Employee.department_id == department_id)


# ---------- headcount, attrition, people (SQL) ----------

def headcount(db: Session, day: date, department_id: int | None = None) -> int:
    query = db.query(func.count(Employee.id)).filter(_employed_on(day))
    return _scope(query, department_id).scalar() or 0


def attrition(db: Session, start: date, end: date, department_id: int | None = None) -> dict:
    query = db.query(func.count(Employee.id)).filter(Employee.exit_date >= start, Employee.exit_date <= end)
    exits = _scope(query, department_id).scalar() or 0
    average = (headcount(db, start, department_id) + headcount(db, end, department_id)) / 2
    return {"exits": exits, "average_headcount": average, "percent": _percent(exits, average)}


def headcount_by_department(db: Session, day: date, department_id: int | None = None) -> list[dict]:
    query = (
        db.query(Department.id, Department.name, func.count(Employee.id))
        .join(Employee, Employee.department_id == Department.id)
        .filter(_employed_on(day))
    )
    rows = (
        _scope(query, department_id)
        .group_by(Department.id, Department.name)
        .order_by(func.lower(Department.name))
        .all()
    )
    return [{"department_id": i, "department": name, "headcount": count} for i, name, count in rows]


def gender_breakdown(db: Session, day: date, department_id: int | None = None) -> list[dict]:
    query = db.query(Employee.gender, func.count(Employee.id)).filter(_employed_on(day))
    counts = {"male": 0, "female": 0, "other": 0, "unspecified": 0}
    for gender, count in _scope(query, department_id).group_by(Employee.gender).all():
        counts[getattr(gender, "value", gender) or "unspecified"] += count
    return [{"gender": key, "count": value} for key, value in counts.items()]


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def tenure_breakdown(db: Session, day: date, department_id: int | None = None) -> list[dict]:
    one, three, five = _years_before(day, 1), _years_before(day, 3), _years_before(day, 5)
    query = db.query(
        func.sum(case((Employee.join_date > one, 1), else_=0)),
        func.sum(case((and_(Employee.join_date <= one, Employee.join_date > three), 1), else_=0)),
        func.sum(case((and_(Employee.join_date <= three, Employee.join_date > five), 1), else_=0)),
        func.sum(case((Employee.join_date <= five, 1), else_=0)),
    ).filter(_employed_on(day))
    values = [int(v or 0) for v in _scope(query, department_id).one()]
    labels = ["Under 1 year", "1-3 years", "3-5 years", "5+ years"]
    return [{"bucket": label, "count": value} for label, value in zip(labels, values)]


# ---------- salary (SQL) ----------

def _gross_from_average(average_basic) -> float | None:
    if average_basic is None:
        return None
    return float(rules.money(Decimal(str(average_basic)) * rules.GROSS_MULTIPLIER))


def average_gross(db: Session, day: date, department_id: int | None = None) -> dict:
    query = db.query(func.avg(Employee.basic_salary), func.count(Employee.id)).filter(_employed_on(day))
    average, count = _scope(query, department_id).one()
    return {"gross": _gross_from_average(average), "employees": count}


def average_gross_by_department(db: Session, day: date, department_id: int | None = None) -> list[dict]:
    query = (
        db.query(Department.id, Department.name, func.avg(Employee.basic_salary), func.count(Employee.id))
        .join(Employee, Employee.department_id == Department.id)
        .filter(_employed_on(day))
    )
    rows = (
        _scope(query, department_id)
        .group_by(Department.id, Department.name)
        .order_by(func.lower(Department.name))
        .all()
    )
    return [
        {"department_id": i, "department": name, "average_gross": _gross_from_average(avg), "employees": count}
        for i, name, avg, count in rows
    ]


def average_gross_by_designation(db: Session, day: date, department_id: int | None = None) -> list[dict]:
    query = (
        db.query(Designation.id, Designation.name, func.avg(Employee.basic_salary), func.count(Employee.id))
        .join(Employee, Employee.designation_id == Designation.id)
        .filter(_employed_on(day))
    )
    rows = (
        _scope(query, department_id)
        .group_by(Designation.id, Designation.name)
        .order_by(func.lower(Designation.name))
        .all()
    )
    return [
        {"designation_id": i, "designation": name, "average_gross": _gross_from_average(avg), "employees": count}
        for i, name, avg, count in rows
    ]


# ---------- payroll (SQL, finalized runs only) ----------

def _payslip_query(db: Session, status: RunStatus, start: date, end: date, department_id: int | None, *columns):
    key = PayrollRun.year * 100 + PayrollRun.month  # 2026-10 becomes 202610, so months compare as numbers
    query = (
        db.query(*columns)
        .select_from(Payslip)
        .join(PayrollRun, Payslip.run_id == PayrollRun.id)
        .filter(
            PayrollRun.status == status,
            key >= start.year * 100 + start.month,
            key <= end.year * 100 + end.month,
        )
    )
    if department_id is not None:
        query = query.filter(Payslip.department_id == department_id)
    return query


def _money(value) -> float:
    return round(float(value or 0), 2)


def payroll_summary(db: Session, start: date, end: date, department_id: int | None = None) -> dict:
    gross, net, tax, payslips, runs = _payslip_query(
        db, RunStatus.finalized, start, end, department_id,
        func.sum(Payslip.prorated_gross), func.sum(Payslip.net_pay), func.sum(Payslip.tax),
        func.count(Payslip.id), func.count(distinct(Payslip.run_id)),
    ).one()
    drafts = _payslip_query(
        db, RunStatus.draft, start, end, department_id, func.count(distinct(Payslip.run_id))
    ).scalar() or 0

    by_month = (
        _payslip_query(
            db, RunStatus.finalized, start, end, department_id,
            PayrollRun.year, PayrollRun.month,
            func.sum(Payslip.prorated_gross), func.sum(Payslip.net_pay), func.count(Payslip.id),
        )
        .group_by(PayrollRun.year, PayrollRun.month)
        .order_by(PayrollRun.year, PayrollRun.month)
        .all()
    )
    by_department = (
        _payslip_query(
            db, RunStatus.finalized, start, end, department_id,
            Payslip.department_id, func.max(Payslip.department_name),
            func.sum(Payslip.prorated_gross), func.sum(Payslip.net_pay),
        )
        .group_by(Payslip.department_id)
        .order_by(func.lower(func.max(Payslip.department_name)))
        .all()
    )
    return {
        "totals": {
            "gross": _money(gross), "net": _money(net), "tax": _money(tax),
            "payslips": int(payslips or 0), "finalized_runs": int(runs or 0), "draft_runs_excluded": int(drafts),
        },
        "by_month": [
            {"period": f"{year}-{month:02d}", "gross": _money(g), "net": _money(n), "employees": int(c)}
            for year, month, g, n, c in by_month
        ],
        "by_department": [
            {"department_id": i, "department": name, "gross": _money(g), "net": _money(n)}
            for i, name, g, n in by_department
        ],
    }


# ---------- leave and approvals (SQL) ----------

def pending_approvals(db: Session, department_id: int | None = None) -> dict:
    leave = (
        db.query(func.count(LeaveRequest.id))
        .select_from(LeaveRequest)
        .join(Employee, LeaveRequest.employee_id == Employee.id)
        .filter(LeaveRequest.status == LeaveStatus.pending)
    )
    corrections = (
        db.query(func.count(AttendanceCorrection.id))
        .select_from(AttendanceCorrection)
        .join(Employee, AttendanceCorrection.employee_id == Employee.id)
        .filter(AttendanceCorrection.status == CorrectionStatus.pending)
    )
    leave_count = _scope(leave, department_id).scalar() or 0
    correction_count = _scope(corrections, department_id).scalar() or 0
    return {"leave": leave_count, "corrections": correction_count, "total": leave_count + correction_count}


def leave_by_type(db: Session, start: date, end: date, department_id: int | None = None) -> list[dict]:
    query = (
        db.query(LeaveType.id, LeaveType.name, func.sum(LeaveRequest.days), func.count(LeaveRequest.id))
        .select_from(LeaveRequest)
        .join(LeaveType, LeaveRequest.leave_type_id == LeaveType.id)
        .join(Employee, LeaveRequest.employee_id == Employee.id)
        .filter(
            LeaveRequest.status == LeaveStatus.approved,
            LeaveRequest.start_date >= start,
            LeaveRequest.start_date <= end,
        )
    )
    rows = (
        _scope(query, department_id)
        .group_by(LeaveType.id, LeaveType.name)
        .order_by(func.lower(LeaveType.name))
        .all()
    )
    return [
        {"leave_type_id": i, "leave_type": name, "days": int(days or 0), "requests": int(count)}
        for i, name, days, count in rows
    ]


def leave_utilization(db: Session, day: date, department_id: int | None = None) -> list[dict]:
    """Approved days in the year of `day` divided by (yearly quota x headcount), per leave type and department."""
    types = db.query(LeaveType).filter(LeaveType.is_active.is_(True)).order_by(func.lower(LeaveType.name)).all()
    heads = {row["department_id"]: row["headcount"] for row in headcount_by_department(db, day, department_id)}

    query = (
        db.query(LeaveRequest.leave_type_id, Employee.department_id, func.sum(LeaveRequest.days))
        .select_from(LeaveRequest)
        .join(Employee, LeaveRequest.employee_id == Employee.id)
        .filter(
            LeaveRequest.status == LeaveStatus.approved,
            LeaveRequest.start_date >= date(day.year, 1, 1),
            LeaveRequest.start_date <= date(day.year, 12, 31),
        )
    )
    used = {
        (type_id, dept_id): int(total or 0)
        for type_id, dept_id, total in _scope(query, department_id)
        .group_by(LeaveRequest.leave_type_id, Employee.department_id)
        .all()
    }
    names = {row.id: row.name for row in db.query(Department.id, Department.name).all()}

    rows = []
    for leave_type in types:
        dept_ids = set(heads) | {dept for (type_id, dept) in used if type_id == leave_type.id}
        for dept_id in sorted(dept_ids, key=lambda d: names.get(d, "").lower()):
            people = heads.get(dept_id, 0)
            approved = used.get((leave_type.id, dept_id), 0)
            quota = leave_type.yearly_quota
            quota_total = quota * people if quota is not None else None
            rows.append({
                "leave_type_id": leave_type.id,
                "leave_type": leave_type.name,
                "department_id": dept_id,
                "department": names.get(dept_id, ""),
                "yearly_quota": quota,
                "headcount": people,
                "approved_days": approved,
                "quota_total": quota_total,
                "utilization_percent": _percent(approved, quota_total),
            })
    return rows

def on_leave_on(db: Session, day: date, department_id: int | None = None, limit: int = 50) -> dict:
    """Everyone whose approved leave covers this working day (a real check-in wins, as in the calendar)."""
    cfg = settings_service.get_settings(db)
    employees = _employees_in_scope(db, day, day, department_id)
    away = [
        emp for emp, entry, _working in iter_day_entries(db, employees, day, day, cfg)
        if entry["status"].value == "on_leave"
    ]
    leave_names: dict = {}
    if away:
        rows = (
            db.query(LeaveRequest.employee_id, LeaveType.name)
            .join(LeaveType, LeaveRequest.leave_type_id == LeaveType.id)
            .filter(
                LeaveRequest.employee_id.in_([emp.id for emp in away]),
                LeaveRequest.status == LeaveStatus.approved,
                LeaveRequest.start_date <= day,
                LeaveRequest.end_date >= day,
            )
            .all()
        )
        leave_names = {employee_id: name for employee_id, name in rows}
    by_type = Counter(leave_names.get(emp.id, "Unknown") for emp in away)
    away.sort(key=lambda emp: (emp.full_name.lower(), emp.id))
    return {
        "date": day,
        "total": len(away),
        "by_type": [{"leave_type": name, "count": count} for name, count in sorted(by_type.items())],
        "employees": [
            {
                "employee_code": emp.employee_code,
                "full_name": emp.full_name,
                "department": emp.department.name,
                "leave_type": leave_names.get(emp.id),
            }
            for emp in away[:limit]
        ],
    }


# ---------- attendance (one shared pass over the days) ----------

def _employees_in_scope(db: Session, start: date, end: date, department_id: int | None) -> list[Employee]:
    query = (
        db.query(Employee)
        .filter(Employee.join_date <= end)
        .filter(or_(Employee.exit_date.is_(None), Employee.exit_date >= start))
        .options(joinedload(Employee.department))
        .order_by(Employee.id)
    )
    return _scope(query, department_id).all()


def _load_inputs(db: Session, employees: list[Employee], start: date, end: date):
    """All attendance records and approved leave of these people, in two queries."""
    records: dict = {}
    leave_days: dict = defaultdict(set)
    ids = [emp.id for emp in employees]
    if not ids:
        return records, leave_days
    for record in db.query(Attendance).filter(
        Attendance.employee_id.in_(ids), Attendance.date >= start, Attendance.date <= end
    ):
        records[(record.employee_id, record.date)] = record
    rows = db.query(LeaveRequest.employee_id, LeaveRequest.start_date, LeaveRequest.end_date).filter(
        LeaveRequest.employee_id.in_(ids),
        LeaveRequest.status == LeaveStatus.approved,
        LeaveRequest.start_date <= end,
        LeaveRequest.end_date >= start,
    ).all()
    for employee_id, leave_start, leave_end in rows:
        day, last = max(leave_start, start), min(leave_end, end)
        while day <= last:
            leave_days[employee_id].add(day)
            day += timedelta(days=1)
    return records, leave_days


def iter_day_entries(db: Session, employees: list[Employee], start: date, end: date, cfg):
    """Yields (employee, day entry, is working day) for every day each person was employed.
    The entry comes from attendance_service.build_day, the same function the calendar and the board use."""
    weekend = cfg.weekend_days or []
    holidays = calendar_service.holiday_dates(db, start, end)
    today = clock.today()
    records, leave_days = _load_inputs(db, employees, start, end)
    for emp in employees:
        day = max(start, emp.join_date)
        last = min(end, emp.exit_date) if emp.exit_date else end
        mine = leave_days.get(emp.id, set())
        while day <= last:
            entry = attendance.build_day(day, emp, records.get((emp.id, day)), weekend, holidays, mine, today)
            if entry is not None:
                yield emp, entry, calendar_service.is_working_day(day, weekend, holidays)
            day += timedelta(days=1)


def _rate(counter: Counter) -> float | None:
    attended = sum(counter[s] for s in WORKED)
    denominator = attended + counter["absent"]
    if denominator == 0:
        return None
    numerator = counter["present"] + counter["late"] + counter["missing_checkout"] + 0.5 * counter["half_day"]
    return round(numerator / denominator * 100, 2)


def attendance_summary(db: Session, start: date, end: date, department_id: int | None = None, top: int = 5) -> dict:
    cfg = settings_service.get_settings(db)
    employees = _employees_in_scope(db, start, end, department_id)

    total: Counter = Counter()
    by_day: dict = defaultdict(Counter)
    by_dept: dict = defaultdict(Counter)
    dept_names: dict = {}
    late_by_employee: Counter = Counter()
    people: dict = {}

    for emp, entry, working in iter_day_entries(db, employees, start, end, cfg):
        status = entry["status"].value
        if not working or status not in COUNTED:
            continue  # weekends, holidays, and days that are not over yet
        total[status] += 1
        by_day[entry["date"]][status] += 1
        by_dept[emp.department_id][status] += 1
        dept_names[emp.department_id] = emp.department.name
        if status in WORKED and entry["is_late"]:
            total["late_arrivals"] += 1
            by_dept[emp.department_id]["late_arrivals"] += 1
            late_by_employee[emp.id] += 1
            people[emp.id] = emp

    attended = sum(total[s] for s in WORKED)
    expected = attended + total["absent"] + total["on_leave"]

    trend = [
        {"date": day, **{s: counter[s] for s in COUNTED}, "attendance_rate": _rate(counter)}
        for day, counter in sorted(by_day.items())
    ]
    by_department = []
    for dept_id in sorted(by_dept, key=lambda d: dept_names[d].lower()):
        counter = by_dept[dept_id]
        dept_expected = sum(counter[s] for s in WORKED) + counter["absent"] + counter["on_leave"]
        by_department.append({
            "department_id": dept_id,
            "department": dept_names[dept_id],
            "absent_days": counter["absent"],
            "expected_working_days": dept_expected,
            "absenteeism_percent": _percent(counter["absent"], dept_expected),
            "attendance_rate": _rate(counter),
        })
    ranked = sorted(late_by_employee.items(), key=lambda item: (-item[1], people[item[0]].full_name.lower(), item[0]))
    top_late = [
        {
            "employee_id": employee_id,
            "employee_code": people[employee_id].employee_code,
            "full_name": people[employee_id].full_name,
            "department": people[employee_id].department.name,
            "late_count": count,
        }
        for employee_id, count in ranked[:top]
    ]
    return {
        "counts": {**{s: total[s] for s in COUNTED}, "late_arrivals": total["late_arrivals"]},
        "attendance_rate": _rate(total),
        "late_percent": _percent(total["late_arrivals"], attended),
        "absenteeism_percent": _percent(total["absent"], expected),
        "expected_working_days": expected,
        "trend": trend,
        "by_department": by_department,
        "top_late_comers": top_late,
    }


def today_snapshot(db: Session, department_id: int | None = None) -> dict:
    """Who is here right now. 'present' includes people who are late."""
    cfg = settings_service.get_settings(db)
    today = clock.today()
    employees = _employees_in_scope(db, today, today, department_id)
    counts: Counter = Counter()
    for _emp, entry, _working in iter_day_entries(db, employees, today, today, cfg):
        status = entry["status"].value
        if status in ("present", "late", "half_day"):
            counts["present"] += 1
            if entry["is_late"]:
                counts["late"] += 1
        elif status == "on_leave":
            counts["on_leave"] += 1
        elif status == "not_checked_in":
            counts["not_checked_in"] += 1
    return {
        "date": today,
        "headcount": len(employees),
        "present": counts["present"],
        "late": counts["late"],
        "on_leave": counts["on_leave"],
        "not_checked_in": counts["not_checked_in"],
    }