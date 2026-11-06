from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, event

from app.database import Base


class AuditLogIsReadOnly(Exception):
    pass


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, index=True, default=lambda: datetime.now(timezone.utc)
    )
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    actor_email = Column(String(255), nullable=True)  # copied in, so the entry still reads well later
    actor_role = Column(String(20), nullable=True)
    action = Column(String(60), nullable=False, index=True)
    entity_type = Column(String(40), nullable=False, index=True)
    entity_id = Column(Integer, nullable=True, index=True)
    summary = Column(String(255), nullable=False)
    before = Column(JSON, nullable=True)
    after = Column(JSON, nullable=True)


@event.listens_for(AuditLog, "before_update")
def _block_update(mapper, connection, target):
    raise AuditLogIsReadOnly("Audit log entries cannot be changed")


@event.listens_for(AuditLog, "before_delete")
def _block_delete(mapper, connection, target):
    raise AuditLogIsReadOnly("Audit log entries cannot be deleted")