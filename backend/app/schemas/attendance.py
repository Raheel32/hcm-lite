from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel

from app.models.attendance import DayStatus
from app.schemas.employee import NamedRef


class DayEntry(BaseModel):
    date: date_type
    status: DayStatus
    check_in: datetime | None = None
    check_out: datetime | None = None
    worked_hours: float | None = None
    is_late: bool = False
    source: str | None = None


class MonthSummary(BaseModel):
    counts: dict[str, int]
    late_count: int
    total_hours: float


class CalendarOut(BaseModel):
    month: str
    days: list[DayEntry]
    summary: MonthSummary


class EmployeeBrief(BaseModel):
    id: int
    employee_code: str
    full_name: str


class EmployeeCalendarOut(CalendarOut):
    employee: EmployeeBrief


class BoardItem(DayEntry):
    employee_id: int
    employee_code: str
    full_name: str
    department: NamedRef


class BoardOut(BaseModel):
    date: date_type
    summary: dict[str, int]
    items: list[BoardItem]
    total: int