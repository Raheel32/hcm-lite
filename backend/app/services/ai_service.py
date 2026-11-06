import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date as date_type
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import clock
from app.models.ai import AiLog
from app.models.user import Role, User
from app.schemas.ai import LABEL
from app.services import ai_prompts, ai_tools, audit_service, dashboard_service
from app.services import employee_service, leave_service, settings_service
from app.services.ai_client import AiProviderError, AiTimeout
from app.models.user import User as UserModel

logger = logging.getLogger("hcm.ai")

MAX_TOOL_ROUNDS = 4
MAX_CALLS_PER_ROUND = 4
MAX_RESULT_CHARS = 12000
MAX_ANSWER_CHARS = 4000
NOT_COUNTED = ("rate_limited", "unavailable")  # these never reached the AI, so they do not use up the allowance

MSG_UNAVAILABLE = "The AI assistant is not available right now. Please use the dashboards and forms, or try again later."
MSG_TIMEOUT = "The AI assistant took too long to answer. Please try again in a moment."
MSG_ERROR = "The AI assistant ran into a problem. Please try again in a moment."
MSG_LEAVE_FALLBACK = "I could not turn that into a leave request. Please fill in the form yourself."
MSG_NOT_LEAVE = "That does not look like a leave request. Please fill in the form yourself."
MSG_NO_LEAVE_TYPES = "No leave types have been set up yet. Please ask HR or Admin."
MSG_SUMMARY_FALLBACK = "I could not write a reliable summary this time. Please read the numbers on the dashboard."
MSG_NO_DATA = "There is not enough data yet to summarise."


class Unavailable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class RateLimited(Exception):
    def __init__(self, retry_after: int, limit: int):
        super().__init__("rate limited")
        self.retry_after = retry_after
        self.limit = limit


class Forbidden(Exception):
    pass


# ---------- the shared wrapper: limit, deadline, errors and logging ----------

@dataclass
class CallInfo:
    started: float
    deadline: float
    tools_called: list = field(default_factory=list)
    rounds: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    status: str = "ok"
    error: str | None = None

    def complete(self, llm, messages: list, tools: list | None = None) -> dict:
        """Ask the model, with whatever is left of the overall time budget as the timeout."""
        remaining = self.deadline - time.monotonic()
        if remaining < 0.2:
            raise AiTimeout()
        reply = llm.complete(messages, tools, remaining)
        self.rounds += 1
        usage = reply.get("usage") or {}
        if isinstance(usage.get("prompt_tokens"), int):
            self.prompt_tokens = (self.prompt_tokens or 0) + usage["prompt_tokens"]
        if isinstance(usage.get("completion_tokens"), int):
            self.completion_tokens = (self.completion_tokens or 0) + usage["completion_tokens"]
        return reply


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _retry_after(db: Session, user: User) -> int | None:
    """None if the person may ask now, otherwise the seconds until a slot frees up."""
    limit = settings.AI_RATE_LIMIT_PER_HOUR
    if limit <= 0:
        return None
    now = datetime.now(timezone.utc)
    rows = (
        db.query(AiLog.created_at)
        .filter(
            AiLog.user_id == user.id,
            AiLog.created_at >= now - timedelta(hours=1),
            AiLog.status.notin_(NOT_COUNTED),
        )
        .order_by(AiLog.created_at)
        .all()
    )
    if len(rows) < limit:
        return None
    frees_up = _utc(rows[len(rows) - limit][0]) + timedelta(hours=1)
    return max(1, math.ceil((frees_up - now).total_seconds()))


def _log(db: Session, user: User, kind: str, question: str, status: str,
         call: CallInfo | None = None, error: str | None = None) -> None:
    error = error or (call.error if call else None)
    db.add(AiLog(
        user_id=user.id,
        kind=kind,
        question=question[:500],
        status=status,
        tools_called=list(call.tools_called) if call else [],
        rounds=call.rounds if call else 0,
        latency_ms=int((time.monotonic() - call.started) * 1000) if call else 0,
        prompt_tokens=call.prompt_tokens if call else None,
        completion_tokens=call.completion_tokens if call else None,
        error=error[:255] if error else None,
    ))
    db.commit()


