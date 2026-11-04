import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint

from app.database import Base


class DayStatus(str, enum.Enum):
    # Stored on a record:
    present = "present"
    late = "late"
    half_day = "half_day"
    # Worked out when we show a day:
    missing_checkout = "missing_checkout"
    absent = "absent"
    on_leave = "on_leave"
    weekend = "weekend"
    holiday = "holiday"
    not_checked_in = "not_checked_in"
    upcoming = "upcoming"


class Attendance(Base):
    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("employee_id", "date", name="uq_attendance_employee_date"),)

    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)  # the office-local calendar day
    check_in = Column(DateTime(timezone=True), nullable=False)
    check_out = Column(DateTime(timezone=True), nullable=True)
    status = Column(Enum(DayStatus, native_enum=False, length=20), nullable=False)
    is_late = Column(Boolean, nullable=False, default=False)
    source = Column(String(20), nullable=False, default="web")  # web, camera, correction
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))