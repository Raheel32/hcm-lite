from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.user import Role, User
from app.schemas.leave_config import (
    HolidayCreate,
    HolidayOut,
    LeaveTypeCreate,
    LeaveTypeOut,
    LeaveTypeUpdate,
)
from app.services import leave_config_service as service

leave_types_router = APIRouter(prefix="/leave-types", tags=["Leave types"])
holidays_router = APIRouter(prefix="/holidays", tags=["Holidays"])


# ---------- Leave types ----------

@leave_types_router.get("", response_model=list[LeaveTypeOut])
def list_leave_types(
    include_inactive: bool = False,
    user: User = Depends(require_roles(Role.admin, Role.hr)),
    db: Session = Depends(get_db),
):
    return service.list_leave_types(db, include_inactive)


@leave_types_router.post("", response_model=LeaveTypeOut, status_code=201)
def create_leave_type(
    data: LeaveTypeCreate,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    try:
        return service.create_leave_type(db, data)
    except service.DuplicateName:
        raise HTTPException(status_code=409, detail="A leave type with this name already exists")


@leave_types_router.put("/{leave_type_id}", response_model=LeaveTypeOut)
def update_leave_type(
    leave_type_id: int,
    data: LeaveTypeUpdate,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    try:
        return service.update_leave_type(db, leave_type_id, data)
    except service.ItemNotFound:
        raise HTTPException(status_code=404, detail="Leave type not found")
    except service.DuplicateName:
        raise HTTPException(status_code=409, detail="A leave type with this name already exists")


# ---------- Holidays ----------

@holidays_router.get("", response_model=list[HolidayOut])
def list_holidays(
    year: int | None = None,
    user: User = Depends(require_roles(Role.admin, Role.hr)),
    db: Session = Depends(get_db),
):
    return service.list_holidays(db, year)


@holidays_router.post("", response_model=HolidayOut, status_code=201)
def create_holiday(
    data: HolidayCreate,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    try:
        return service.create_holiday(db, data)
    except service.DuplicateDate:
        raise HTTPException(status_code=409, detail="A holiday already exists on this date")


@holidays_router.put("/{holiday_id}", response_model=HolidayOut)
def update_holiday(
    holiday_id: int,
    data: HolidayCreate,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    try:
        return service.update_holiday(db, holiday_id, data)
    except service.ItemNotFound:
        raise HTTPException(status_code=404, detail="Holiday not found")
    except service.DuplicateDate:
        raise HTTPException(status_code=409, detail="A holiday already exists on this date")


@holidays_router.delete("/{holiday_id}", status_code=204)
def delete_holiday(
    holiday_id: int,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    try:
        service.delete_holiday(db, holiday_id)
    except service.ItemNotFound:
        raise HTTPException(status_code=404, detail="Holiday not found")