def run_ai_call(db: Session, user: User, kind: str, question: str, llm, work):
    """Every AI request goes through here: setup check, hourly limit, deadline, friendly errors, logging."""
    settings_service.get_settings(db)  # raises NotInitialized before first-run setup

    retry_after = _retry_after(db, user)
    if retry_after is not None:
        _log(db, user, kind, question, "rate_limited")
        raise RateLimited(retry_after, settings.AI_RATE_LIMIT_PER_HOUR)
    if llm is None:
        _log(db, user, kind, question, "unavailable", error="the AI provider is not configured")
        raise Unavailable(MSG_UNAVAILABLE)

    started = time.monotonic()
    call = CallInfo(started=started, deadline=started + settings.AI_TIMEOUT_SECONDS)
    try:
        return work(call)
    except AiTimeout:
        call.status, call.error = "timeout", "the AI provider did not answer in time"
        raise Unavailable(MSG_TIMEOUT)
    except AiProviderError as exc:
        call.status, call.error = "error", str(exc)
        raise Unavailable(MSG_ERROR)
    except Exception as exc:  # a bug must not leak details to the user either
        logger.exception("The AI call failed unexpectedly")
        db.rollback()
        call.status, call.error = "error", f"unexpected {type(exc).__name__}"
        raise Unavailable(MSG_ERROR)
    finally:
        _log(db, user, kind, question, call.status, call)


# ---------- chat with tools ----------

def _tool_content(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        return json.dumps({"error": "The result was too large. Ask again for one department or a shorter date range."})
    return text


def chat(db: Session, user: User, llm, message: str, history: list) -> dict:
    def work(call: CallInfo) -> dict:
        tools = ai_tools.definitions_for(user.role)
        messages = [{"role": "system", "content": ai_prompts.chat_system_prompt(db, user)}]
        messages += [{"role": turn.role, "content": turn.content} for turn in history]
        messages.append({"role": "user", "content": message})

        for round_number in range(MAX_TOOL_ROUNDS):
            reply = call.complete(llm, messages, tools)
            requested = (reply.get("tool_calls") or [])[:MAX_CALLS_PER_ROUND]
            if not requested:
                answer = (reply.get("content") or "").strip()
                if not answer:
                    raise AiProviderError("the model returned an empty answer")
                return {
                    "answer": answer[:MAX_ANSWER_CHARS],
                    "ai_generated": True,
                    "label": LABEL,
                    "tools_used": list(dict.fromkeys(call.tools_called)),
                }

            prepared = []
            for index, item in enumerate(requested):
                arguments = item.get("arguments")
                prepared.append({
                    "id": item.get("id") or f"call_{round_number}_{index}",
                    "name": item.get("name") or "",
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments or {}),
                })
            messages.append({
                "role": "assistant",
                "content": reply.get("content") or "",
                "tool_calls": [
                    {"id": p["id"], "type": "function", "function": {"name": p["name"], "arguments": p["arguments"]}}
                    for p in prepared
                ],
            })
            for p in prepared:
                result = ai_tools.run_tool(db, user, p["name"], p["arguments"])
                call.tools_called.append(p["name"][:60])
                messages.append({"role": "tool", "tool_call_id": p["id"], "content": _tool_content(result)})
        raise AiProviderError("too many tool rounds")

    return run_ai_call(db, user, "chat", message, llm, work)


# ---------- reading the model's JSON ----------

def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    first, last = text.find("{"), text.rfind("}")
    if first == -1 or last <= first:
        raise ValueError("no JSON object was found")
    data = json.loads(text[first:last + 1])
    if not isinstance(data, dict):
        raise ValueError("the answer is not a JSON object")
    return data


# ---------- smart leave ----------

class _LeaveJson(BaseModel):
    type: str
    start: date_type
    end: date_type
    reason: str


