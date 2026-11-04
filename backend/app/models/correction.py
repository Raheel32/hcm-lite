import enum
from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class CorrectionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class AttendanceCorrection(Base):
    __tablename__ = "attendance_corrections"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    # Only the part the employee wants fixed is filled in
    requested_check_in = Column(DateTime(timezone=True), nullable=True)
    requested_check_out = Column(DateTime(timezone=True), nullable=True)
    reason = Column(String(500), nullable=False)
    status = Column(
        Enum(CorrectionStatus, native_enum=False, length=20),
        nullable=False,
        default=CorrectionStatus.pending,
        index=True,
    )
    reviewed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_comment = Column(String(500), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    employee = relationship("Employee")
    reviewer = relationship("User")