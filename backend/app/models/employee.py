import enum
from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base


class EmployeeStatus(str, enum.Enum):
    probation = "probation"
    active = "active"
    exited = "exited"


class Gender(str, enum.Enum):
    male = "male"
    female = "female"
    other = "other"


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    employee_code = Column(String(30), unique=True, index=True, nullable=False)
    full_name = Column(String(150), nullable=False)
    phone = Column(String(30), nullable=True)
    address = Column(String(300), nullable=True)
    gender = Column(Enum(Gender, native_enum=False), nullable=True)

    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False, index=True)
    designation_id = Column(Integer, ForeignKey("designations.id"), nullable=False, index=True)

    join_date = Column(Date, nullable=False)
    exit_date = Column(Date, nullable=True)
    status = Column(Enum(EmployeeStatus, native_enum=False), nullable=False, default=EmployeeStatus.active)
    basic_salary = Column(Numeric(12, 2, asdecimal=False), nullable=False)

    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User")
    department = relationship("Department")
    designation = relationship("Designation")

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def is_active(self) -> bool:
        return self.user.is_active