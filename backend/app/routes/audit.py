from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.user import Role, User
from app.schemas.comms import AuditListOut
from app.services import audit_service

router = APIRouter(prefix="/audit", tags=["Audit log (Admin)"])

admin_only = require_roles(Role.admin)


@router.get("", response_model=AuditListOut)
def list_audit(
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    actor_id: int | None = None,
    search: str | None = None,
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from cannot be after date_to")
    return audit_service.list_entries(
        db, action, entity_type, entity_id, actor_id, search, date_from, date_to, limit, offset
    )


@router.get("/actions", response_model=list[str])
def list_actions(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return audit_service.list_actions(db)