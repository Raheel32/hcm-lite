from datetime import date as date_type
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.dashboard import RangeOut

LABEL = "AI-generated"


# ---------- chat ----------

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]  # no "system" or "tool": the client cannot fake those
    content: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=6)

    @field_validator("message")
    @classmethod
    def clean_message(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("The message cannot be empty")
        return v


class ChatResponse(BaseModel):
    answer: str
    ai_generated: bool = True
    label: str = LABEL
    tools_used: list[str]


# ---------- smart leave ----------

class SmartLeaveRequest(BaseModel):
    text: str = Field(min_length=3, max_length=500)

    @field_validator("text")
    @classmethod
    def clean_text(cls, v):
        v = v.strip()
        if len(v) < 3:
            raise ValueError("Please write a little more")
        return v


class SmartLeaveSuggestion(BaseModel):
    leave_type_id: int
    leave_type: str
    start_date: date_type
    end_date: date_type
    reason: str
    working_days: int | None
    available_before: int | None
    available_after: int | None


class SmartLeaveResponse(BaseModel):
    suggestion: SmartLeaveSuggestion | None
    warning: str | None
    fallback: bool
    message: str | None
    ai_generated: bool = True
    label: str = LABEL


# ---------- dashboard summary ----------

class SummaryRequest(BaseModel):
    dashboard: Literal["admin", "hr"] | None = None
    date_from: date_type | None = None
    date_to: date_type | None = None
    department_id: int | None = None


class SummaryResponse(BaseModel):
    dashboard: str
    range: RangeOut
    bullets: list[str]
    fallback: bool
    message: str | None
    ai_generated: bool = True
    label: str = LABEL


# ---------- logs ----------

class AiLogOut(BaseModel):
    id: int
    created_at: datetime
    user_email: str
    kind: str
    question: str
    status: str
    tools_called: list[str]
    rounds: int
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    error: str | None


class AiLogListOut(BaseModel):
    items: list[AiLogOut]
    total: int