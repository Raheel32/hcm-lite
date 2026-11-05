from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.leave import LeaveStatus
from app.schemas.attendance import EmployeeBrief


class LeaveApply(BaseModel):
    leave_type_id: int
    start_date: date_type
    end_date: date_type
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v):
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("Please explain the reason in at least 3 characters")
        return v

    @model_validator(mode="after")
    def check_order(self):
        if self.end_date < self.start_date:
            raise ValueError("End date cannot be before the start date")
        return self


class LeaveTypeBrief(BaseModel):
    id: int
    name: str
    is_paid: bool


class LeaveOut(BaseModel):
    id: int
    employee: EmployeeBrief
    leave_type: LeaveTypeBrief
    start_date: date_type
    end_date: date_type
    days: int
    reason: str
    status: LeaveStatus
    review_comment: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime | None


class LeaveListOut(BaseModel):
    items: list[LeaveOut]
    total: int


class BalanceItem(BaseModel):
    leave_type_id: int
    leave_type: str
    is_paid: bool
    is_unlimited: bool
    yearly_quota: int | None
    used: int        # approved days
    pending: int     # days waiting for a decision (reserved)
    remaining: int | None  # quota - used - pending, never below 0; None when unlimited


class BalanceOut(BaseModel):
    year: int
    items: list[BalanceItem]


class PreviewOut(BaseModel):
    working_days: int
    yearly_quota: int | None
    available_before: int | None
    available_after: int | None