def _parse_leave(text: str, types_by_name: dict):
    """Returns ("leave", (leave_type, start, end, reason)) or ("not_leave", None). Raises ValueError if unusable."""
    data = _extract_json(text)
    if isinstance(data.get("error"), str) and data["error"].strip() and "start" not in data:
        return "not_leave", None
    try:
        parsed = _LeaveJson(**{key: data.get(key) for key in ("type", "start", "end", "reason")})
    except ValidationError as exc:
        raise ValueError(ai_tools.short_errors(exc))
    leave_type = types_by_name.get(parsed.type.strip().lower())
    if leave_type is None:
        raise ValueError(f"'{parsed.type}' is not one of the leave types: {', '.join(t.name for t in types_by_name.values())}")
    if not (2000 <= parsed.start.year <= 2100 and 2000 <= parsed.end.year <= 2100):
        raise ValueError("the dates must be between the years 2000 and 2100")
    if parsed.end < parsed.start:
        raise ValueError("the end date is before the start date")
    reason = " ".join(parsed.reason.split())
    if len(reason) < 3:
        raise ValueError("the reason is too short")
    return "leave", (leave_type, parsed.start, parsed.end, reason[:500])


def _leave_response(suggestion=None, warning=None, fallback=False, message=None) -> dict:
    return {
        "suggestion": suggestion, "warning": warning, "fallback": fallback, "message": message,
        "ai_generated": True, "label": LABEL,
    }


def smart_leave(db: Session, user: User, llm, text: str) -> dict:
    employee_service.get_employee_by_user(db, user.id)  # raises EmployeeNotFound
    settings_service.get_settings(db)
    types = ai_prompts.active_leave_types(db)
    if not types:
        return _leave_response(fallback=True, message=MSG_NO_LEAVE_TYPES)
    types_by_name = {leave_type.name.lower(): leave_type for leave_type in types}

    def work(call: CallInfo) -> dict:
        messages = [
            {"role": "system", "content": ai_prompts.smart_leave_system_prompt(db)},
            {"role": "user", "content": text},
        ]
        problem = None
        for _attempt in range(2):  # one try and one retry, as the brief says
            reply = call.complete(llm, messages)
            content = reply.get("content") or ""
            try:
                kind, choice = _parse_leave(content, types_by_name)
            except ValueError as exc:
                problem = str(exc)
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That answer was not valid: {problem}. Reply again with ONLY the JSON object."},
                ]
                continue
            if kind == "not_leave":
                return _leave_response(fallback=True, message=MSG_NOT_LEAVE)

            leave_type, start, end, reason = choice
            warning, info = None, None
            try:  # the same rules as the real form, so problems show up before the person submits
                info = leave_service.preview(db, user, leave_type.id, start, end)
            except leave_service.ERRORS as exc:
                warning = str(exc) or "These dates cannot be requested."
            return _leave_response(
                suggestion={
                    "leave_type_id": leave_type.id,
                    "leave_type": leave_type.name,
                    "start_date": start,
                    "end_date": end,
                    "reason": reason,
                    "working_days": info["working_days"] if info else None,
                    "available_before": info["available_before"] if info else None,
                    "available_after": info["available_after"] if info else None,
                },
                warning=warning,
            )
        call.status, call.error = "invalid_output", (problem or "unusable answer")[:200]
        return _leave_response(fallback=True, message=MSG_LEAVE_FALLBACK)

    return run_ai_call(db, user, "smart_leave", text, llm, work)


# ---------- dashboard summary ----------

_NUMBER = re.compile(r"\d+(?:[,.]\d+)*")


def numbers_in(text: str) -> set[float]:
    found = set()
    for token in _NUMBER.findall(text):
        try:
            found.add(round(float(token.replace(",", "")), 2))
        except ValueError:
            pass
    return found


