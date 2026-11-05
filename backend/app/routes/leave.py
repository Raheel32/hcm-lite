from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.leave import LeaveStatus
from app.models.user import Role, User
from app.schemas.correction import DecisionApprove, DecisionReject
from app.schemas.leave import BalanceOut, LeaveApply, LeaveListOut, LeaveOut, PreviewOut
from app.services import employee_service, settings_service
from app.services import leave_service as service

router = APIRouter(prefix="/leave", tags=["Leave"])

employee_only = require_roles(Role.employee)
hr_only = require_roles(Role.hr)
staff = require_roles(Role.admin, Role.hr)

CAUGHT = service.ERRORS + (employee_service.EmployeeNotFound, settings_service.NotInitialized)
YEAR = Query(None, ge=2000, le=2100)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, employee_service.EmployeeNotFound):
        return HTTPException(status_code=404, detail="Employee record not found")
    if isinstance(exc, settings_service.NotInitialized):
        return HTTPException(status_code=409, detail="Complete first-run setup first")
    if isinstance(exc, service.NotFound):
        return HTTPException(status_code=404, detail="Leave request not found")
    if isinstance(exc, service.Invalid):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, service.Unprocessable):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, service.Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


# ---------- Employee ----------

@router.get("/balance", response_model=BalanceOut)
def my_balance(year: int | None = YEAR, user: User = Depends(employee_only), db: Session = Depends(get_db)):
    try:
        return service.my_balance(db, user, year)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/preview", response_model=PreviewOut)
def preview(
    leave_type_id: int,
    start_date: date_type,
    end_date: date_type,
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
):
    try:
        return service.preview(db, user, leave_type_id, start_date, end_date)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/me", response_model=LeaveListOut)
def my_leave(
    status_filter: LeaveStatus | None = Query(None, alias="status"),
    year: int | None = YEAR,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
):
    try:
        return service.list_mine(db, user, status_filter, year, limit, offset)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("", response_model=LeaveOut, status_code=201)
def apply_for_leave(data: LeaveApply, user: User = Depends(employee_only), db: Session = Depends(get_db)):
    try:
        return service.to_out(service.apply_leave(db, user, data))
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/{leave_id}/cancel", response_model=LeaveOut)
def cancel_leave(leave_id: int, user: User = Depends(employee_only), db: Session = Depends(get_db)):
    try:
        return service.to_out(service.cancel_leave(db, user, leave_id))
    except CAUGHT as exc:
        raise _http(exc)


# ---------- HR and Admin ----------

@router.get("", response_model=LeaveListOut)
def list_leave(
    status_filter: LeaveStatus | None = Query(None, alias="status"),
    employee_id: int | None = None,
    leave_type_id: int | None = None,
    department_id: int | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return service.list_all(db, status_filter, employee_id, leave_type_id, department_id, limit, offset)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/employees/{employee_id}/balance", response_model=BalanceOut)
def employee_balance(
    employee_id: int,
    year: int | None = YEAR,
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return service.employee_balance(db, employee_id, year)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/{leave_id}/approve", response_model=LeaveOut)
def approve_leave(
    leave_id: int,
    data: DecisionApprove | None = None,
    user: User = Depends(hr_only),
    db: Session = Depends(get_db),
):
    try:
        comment = data.comment if data else None
        return service.to_out(service.approve_leave(db, user, leave_id, comment))
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/{leave_id}/reject", response_model=LeaveOut)
def reject_leave(
    leave_id: int,
    data: DecisionReject,
    user: User = Depends(hr_only),
    db: Session = Depends(get_db),
):
    try:
        return service.to_out(service.reject_leave(db, user, leave_id, data.comment))
    except CAUGHT as exc:
        raise _http(exc)