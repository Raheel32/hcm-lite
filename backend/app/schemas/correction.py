from datetime import date as date_type
from datetime import datetime
from datetime import time as time_type

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.correction import CorrectionStatus
from app.schemas.attendance import DayEntry, EmployeeBrief


class CorrectionCreate(BaseModel):
    date: date_type
    check_in: time_type | None = None   # office-local time, for example "09:00"
    check_out: time_type | None = None
    reason: str = Field(min_length=5, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v):
        v = " ".join(v.split())
        if len(v) < 5:
            raise ValueError("Please explain the reason in at least 5 characters")
        return v

    @field_validator("check_in", "check_out")
    @classmethod
    def drop_seconds(cls, v):
        return v.replace(second=0, microsecond=0) if v is not None else v

    @model_validator(mode="after")
    def need_a_time(self):
        if self.check_in is None and self.check_out is None:
            raise ValueError("Give a check-in time, a check-out time, or both")
        return self


class DecisionApprove(BaseModel):
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, v):
        if v is None:
            return None
        return " ".join(v.split()) or None


class DecisionReject(BaseModel):
    comment: str = Field(min_length=3, max_length=500)

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, v):
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("Please explain the decision in at least 3 characters")
        return v


class CorrectionOut(BaseModel):
    id: int
    employee: EmployeeBrief
    date: date_type
    requested_check_in: datetime | None
    requested_check_out: datetime | None
    reason: str
    status: CorrectionStatus
    review_comment: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime | None
    current: DayEntry | None  # what the attendance record looks like right now, so HR can compare


class CorrectionListOut(BaseModel):
    items: list[CorrectionOut]
    total: int