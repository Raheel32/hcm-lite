from pydantic import BaseModel, ConfigDict, Field, field_validator


class OrgItemCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)

    @field_validator("name")
    @classmethod
    def clean_name(cls, v):
        v = " ".join(v.split())  # trims and collapses extra spaces
        if len(v) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v


class OrgItemUpdate(OrgItemCreate):
    is_active: bool = True


class OrgItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    is_active: bool