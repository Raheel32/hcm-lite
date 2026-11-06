from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from enum import Enum

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core import clock
from app.models.audit import AuditLog
from app.models.user import User


def clean(value):
    """Turn a value into something that can be stored as JSON."""
    if isinstance(value, Enum):
        return clean(value.value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean(item) for item in value]
    return str(value)


def snapshot(obj, fields) -> dict:
    """The chosen fields of a record, ready to store."""
    return {name: clean(getattr(obj, name)) for name in fields}


def _actor(db: Session, actor) -> dict:
    if isinstance(actor, User):
        return {"id": actor.id, "email": actor.email, "role": actor.role.value}
    return db.info.get("actor") or {}  # set by get_token_user for every logged-in request


def record(db: Session, action: str, entity_type: str, entity_id, summary: str,
           before=None, after=None, actor=None) -> AuditLog:
    """Add an audit entry to the current transaction. The caller commits, so the entry
    is saved together with the action itself, or not at all."""
    who = _actor(db, actor)
    entry = AuditLog(
        actor_id=who.get("id"),
        actor_email=who.get("email"),
        actor_role=who.get("role"),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary[:255],
        before=clean(before),
        after=clean(after),
    )
    db.add(entry)
    return entry


def record_update(db: Session, entity_type: str, entity_id, summary: str, before: dict, after: dict,
                  action: str | None = None, actor=None):
    """Log an update as a diff. Nothing is logged when nothing changed."""
    changed = [key for key in after if before.get(key) != after[key]]
    if not changed:
        return None
    return record(
        db, action or f"{entity_type}.update", entity_type, entity_id, summary,
        before={key: before.get(key) for key in changed},
        after={key: after[key] for key in changed},
        actor=actor,
    )


# ---------- reading (Admin only) ----------

def to_out(entry: AuditLog) -> dict:
    actor = None
    if entry.actor_id is not None or entry.actor_email is not None:
        actor = {"id": entry.actor_id, "email": entry.actor_email, "role": entry.actor_role}
    return {
        "id": entry.id,
        "created_at": clock.to_local(entry.created_at),
        "actor": actor,
        "action": entry.action,
        "entity_type": entry.entity_type,
        "entity_id": entry.entity_id,
        "summary": entry.summary,
        "before": entry.before,
        "after": entry.after,
    }


def list_entries(
    db: Session,
    action: str | None,
    entity_type: str | None,
    entity_id: int | None,
    actor_id: int | None,
    search: str | None,
    date_from: date | None,
    date_to: date | None,
    limit: int,
    offset: int,
) -> dict:
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        query = query.filter(AuditLog.entity_id == entity_id)
    if actor_id is not None:
        query = query.filter(AuditLog.actor_id == actor_id)
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(or_(AuditLog.summary.ilike(pattern), AuditLog.actor_email.ilike(pattern)))

    tz = clock.office_tz()
    if date_from is not None:  # whole office days, converted to UTC for the comparison
        start = datetime.combine(date_from, time.min, tzinfo=tz).astimezone(timezone.utc)
        query = query.filter(AuditLog.created_at >= start)
    if date_to is not None:
        end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
        query = query.filter(AuditLog.created_at < end)

    total = query.count()
    rows = query.order_by(AuditLog.id.desc()).limit(limit).offset(offset).all()
    return {"items": [to_out(row) for row in rows], "total": total}


def list_actions(db: Session) -> list[str]:
    rows = db.query(AuditLog.action).distinct().order_by(AuditLog.action).all()
    return [row[0] for row in rows]