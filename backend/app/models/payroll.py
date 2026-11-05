import enum
from datetime import datetime, timezone

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class RunStatus(str, enum.Enum):
    draft = "draft"
    finalized = "finalized"


def _money():
    return Numeric(12, 2, asdecimal=False)


class PayrollRun(Base):
    __tablename__ = "payroll_runs"
    __table_args__ = (UniqueConstraint("year", "month", name="uq_payroll_run_month"),)

    id = Column(Integer, primary_key=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    status = Column(
        Enum(RunStatus, native_enum=False, length=20),
        nullable=False,
        default=RunStatus.draft,
        index=True,
    )
    working_days = Column(Integer, nullable=False)
    as_of_date = Column(Date, nullable=False)  # the day the figures were calculated
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    calculated_at = Column(DateTime(timezone=True), nullable=False)
    finalized_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    finalized_at = Column(DateTime(timezone=True), nullable=True)

    created_by = relationship("User", foreign_keys=[created_by_id])
    finalized_by = relationship("User", foreign_keys=[finalized_by_id])
    payslips = relationship("Payslip", back_populates="run", cascade="all, delete-orphan")


class Payslip(Base):
    """One row per employee per run. Names and amounts are copied in, so later edits never change it."""

    __tablename__ = "payslips"
    __table_args__ = (UniqueConstraint("run_id", "employee_id", name="uq_payslip_run_employee"),)

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("payroll_runs.id"), nullable=False, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)

    employee_code = Column(String(30), nullable=False)
    full_name = Column(String(150), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False)
    department_name = Column(String(100), nullable=False)
    designation_name = Column(String(100), nullable=False)

    basic = Column(_money(), nullable=False)
    hra = Column(_money(), nullable=False)
    medical = Column(_money(), nullable=False)
    gross_salary = Column(_money(), nullable=False)  # the full monthly gross

    working_days = Column(Integer, nullable=False)  # working days in the month
    payable_days = Column(Integer, nullable=False)  # working days inside the employment
    absent_days = Column(Integer, nullable=False)
    unpaid_days = Column(Integer, nullable=False)
    half_days = Column(Integer, nullable=False)
    deduction_days = Column(Numeric(6, 1, asdecimal=False), nullable=False)

    per_day_rate = Column(_money(), nullable=False)
    prorated_gross = Column(_money(), nullable=False)  # gross for the days employed
    attendance_deduction = Column(_money(), nullable=False)
    tax = Column(_money(), nullable=False)
    net_pay = Column(_money(), nullable=False)

    run = relationship("PayrollRun", back_populates="payslips")
    employee = relationship("Employee")