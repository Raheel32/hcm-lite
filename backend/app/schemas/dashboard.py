from datetime import date as date_type

from pydantic import BaseModel

from app.schemas.attendance import DayEntry, EmployeeBrief
from app.schemas.comms import AnnouncementOut, AuditOut
from app.schemas.leave import BalanceOut


class RangeOut(BaseModel):
    date_from: date_type
    date_to: date_type
    department_id: int | None


# ---------- Admin ----------

class AttritionOut(BaseModel):
    exits: int
    average_headcount: float
    percent: float | None


class PayrollTotalsOut(BaseModel):
    gross: float
    net: float
    tax: float
    payslips: int
    finalized_runs: int
    draft_runs_excluded: int


class AverageSalaryOut(BaseModel):
    gross: float | None
    employees: int


class AdminKpis(BaseModel):
    headcount: int
    attrition_ytd: AttritionOut
    payroll_cost: PayrollTotalsOut
    average_salary: AverageSalaryOut


class DepartmentHeadcount(BaseModel):
    department_id: int
    department: str
    headcount: int


class PayrollDepartment(BaseModel):
    department_id: int
    department: str
    gross: float
    net: float


class PayrollMonth(BaseModel):
    period: str
    gross: float
    net: float
    employees: int


class SalaryByDepartment(BaseModel):
    department_id: int
    department: str
    average_gross: float | None
    employees: int


class SalaryByDesignation(BaseModel):
    designation_id: int
    designation: str
    average_gross: float | None
    employees: int


class GenderCount(BaseModel):
    gender: str
    count: int


class TenureCount(BaseModel):
    bucket: str
    count: int


class AdminDashboardOut(BaseModel):
    range: RangeOut
    kpis: AdminKpis
    headcount_by_department: list[DepartmentHeadcount]
    payroll_by_department: list[PayrollDepartment]
    payroll_by_month: list[PayrollMonth]
    average_salary_by_department: list[SalaryByDepartment]
    average_salary_by_designation: list[SalaryByDesignation]
    gender: list[GenderCount]
    tenure: list[TenureCount]
    audit_feed: list[AuditOut]
    announcements: list[AnnouncementOut]


# ---------- HR ----------

class PendingOut(BaseModel):
    leave: int
    corrections: int
    total: int


class HrKpis(BaseModel):
    date: date_type
    headcount: int
    present: int
    late_today: int
    on_leave: int
    not_checked_in: int
    pending_approvals: PendingOut


class AttendanceCounts(BaseModel):
    present: int
    late: int
    half_day: int
    missing_checkout: int
    absent: int
    on_leave: int
    late_arrivals: int


class PeriodAttendance(BaseModel):
    counts: AttendanceCounts
    attendance_rate: float | None
    late_percent: float | None
    absenteeism_percent: float | None
    expected_working_days: int


class TrendPoint(BaseModel):
    date: date_type
    present: int
    late: int
    half_day: int
    missing_checkout: int
    absent: int
    on_leave: int
    attendance_rate: float | None


class AbsenteeismRow(BaseModel):
    department_id: int
    department: str
    absent_days: int
    expected_working_days: int
    absenteeism_percent: float | None
    attendance_rate: float | None


class LateComer(BaseModel):
    employee_id: int
    employee_code: str
    full_name: str
    department: str
    late_count: int


class LeaveTypeRow(BaseModel):
    leave_type_id: int
    leave_type: str
    days: int
    requests: int


class UtilizationRow(BaseModel):
    leave_type_id: int
    leave_type: str
    department_id: int
    department: str
    yearly_quota: int | None
    headcount: int
    approved_days: int
    quota_total: int | None
    utilization_percent: float | None


class HrDashboardOut(BaseModel):
    range: RangeOut
    kpis: HrKpis
    period: PeriodAttendance
    attendance_trend: list[TrendPoint]
    absenteeism_by_department: list[AbsenteeismRow]
    top_late_comers: list[LateComer]
    leave_by_type: list[LeaveTypeRow]
    leave_year: int
    leave_utilization: list[UtilizationRow]
    announcements: list[AnnouncementOut]


# ---------- Employee ----------

class WeekHours(BaseModel):
    week_start: date_type
    week_end: date_type
    worked_hours: float
    target_hours: float
    percent: float


class LastPayslip(BaseModel):
    id: int
    period: str
    net_pay: float
    gross_pay: float


class EmployeeDashboardOut(BaseModel):
    employee: EmployeeBrief
    today: DayEntry | None
    week: WeekHours
    month: str
    late_count_month: int
    leave_balance: BalanceOut
    last_payslip: LastPayslip | None
    announcements: list[AnnouncementOut]
    unread_notifications: int