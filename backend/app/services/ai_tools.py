import json
from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import clock
from app.models.employee import EmployeeStatus
from app.models.org import Department
from app.models.user import Role, User
from app.services import audit_service, employee_service, leave_service
from app.services import metrics_service as metrics


class ToolError(Exception):
    """A problem the model can read and fix, for example an unknown department name."""


def short_errors(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:3]:
        where = ".".join(str(p) for p in error["loc"]) or "value"
        parts.append(f"{where}: {error['msg']}")
    return "; ".join(parts)


# ---------- arguments (validated here, never trusted from the model) ----------

class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeadcountArgs(_Args):
    department: str | None = Field(default=None, max_length=100)
    as_of: date | None = None


class RangeArgs(_Args):
    date_from: date | None = None
    date_to: date | None = None
    department: str | None = Field(default=None, max_length=100)


class LateArgs(RangeArgs):
    limit: int = Field(default=5, ge=1, le=10)


class LeaveArgs(RangeArgs):
    on_date: date | None = None


class BalanceArgs(_Args):
    year: int | None = Field(default=None, ge=2000, le=2100)


class SearchArgs(_Args):
    query: str | None = Field(default=None, max_length=100)
    department: str | None = Field(default=None, max_length=100)
    status: Literal["probation", "active", "exited"] | None = None
    limit: int = Field(default=10, ge=1, le=25)


# ---------- helpers ----------

def _department(db: Session, name: str | None) -> tuple[int | None, str | None]:
    if not name:
        return None, None
    row = db.query(Department).filter(func.lower(Department.name) == name.strip().lower()).first()
    if row is None:
        names = [d.name for d in db.query(Department).order_by(func.lower(Department.name)).all()]
        raise ToolError(f"Unknown department '{name}'. Departments: {', '.join(names) or 'none yet'}")
    return row.id, row.name


def _scope(db: Session, args: RangeArgs):
    try:
        start, end = metrics.resolve_range(args.date_from, args.date_to)
    except metrics.Invalid as exc:
        raise ToolError(str(exc))
    return start, end, _department(db, args.department)


# ---------- the tools (all read-only, all built on the same services as the screens) ----------

def _get_headcount(db: Session, user: User, args: HeadcountArgs) -> dict:
    day = args.as_of or clock.today()
    dept_id, dept_name = _department(db, args.department)
    return {
        "as_of": day,
        "department": dept_name,
        "headcount": metrics.headcount(db, day, dept_id),
        "by_department": metrics.headcount_by_department(db, day, dept_id),
    }


def _get_attendance_summary(db: Session, user: User, args: RangeArgs) -> dict:
    start, end, (dept_id, dept_name) = _scope(db, args)
    summary = metrics.attendance_summary(db, start, end, dept_id)
    return {
        "date_from": start,
        "date_to": end,
        "department": dept_name,
        "counts": summary["counts"],
        "attendance_rate": summary["attendance_rate"],
        "late_percent": summary["late_percent"],
        "absenteeism_percent": summary["absenteeism_percent"],
        "expected_working_days": summary["expected_working_days"],
        "by_department": summary["by_department"],
    }


def _get_late_comers(db: Session, user: User, args: LateArgs) -> dict:
    start, end, (dept_id, dept_name) = _scope(db, args)
    summary = metrics.attendance_summary(db, start, end, dept_id, top=args.limit)
    return {
        "date_from": start,
        "date_to": end,
        "department": dept_name,
        "late_comers": [
            {
                "employee_code": row["employee_code"],
                "full_name": row["full_name"],
                "department": row["department"],
                "late_count": row["late_count"],
            }
            for row in summary["top_late_comers"]
        ],
    }


def _get_leave_summary(db: Session, user: User, args: LeaveArgs) -> dict:
    start, end, (dept_id, dept_name) = _scope(db, args)
    pending = metrics.pending_approvals(db, dept_id)
    result = {
        "date_from": start,
        "date_to": end,
        "department": dept_name,
        "approved_leave_by_type": metrics.leave_by_type(db, start, end, dept_id),
        "pending_requests": {"leave_requests": pending["leave"], "attendance_corrections": pending["corrections"]},
    }
    if args.on_date is not None:
        result["on_leave"] = metrics.on_leave_on(db, args.on_date, dept_id)
    return result


def _get_my_leave_balance(db: Session, user: User, args: BalanceArgs) -> dict:
    try:
        return leave_service.my_balance(db, user, args.year)  # always the signed-in person, there is no id to ask for
    except employee_service.EmployeeNotFound:
        raise ToolError("This account has no employee record, so there is no personal leave balance.")


def _get_payroll_summary(db: Session, user: User, args: RangeArgs) -> dict:
    start, end, (dept_id, dept_name) = _scope(db, args)
    payroll = metrics.payroll_summary(db, start, end, dept_id)
    return {
        "date_from": start,
        "date_to": end,
        "department": dept_name,
        "note": "Only finalized payroll runs are counted.",
        "totals": payroll["totals"],
        "by_month": payroll["by_month"],
        "by_department": payroll["by_department"],
    }


