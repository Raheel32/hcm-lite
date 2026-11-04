import re
from datetime import date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.employee import EmployeeStatus, Gender

PHONE_RE = re.compile(r"^[0-9+\-() ]{7,20}$")
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9\-_]{1,29}$")


def _clean_phone(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if v == "":
        return None
    if not PHONE_RE.match(v):
        raise ValueError("Phone must be 7-20 characters: digits, spaces, + - ( )")
    return v


def _clean_address(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    return v or None


class EmployeeFields(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    phone: str | None = None
    address: str | None = Field(default=None, max_length=300)
    gender: Gender | None = None
    department_id: int
    designation_id: int
    join_date: date
    basic_salary: float = Field(gt=0, le=100_000_000)
    status: EmployeeStatus = EmployeeStatus.active

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, v):
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v):
        return _clean_phone(v)

    @field_validator("address")
    @classmethod
    def check_address(cls, v):
        return _clean_address(v)

    @field_validator("join_date")
    @classmethod
    def check_join_date(cls, v):
        if v < date(2000, 1, 1) or v > date.today() + timedelta(days=365):
            raise ValueError("Join date is out of range")
        return v

    @field_validator("status")
    @classmethod
    def not_exited(cls, v):
        if v == EmployeeStatus.exited:
            raise ValueError("Use the exit action to mark an employee as exited")
        return v


class EmployeeCreate(EmployeeFields):
    email: EmailStr
    employee_code: str | None = None  # optional, generated as EMP-0001 if empty

    @field_validator("employee_code")
    @classmethod
    def check_code(cls, v):
        if v is None:
            return None
        v = v.strip().upper()
        if v == "":
            return None
        if not CODE_RE.match(v):
            raise ValueError("Code may use letters, digits, - and _ (2-30 characters)")
        return v


class EmployeeUpdate(EmployeeFields):
    """Email and employee code cannot be changed, so they are not part of this schema."""


class SelfUpdate(BaseModel):
    """The only fields an employee may edit about themselves."""

    phone: str | None = None
    address: str | None = Field(default=None, max_length=300)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v):
        return _clean_phone(v)

    @field_validator("address")
    @classmethod
    def check_address(cls, v):
        return _clean_address(v)


class ExitRequest(BaseModel):
    exit_date: date


class NamedRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class EmployeeListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_code: str
    full_name: str
    email: EmailStr
    department: NamedRef
    designation: NamedRef
    status: EmployeeStatus
    join_date: date
    exit_date: date | None
    is_active: bool


class EmployeeProfileOut(EmployeeListItem):
    """What an employee sees about themselves: no salary."""

    phone: str | None
    address: str | None
    gender: Gender | None
    created_at: datetime | None = None


class EmployeeFullOut(EmployeeProfileOut):
    """What Admin and HR see: includes salary."""

    basic_salary: float


class EmployeeListOut(BaseModel):
    items: list[EmployeeListItem]
    total: int


class CreatedEmployeeOut(BaseModel):
    employee: EmployeeFullOut
    temporary_password: str