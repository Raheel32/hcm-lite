from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.correction import CorrectionStatus
from app.models.user import Role, User
from app.schemas.correction import (
    CorrectionCreate,
    CorrectionListOut,
    CorrectionOut,
    DecisionApprove,
    DecisionReject,
)
from app.services import correction_service as service
from app.services import employee_service, settings_service

router = APIRouter(prefix="/attendance/corrections", tags=["Attendance corrections"])

employee_only = require_roles(Role.employee)
hr_only = require_roles(Role.hr)
staff = require_roles(Role.admin, Role.hr)

CAUGHT = service.ERRORS + (employee_service.EmployeeNotFound, settings_service.NotInitialized)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, employee_service.EmployeeNotFound):
        return HTTPException(status_code=404, detail="Employee record not found")
    if isinstance(exc, settings_service.NotInitialized):
        return HTTPException(status_code=409, detail="Complete first-run setup first")
    if isinstance(exc, service.CorrectionNotFound):
        return HTTPException(status_code=404, detail="Correction request not found")
    if isinstance(exc, service.DuplicatePending):
        return HTTPException(status_code=409, detail="There is already a pending correction for this date")
    if isinstance(exc, service.NotPending):
        return HTTPException(status_code=409, detail="Only pending requests can be changed")
    if isinstance(exc, (service.BadDate, service.BadTimes)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, service.CannotApply):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


# ---------- Employee ----------

@router.post("", response_model=CorrectionOut, status_code=201)
def request_correction(
    data: CorrectionCreate,
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
):
    try:
        correction = service.create_correction(db, user, data)
        return service.to_out(db, correction)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/me", response_model=CorrectionListOut)
def my_corrections(
    status_filter: CorrectionStatus | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
):
    try:
        return service.list_mine(db, user, status_filter, limit, offset)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/{correction_id}/cancel", response_model=CorrectionOut)
def cancel_correction(
    correction_id: int,
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
):
    try:
        correction = service.cancel_correction(db, user, correction_id)
        return service.to_out(db, correction)
    except CAUGHT as exc:
        raise _http(exc)


# ---------- HR and Admin ----------

@router.get("", response_model=CorrectionListOut)
def list_corrections(
    status_filter: CorrectionStatus | None = Query(None, alias="status"),
    employee_id: int | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return service.list_all(db, status_filter, employee_id, limit, offset)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/{correction_id}/approve", response_model=CorrectionOut)
def approve_correction(
    correction_id: int,
    data: DecisionApprove | None = None,
    user: User = Depends(hr_only),
    db: Session = Depends(get_db),
):
    try:
        comment = data.comment if data else None
        correction = service.approve_correction(db, user, correction_id, comment)
        return service.to_out(db, correction)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/{correction_id}/reject", response_model=CorrectionOut)
def reject_correction(
    correction_id: int,
    data: DecisionReject,
    user: User = Depends(hr_only),
    db: Session = Depends(get_db),
):
    try:
        correction = service.reject_correction(db, user, correction_id, data.comment)
        return service.to_out(db, correction)
    except CAUGHT as exc:
        raise _http(exc)