def _search_employees(db: Session, user: User, args: SearchArgs) -> dict:
    dept_id, _ = _department(db, args.department)
    status = EmployeeStatus(args.status) if args.status else None
    items, total = employee_service.list_employees(db, args.query, dept_id, None, status, args.limit, 0)
    return {
        "total": total,
        "shown": len(items),
        "employees": [  # directory details only: never salary or contact details
            {
                "employee_code": e.employee_code,
                "full_name": e.full_name,
                "department": e.department.name,
                "designation": e.designation.name,
                "status": e.status.value,
                "join_date": e.join_date,
                "exit_date": e.exit_date,
            }
            for e in items
        ],
    }


# ---------- the catalogue ----------

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict
    roles: tuple
    args_model: type
    handler: Callable


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties}


def _date_property(text: str) -> dict:
    return {"type": "string", "description": f"{text}, formatted YYYY-MM-DD."}


_DEPARTMENT = {
    "type": "string",
    "description": "Exact department name, for example Engineering. Leave out for the whole company.",
}
_RANGE = {
    "date_from": _date_property("First day of the period. Default: the first day of the current month"),
    "date_to": _date_property("Last day of the period. Default: today"),
    "department": _DEPARTMENT,
}
_STAFF = (Role.admin, Role.hr)

TOOLS = {
    spec.name: spec
    for spec in [
        ToolSpec(
            "get_headcount",
            "Number of people employed on a date (active or on probation), for the whole company or one department, with a split by department.",
            _object({"department": _DEPARTMENT, "as_of": _date_property("The date to count on. Default: today")}),
            _STAFF, HeadcountArgs, _get_headcount,
        ),
        ToolSpec(
            "get_attendance_summary",
            "Attendance numbers for a period: day counts (present, late, half_day, missing_checkout, absent, on_leave), attendance rate, late percent, absenteeism percent and a split by department.",
            _object(_RANGE),
            _STAFF, RangeArgs, _get_attendance_summary,
        ),
        ToolSpec(
            "get_late_comers",
            "The employees who arrived late most often in a period, with how many times.",
            _object({**_RANGE, "limit": {"type": "integer", "description": "How many people to list, 1 to 10. Default 5."}}),
            _STAFF, LateArgs, _get_late_comers,
        ),
        ToolSpec(
            "get_leave_summary",
            "Approved leave days by leave type for a period, the number of pending requests, and optionally who is on leave on one date.",
            _object({**_RANGE, "on_date": _date_property("Also list who is on approved leave on this date, for example today")}),
            _STAFF, LeaveArgs, _get_leave_summary,
        ),
        ToolSpec(
            "get_payroll_summary",
            "Payroll cost for a period from finalized payroll runs: total gross, net and tax, with a split by month and department.",
            _object(_RANGE),
            _STAFF, RangeArgs, _get_payroll_summary,
        ),
        ToolSpec(
            "search_employees",
            "Find employees by name or code. Returns directory details only (code, name, department, designation, status, join date).",
            _object({
                "query": {"type": "string", "description": "Part of a name, employee code or email."},
                "department": _DEPARTMENT,
                "status": {"type": "string", "enum": ["probation", "active", "exited"]},
                "limit": {"type": "integer", "description": "How many to list, 1 to 25. Default 10."},
            }),
            _STAFF, SearchArgs, _search_employees,
        ),
        ToolSpec(
            "get_my_leave_balance",
            "The signed-in employee's own leave balance (yearly quota, used, pending and remaining days for each leave type).",
            _object({"year": {"type": "integer", "description": "Calendar year. Default: the current year."}}),
            (Role.employee,), BalanceArgs, _get_my_leave_balance,
        ),
    ]
}


def definitions_for(role: Role) -> list[dict]:
    """Only the tools this role may use are ever shown to the model."""
    return [
        {"type": "function", "function": {"name": s.name, "description": s.description, "parameters": s.schema}}
        for s in TOOLS.values()
        if role in s.roles
    ]


def run_tool(db: Session, user: User, name: str, raw_arguments) -> dict:
    """Run one tool call from the model. Problems come back as {"error": ...} for the model to read, never as crashes."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"error": f"There is no tool called '{name}'."}
    if user.role not in spec.roles:  # the real guard: the prompt is not
        return {"error": "You do not have access to this information."}
    try:
        if isinstance(raw_arguments, str):
            raw = json.loads(raw_arguments) if raw_arguments.strip() else {}
        else:
            raw = raw_arguments or {}
    except json.JSONDecodeError:
        return {"error": "The arguments must be a JSON object."}
    if not isinstance(raw, dict):
        return {"error": "The arguments must be a JSON object."}

    given = {key: value for key, value in raw.items() if value not in ("", None)}  # blank means "not given"
    try:
        args = spec.args_model(**given)
    except ValidationError as exc:
        return {"error": "Invalid arguments: " + short_errors(exc)}
    try:
        return audit_service.clean(spec.handler(db, user, args))
    except ToolError as exc:
        return {"error": str(exc)}