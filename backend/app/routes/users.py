from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.user import Role, User
from app.schemas.users import (
    CreatedUserOut,
    HRCreate,
    ResetPasswordOut,
    UserListItem,
    UserListOut,
)
from app.services import user_service

router = APIRouter(prefix="/users", tags=["User logins (Admin)"])

admin_only = require_roles(Role.admin)


@router.get("", response_model=UserListOut)
def list_users(
    role: Role | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    items, total = user_service.list_users(db, role, is_active, search, limit, offset)
    return {"items": items, "total": total}


@router.post("", response_model=CreatedUserOut, status_code=201)
def create_hr_login(
    data: HRCreate,
    user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    try:
        new_user, temp_password = user_service.create_hr_user(db, data.email)
    except user_service.EmailTaken:
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    return {"user": new_user, "temporary_password": temp_password}


@router.post("/{user_id}/deactivate", response_model=UserListItem)
def deactivate(user_id: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return _set_active(db, user_id, False)


@router.post("/{user_id}/activate", response_model=UserListItem)
def activate(user_id: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return _set_active(db, user_id, True)


@router.post("/{user_id}/reset-password", response_model=ResetPasswordOut)
def reset_password(user_id: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    try:
        target, temp_password = user_service.reset_password(db, user_id)
    except user_service.UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except user_service.ProtectedAccount:
        raise HTTPException(status_code=400, detail="Admin accounts cannot be changed here")
    return {"email": target.email, "temporary_password": temp_password}


def _set_active(db: Session, user_id: int, active: bool):
    try:
        return user_service.set_active(db, user_id, active)
    except user_service.UserNotFound:
        raise HTTPException(status_code=404, detail="User not found")
    except user_service.ProtectedAccount:
        raise HTTPException(status_code=400, detail="Admin accounts cannot be changed here")