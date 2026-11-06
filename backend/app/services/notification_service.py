from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core import clock
from app.models.comms import Notification
from app.models.user import Role, User


class NotFound(Exception):
    pass


def notify(db: Session, user_id: int, kind: str, title: str, body: str,
           entity_type: str | None = None, entity_id: int | None = None) -> None:
    """Add a notification to the current transaction (the caller commits)."""
    db.add(Notification(
        user_id=user_id, kind=kind, title=title[:150], body=body[:500],
        entity_type=entity_type, entity_id=entity_id,
    ))


def notify_hr(db: Session, kind: str, title: str, body: str,
              entity_type: str | None = None, entity_id: int | None = None) -> None:
    """One notification for every active HR login."""
    hr_ids = [row[0] for row in db.query(User.id).filter(User.role == Role.hr, User.is_active.is_(True)).all()]
    for user_id in hr_ids:
        notify(db, user_id, kind, title, body, entity_type, entity_id)


def to_out(item: Notification) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "title": item.title,
        "body": item.body,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "created_at": clock.to_local(item.created_at),
        "read_at": clock.to_local(item.read_at) if item.read_at else None,
        "is_read": item.read_at is not None,
    }


def list_for(db: Session, user: User, unread_only: bool, limit: int, offset: int) -> dict:
    mine = db.query(Notification).filter(Notification.user_id == user.id)
    unread = mine.filter(Notification.read_at.is_(None)).count()
    query = mine.filter(Notification.read_at.is_(None)) if unread_only else mine
    total = query.count()
    rows = query.order_by(Notification.id.desc()).limit(limit).offset(offset).all()
    return {"items": [to_out(row) for row in rows], "total": total, "unread": unread}


def unread_count(db: Session, user: User) -> int:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.read_at.is_(None))
        .count()
    )


def mark_read(db: Session, user: User, notification_id: int) -> Notification:
    item = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == user.id)
        .first()
    )
    if item is None:
        raise NotFound()  # someone else's notification looks exactly like a missing one
    if item.read_at is None:
        item.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(item)
    return item


def mark_all_read(db: Session, user: User) -> int:
    marked = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.read_at.is_(None))
        .update({Notification.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.commit()
    return marked