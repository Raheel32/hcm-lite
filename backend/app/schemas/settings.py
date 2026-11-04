from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SettingsBase(BaseModel):
    office_start: time
    grace_minutes: int = Field(ge=0, le=120)
    half_day_hours: float = Field(gt=0, le=12)
    weekend_days: list[int]
    office_lat: float | None = Field(default=None, ge=-90, le=90)
    office_lng: float | None = Field(default=None, ge=-180, le=180)
    radius_m: int = Field(ge=10, le=5000)

    @field_validator("weekend_days")
    @classmethod
    def check_weekend_days(cls, v):
        if any(d < 0 or d > 6 for d in v):
            raise ValueError("weekend_days must be numbers from 0 (Monday) to 6 (Sunday)")
        if len(set(v)) != len(v):
            raise ValueError("weekend_days must not repeat a day")
        if len(v) > 3:
            raise ValueError("weekend_days can have at most 3 days")
        return sorted(v)

    @model_validator(mode="after")
    def check_location_pair(self):
        if (self.office_lat is None) != (self.office_lng is None):
            raise ValueError("office_lat and office_lng must be set together")
        return self


class SettingsUpdate(SettingsBase):
    pass


class SettingsOut(SettingsBase):
    model_config = ConfigDict(from_attributes=True)
    updated_at: datetime | None = None