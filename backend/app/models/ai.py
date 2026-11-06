from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String

from app.database import Base


class AiLog(Base):
    """One row for every AI call, including refused and failed ones."""

    __tablename__ = "ai_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False, index=True)       # chat, smart_leave, summary
    question = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, index=True)     # ok, invalid_output, timeout, error, rate_limited, unavailable
    tools_called = Column(JSON, nullable=False, default=list)
    rounds = Column(Integer, nullable=False, default=0)          # how often the model was asked
    latency_ms = Column(Integer, nullable=False, default=0)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    error = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, index=True, default=lambda: datetime.now(timezone.utc)
    )