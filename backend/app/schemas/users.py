from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models.user import Role


class HRCreate(BaseModel):
    email: EmailStr


class UserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: Role
    is_active: bool
    must_change_password: bool
    created_at: datetime | None = None


class UserListOut(BaseModel):
    items: list[UserListItem]
    total: int


class CreatedUserOut(BaseModel):
    user: UserListItem
    temporary_password: str


class ResetPasswordOut(BaseModel):
    email: EmailStr
    temporary_password: str