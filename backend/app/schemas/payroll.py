from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.payroll import RunStatus
from app.schemas.attendance import EmployeeBrief


class RunCreate(BaseModel):
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)


class RunTotals(BaseModel):
    employees: int
    gross_salary: float
    prorated_gross: float
    attendance_deduction: float
    tax: float
    net_pay: float


class RunOut(BaseModel):
    id: int
    year: int
    month: int
    period: str
    status: RunStatus
    working_days: int
    as_of_date: date_type
    month_in_progress: bool
    created_by: str | None
    created_at: datetime | None
    calculated_at: datetime | None
    finalized_by: str | None
    finalized_at: datetime | None
    totals: RunTotals


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


class PayslipOut(BaseModel):
    id: int
    run_id: int
    year: int
    month: int
    period: str
    run_status: RunStatus
    employee: EmployeeBrief
    department: str
    designation: str
    basic: float
    hra: float
    medical: float
    gross_salary: float
    working_days: int
    payable_days: int
    absent_days: int
    unpaid_days: int
    half_days: int
    deduction_days: float
    per_day_rate: float
    prorated_gross: float
    attendance_deduction: float
    tax: float
    net_pay: float


class PayslipListOut(BaseModel):
    items: list[PayslipOut]
    total: int


class SalaryUpdate(BaseModel):
    basic_salary: float = Field(gt=0, le=100_000_000)


class SalaryStructureOut(BaseModel):
    employee: EmployeeBrief
    basic_salary: float
    hra: float
    medical: float
    gross_salary: float
    annual_gross: float
    estimated_monthly_tax: float