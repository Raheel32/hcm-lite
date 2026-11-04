from sqlalchemy import Column, Integer, String, Boolean, Date

from app.database import Base


class LeaveType(Base):
    __tablename__ = "leave_types"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False)
    # Days per year. None means unlimited (for example, unpaid leave).
    yearly_quota = Column(Integer, nullable=True)
    is_paid = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)


class Holiday(Base):
    __tablename__ = "holidays"

    id = Column(Integer, primary_key=True)
    date = Column(Date, unique=True, nullable=False)
    name = Column(String(100), nullable=False)