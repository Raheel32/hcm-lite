from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.attendance import DayStatus
from app.models.user import Role, User
from app.schemas.attendance import BoardOut, CalendarOut, DayEntry, EmployeeCalendarOut
from app.services import attendance_service as service
from app.services import employee_service, settings_service

router = APIRouter(prefix="/attendance", tags=["Attendance"])

employee_only = require_roles(Role.employee)
staff = require_roles(Role.admin, Role.hr)

CAUGHT = service.ERRORS + (employee_service.EmployeeNotFound, settings_service.NotInitialized)
MONTH = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM (default: this month)")


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, employee_service.EmployeeNotFound):
        return HTTPException(status_code=404, detail="Employee record not found")
    if isinstance(exc, settings_service.NotInitialized):
        return HTTPException(status_code=409, detail="Complete first-run setup first")
    if isinstance(exc, service.AlreadyCheckedIn):
        return HTTPException(status_code=409, detail="You have already checked in today")
    if isinstance(exc, service.AlreadyCheckedOut):
        return HTTPException(status_code=409, detail="You have already checked out today")
    if isinstance(exc, service.NoCheckIn):
        return HTTPException(status_code=409, detail="You have not checked in today")
    if isinstance(exc, service.NotStarted):
        return HTTPException(status_code=409, detail="Your joining date has not arrived yet")
    if isinstance(exc, service.FutureDate):
        return HTTPException(status_code=400, detail="The date cannot be in the future")
    raise exc


# ---------- Employee ----------

@router.get("/today", response_model=DayEntry)
def today(user: User = Depends(employee_only), db: Session = Depends(get_db)):
    try:
        return service.today_entry(db, user)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/check-in", response_model=DayEntry, status_code=201)
def check_in(user: User = Depends(employee_only), db: Session = Depends(get_db)):
    try:
        return service.check_in(db, user)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/check-out", response_model=DayEntry)
def check_out(user: User = Depends(employee_only), db: Session = Depends(get_db)):
    try:
        return service.check_out(db, user)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/me", response_model=CalendarOut)
def my_calendar(
    month: str | None = MONTH,
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
):
    try:
        return service.my_calendar(db, user, month)
    except CAUGHT as exc:
        raise _http(exc)


# ---------- HR and Admin ----------

@router.get("/board", response_model=BoardOut)
def board(
    day: date_type | None = Query(None, alias="date"),
    department_id: int | None = None,
    search: str | None = None,
    status_filter: DayStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return service.daily_board(db, day, department_id, search, status_filter, limit, offset)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/employees/{employee_id}", response_model=EmployeeCalendarOut)
def employee_calendar(
    employee_id: int,
    month: str | None = MONTH,
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return service.employee_calendar(db, employee_id, month)
    except CAUGHT as exc:
        raise _http(exc)