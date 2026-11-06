import math

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.user import Role, User
from app.schemas.ai import (
    AiLogListOut,
    ChatRequest,
    ChatResponse,
    SmartLeaveRequest,
    SmartLeaveResponse,
    SummaryRequest,
    SummaryResponse,
)
from app.services import ai_service, employee_service, settings_service
from app.services import metrics_service as metrics
from app.services.ai_client import get_llm_client

router = APIRouter(prefix="/ai", tags=["AI assistant"])

anyone = require_roles(Role.admin, Role.hr, Role.employee)
staff = require_roles(Role.admin, Role.hr)
employee_only = require_roles(Role.employee)
admin_only = require_roles(Role.admin)

CAUGHT = (
    ai_service.RateLimited,
    ai_service.Unavailable,
    ai_service.Forbidden,
    settings_service.NotInitialized,
    employee_service.EmployeeNotFound,
    metrics.Invalid,
    metrics.NotFound,
)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, ai_service.RateLimited):
        minutes = max(1, math.ceil(exc.retry_after / 60))
        return HTTPException(
            status_code=429,
            detail=f"You have reached the limit of {exc.limit} AI requests per hour. Please try again in about {minutes} minute(s).",
            headers={"Retry-After": str(exc.retry_after)},
        )
    if isinstance(exc, ai_service.Unavailable):
        return HTTPException(status_code=503, detail=exc.message)
    if isinstance(exc, ai_service.Forbidden):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, settings_service.NotInitialized):
        return HTTPException(status_code=409, detail="Complete first-run setup first")
    if isinstance(exc, employee_service.EmployeeNotFound):
        return HTTPException(status_code=404, detail="Employee record not found")
    if isinstance(exc, metrics.Invalid):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, metrics.NotFound):
        return HTTPException(status_code=404, detail="Department not found")
    raise exc


@router.post("/chat", response_model=ChatResponse)
def chat(
    data: ChatRequest,
    user: User = Depends(anyone),
    db: Session = Depends(get_db),
    llm=Depends(get_llm_client),
):
    try:
        return ai_service.chat(db, user, llm, data.message, data.history)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/smart-leave", response_model=SmartLeaveResponse)
def smart_leave(
    data: SmartLeaveRequest,
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
    llm=Depends(get_llm_client),
):
    try:
        return ai_service.smart_leave(db, user, llm, data.text)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/dashboard-summary", response_model=SummaryResponse)
def dashboard_summary(
    data: SummaryRequest | None = None,
    user: User = Depends(staff),
    db: Session = Depends(get_db),
    llm=Depends(get_llm_client),
):
    data = data or SummaryRequest()
    try:
        return ai_service.summarize_dashboard(
            db, user, llm, data.dashboard, data.date_from, data.date_to, data.department_id
        )
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/logs", response_model=AiLogListOut)
def list_logs(
    user_id: int | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    return ai_service.list_logs(db, user_id, kind, status, limit, offset)