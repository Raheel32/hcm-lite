import enum
from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class LeaveStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False, index=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    days = Column(Integer, nullable=False)  # working days, counted when the request was made
    reason = Column(String(500), nullable=False)
    status = Column(
        Enum(LeaveStatus, native_enum=False, length=20),
        nullable=False,
        default=LeaveStatus.pending,
        index=True,
    )
    reviewed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    review_comment = Column(String(500), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    employee = relationship("Employee")
    leave_type = relationship("LeaveType")
    reviewer = relationship("User")