def _parse_bullets(text: str, allowed: set[float]) -> list[str]:
    data = _extract_json(text)
    raw = data.get("bullets")
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError('the JSON must look like {"bullets": ["...", "..."]}')
    bullets = [re.sub(r"^[\s\-\u2022*]+", "", item).strip() for item in raw]
    bullets = [item for item in bullets if item]
    if not 3 <= len(bullets) <= 5:
        raise ValueError("give 3 to 5 bullets")
    if any(len(item) > 300 for item in bullets):
        raise ValueError("each bullet must be under 300 characters")
    unknown = sorted(n for n in numbers_in(" ".join(bullets)) if n not in allowed)
    if unknown:
        raise ValueError("these numbers are not in the data: " + ", ".join(f"{n:g}" for n in unknown))
    return bullets


_ADMIN_KEYS = (
    "range", "kpis", "headcount_by_department", "payroll_by_department", "payroll_by_month",
    "average_salary_by_department", "gender", "tenure",
)
_HR_KEYS = (
    "range", "kpis", "period", "absenteeism_by_department", "top_late_comers",
    "leave_by_type", "leave_year", "leave_utilization",
)


def summarize_dashboard(db: Session, user: User, llm, dashboard, date_from, date_to, department_id) -> dict:
    kind = dashboard or ("admin" if user.role == Role.admin else "hr")
    if kind == "admin" and user.role != Role.admin:
        raise Forbidden("Only Admin can summarise the company dashboard.")

    # The numbers come from the server, never from the browser
    if kind == "admin":
        data = dashboard_service.admin_dashboard(db, user, date_from, date_to, department_id)
        keys = _ADMIN_KEYS
    else:
        data = dashboard_service.hr_dashboard(db, user, date_from, date_to, department_id)
        keys = _HR_KEYS
    result_range = data["range"]
    if data["kpis"]["headcount"] == 0:
        return _summary_response(kind, result_range, [], True, MSG_NO_DATA)

    source = audit_service.clean({key: data[key] for key in keys})
    source_text = json.dumps(source)
    allowed = numbers_in(source_text)
    allowed |= {round(n) for n in allowed} | {round(n, 1) for n in allowed}
    question = f"Dashboard summary ({kind}), {result_range['date_from']} to {result_range['date_to']}"

    def work(call: CallInfo) -> dict:
        messages = [
            {"role": "system", "content": ai_prompts.SUMMARY_SYSTEM},
            {"role": "user", "content": "Dashboard data:\n" + source_text},
        ]
        problem = None
        for _attempt in range(2):
            reply = call.complete(llm, messages)
            content = reply.get("content") or ""
            try:
                bullets = _parse_bullets(content, allowed)
            except ValueError as exc:
                problem = str(exc)
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That answer was not valid: {problem}. Reply again with ONLY the JSON object."},
                ]
                continue
            return _summary_response(kind, result_range, bullets, False, None)
        call.status, call.error = "invalid_output", (problem or "unusable answer")[:200]
        return _summary_response(kind, result_range, [], True, MSG_SUMMARY_FALLBACK)

    return run_ai_call(db, user, "summary", question, llm, work)


def _summary_response(kind, result_range, bullets, fallback, message) -> dict:
    return {
        "dashboard": kind, "range": result_range, "bullets": bullets, "fallback": fallback,
        "message": message, "ai_generated": True, "label": LABEL,
    }


# ---------- the log (Admin) ----------

def list_logs(db: Session, user_id, kind, status, limit: int, offset: int) -> dict:
    query = db.query(AiLog, UserModel.email).join(UserModel, AiLog.user_id == UserModel.id)
    if user_id is not None:
        query = query.filter(AiLog.user_id == user_id)
    if kind:
        query = query.filter(AiLog.kind == kind)
    if status:
        query = query.filter(AiLog.status == status)
    total = query.count()
    rows = query.order_by(AiLog.id.desc()).limit(limit).offset(offset).all()
    items = [
        {
            "id": log.id,
            "created_at": clock.to_local(log.created_at),
            "user_email": email,
            "kind": log.kind,
            "question": log.question,
            "status": log.status,
            "tools_called": log.tools_called or [],
            "rounds": log.rounds,
            "latency_ms": log.latency_ms,
            "prompt_tokens": log.prompt_tokens,
            "completion_tokens": log.completion_tokens,
            "error": log.error,
        }
        for log, email in rows
    ]
    return {"items": items, "total": total}