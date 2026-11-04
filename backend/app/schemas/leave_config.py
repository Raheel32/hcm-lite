from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.schemas.org import OrgItemCreate


class LeaveTypeCreate(OrgItemCreate):
    # Inherits the cleaned "name" field from the departments schema
    yearly_quota: int | None = Field(default=None, ge=0, le=366)
    is_paid: bool = True


class LeaveTypeUpdate(LeaveTypeCreate):
    is_active: bool = True


class LeaveTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    yearly_quota: int | None
    is_paid: bool
    is_active: bool

    @computed_field
    @property
    def is_unlimited(self) -> bool:
        return self.yearly_quota is None


class HolidayCreate(OrgItemCreate):
    date: date_type

    @field_validator("date")
    @classmethod
    def check_year(cls, v):
        if v.year < 2000 or v.year > 2100:
            raise ValueError("Date must be between the years 2000 and 2100")
        return v


class HolidayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: date_type
    name: str