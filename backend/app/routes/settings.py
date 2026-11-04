from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.user import Role, User
from app.schemas.settings import SettingsOut, SettingsUpdate
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["Company settings"])


@router.get("", response_model=SettingsOut)
def read_settings(
    user: User = Depends(require_roles(Role.admin, Role.hr)),
    db: Session = Depends(get_db),
):
    try:
        return settings_service.get_settings(db)
    except settings_service.NotInitialized:
        raise HTTPException(status_code=409, detail="Complete first-run setup first")


@router.put("", response_model=SettingsOut)
def update_settings(
    data: SettingsUpdate,
    user: User = Depends(require_roles(Role.admin)),
    db: Session = Depends(get_db),
):
    try:
        return settings_service.update_settings(db, data)
    except settings_service.NotInitialized:
        raise HTTPException(status_code=409, detail="Complete first-run setup first")