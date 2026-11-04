from pydantic import BaseModel, EmailStr, Field


class SetupRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=200)
    admin_email: EmailStr
    admin_password: str = Field(min_length=8, max_length=72)


class SetupStatus(BaseModel):
    initialized: bool