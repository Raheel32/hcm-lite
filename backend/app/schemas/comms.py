from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ---------- audit ----------

class AuditActor(BaseModel):
    id: int | None
    email: str | None
    role: str | None


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    actor: AuditActor | None
    action: str
    entity_type: str
    entity_id: int | None
    summary: str
    before: dict | None
    after: dict | None


class AuditListOut(BaseModel):
    items: list[AuditOut]
    total: int


# ---------- notifications ----------

class NotificationOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str
    entity_type: str | None
    entity_id: int | None
    created_at: datetime
    read_at: datetime | None
    is_read: bool


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    total: int
    unread: int


class UnreadOut(BaseModel):
    unread: int


class MarkedOut(BaseModel):
    marked: int


# ---------- announcements ----------

class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    body: str = Field(min_length=3, max_length=2000)
    expires_on: date_type | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, v):
        v = " ".join(v.split())
        if len(v) < 3:
            raise ValueError("The title must be at least 3 characters")
        return v

    @field_validator("body")
    @classmethod
    def clean_body(cls, v):
        v = v.strip()  # line breaks inside the text are kept
        if len(v) < 3:
            raise ValueError("The text must be at least 3 characters")
        return v


class PostedBy(BaseModel):
    email: str
    role: str


class AnnouncementOut(BaseModel):
    id: int
    title: str
    body: str
    posted_by: PostedBy
    created_at: datetime
    expires_on: date_type | None
    is_active: bool


class AnnouncementListOut(BaseModel):
    items: list[AnnouncementOut]
    total: int