from datetime import datetime, time, timezone

from sqlalchemy import Column, Integer, Float, Time, JSON, DateTime, ForeignKey

from app.database import Base


def _now():
    return datetime.now(timezone.utc)


class CompanySettings(Base):
    __tablename__ = "company_settings"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), unique=True, nullable=False)

    office_start = Column(Time, nullable=False, default=time(9, 0))
    grace_minutes = Column(Integer, nullable=False, default=15)
    half_day_hours = Column(Float, nullable=False, default=5.0)
    # 0 = Monday ... 5 = Saturday, 6 = Sunday
    weekend_days = Column(JSON, nullable=False, default=lambda: [5, 6])

    office_lat = Column(Float, nullable=True)
    office_lng = Column(Float, nullable=True)
    radius_m = Column(Integer, nullable=False, default=150)

    updated_at = Column(DateTime(timezone=True), default=_now, onupdate=_now)