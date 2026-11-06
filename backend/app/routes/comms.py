from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.user import Role, User
from app.schemas.comms import (
    AnnouncementCreate,
    AnnouncementListOut,
    AnnouncementOut,
    MarkedOut,
    NotificationListOut,
    NotificationOut,
    UnreadOut,
)
from app.services import announcement_service, notification_service

notifications_router = APIRouter(prefix="/notifications", tags=["Notifications"])
announcements_router = APIRouter(prefix="/announcements", tags=["Announcements"])

anyone = require_roles(Role.admin, Role.hr, Role.employee)
staff = require_roles(Role.admin, Role.hr)


# ---------- Notifications (everyone, only their own) ----------

@notifications_router.get("", response_model=NotificationListOut)
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(anyone),
    db: Session = Depends(get_db),
):
    return notification_service.list_for(db, user, unread_only, limit, offset)


@notifications_router.get("/unread-count", response_model=UnreadOut)
def unread_count(user: User = Depends(anyone), db: Session = Depends(get_db)):
    return {"unread": notification_service.unread_count(db, user)}


@notifications_router.post("/read-all", response_model=MarkedOut)
def read_all(user: User = Depends(anyone), db: Session = Depends(get_db)):
    return {"marked": notification_service.mark_all_read(db, user)}


@notifications_router.post("/{notification_id}/read", response_model=NotificationOut)
def read_one(notification_id: int, user: User = Depends(anyone), db: Session = Depends(get_db)):
    try:
        return notification_service.to_out(notification_service.mark_read(db, user, notification_id))
    except notification_service.NotFound:
        raise HTTPException(status_code=404, detail="Notification not found")


# ---------- Announcements ----------

def _announcement_http(exc: Exception) -> HTTPException:
    if isinstance(exc, announcement_service.NotFound):
        return HTTPException(status_code=404, detail="Announcement not found")
    if isinstance(exc, announcement_service.Invalid):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, announcement_service.Forbidden):
        return HTTPException(status_code=403, detail=str(exc))
    raise exc


@announcements_router.get("", response_model=AnnouncementListOut)
def list_announcements(
    include_expired: bool = False,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(anyone),
    db: Session = Depends(get_db),
):
    return announcement_service.list_announcements(db, user, include_expired, limit, offset)


@announcements_router.post("", response_model=AnnouncementOut, status_code=201)
def post_announcement(
    data: AnnouncementCreate,
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return announcement_service.to_out(announcement_service.create_announcement(db, user, data))
    except announcement_service.ERRORS as exc:
        raise _announcement_http(exc)


@announcements_router.delete("/{announcement_id}", status_code=204)
def delete_announcement(
    announcement_id: int,
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        announcement_service.delete_announcement(db, user, announcement_id)
    except announcement_service.ERRORS as exc:
        raise _announcement_http(exc)