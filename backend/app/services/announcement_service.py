from datetime import timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.core import clock
from app.models.comms import Announcement
from app.models.user import Role, User
from app.schemas.comms import AnnouncementCreate
from app.services import audit_service as audit


class NotFound(Exception):
    pass


class Invalid(Exception):  # 400
    pass


class Forbidden(Exception):  # 403
    pass


ERRORS = (NotFound, Invalid, Forbidden)


def to_out(item: Announcement, today=None) -> dict:
    today = today or clock.today()
    return {
        "id": item.id,
        "title": item.title,
        "body": item.body,
        "posted_by": {"email": item.created_by.email, "role": item.created_by.role.value},
        "created_at": clock.to_local(item.created_at),
        "expires_on": item.expires_on,
        "is_active": item.expires_on is None or item.expires_on >= today,
    }


def create_announcement(db: Session, user: User, data: AnnouncementCreate) -> Announcement:
    today = clock.today()
    if data.expires_on is not None:
        if data.expires_on < today:
            raise Invalid("The expiry date cannot be in the past")
        if data.expires_on > today + timedelta(days=365):
            raise Invalid("The expiry date cannot be more than a year ahead")

    item = Announcement(title=data.title, body=data.body, expires_on=data.expires_on, created_by_id=user.id)
    db.add(item)
    db.flush()
    audit.record(
        db, "announcement.create", "announcement", item.id,
        f'Posted the announcement "{item.title}"',
        after={"title": item.title, "expires_on": item.expires_on},
    )
    db.commit()
    db.refresh(item)
    return item


def list_announcements(db: Session, user: User, include_expired: bool, limit: int, offset: int) -> dict:
    today = clock.today()
    query = db.query(Announcement)
    # Only HR and Admin may look at expired ones
    if not (include_expired and user.role in (Role.admin, Role.hr)):
        query = query.filter(or_(Announcement.expires_on.is_(None), Announcement.expires_on >= today))
    total = query.count()
    rows = (
        query.options(joinedload(Announcement.created_by))
        .order_by(Announcement.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {"items": [to_out(row, today) for row in rows], "total": total}


def delete_announcement(db: Session, user: User, announcement_id: int) -> None:
    item = db.get(Announcement, announcement_id)
    if item is None:
        raise NotFound()
    if user.role != Role.admin and item.created_by_id != user.id:
        raise Forbidden("You can only delete your own announcements")
    audit.record(
        db, "announcement.delete", "announcement", item.id,
        f'Deleted the announcement "{item.title}"',
        before={"title": item.title, "expires_on": item.expires_on},
    )
    db.delete(item)
    db.commit()