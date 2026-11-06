from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.user import Role, User
from app.schemas.dashboard import AdminDashboardOut, EmployeeDashboardOut, HrDashboardOut
from app.services import dashboard_service, employee_service, settings_service
from app.services import metrics_service as metrics

router = APIRouter(prefix="/dashboard", tags=["Dashboards"])

admin_only = require_roles(Role.admin)
staff = require_roles(Role.admin, Role.hr)
employee_only = require_roles(Role.employee)

CAUGHT = (metrics.Invalid, metrics.NotFound, employee_service.EmployeeNotFound, settings_service.NotInitialized)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, metrics.Invalid):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, metrics.NotFound):
        return HTTPException(status_code=404, detail="Department not found")
    if isinstance(exc, employee_service.EmployeeNotFound):
        return HTTPException(status_code=404, detail="Employee record not found")
    if isinstance(exc, settings_service.NotInitialized):
        return HTTPException(status_code=409, detail="Complete first-run setup first")
    raise exc


@router.get("/admin", response_model=AdminDashboardOut)
def admin_dashboard(
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    department_id: int | None = None,
    user: User = Depends(admin_only),
    db: Session = Depends(get_db),
):
    try:
        return dashboard_service.admin_dashboard(db, user, date_from, date_to, department_id)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/hr", response_model=HrDashboardOut)
def hr_dashboard(
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    department_id: int | None = None,
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return dashboard_service.hr_dashboard(db, user, date_from, date_to, department_id)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/me", response_model=EmployeeDashboardOut)
def my_dashboard(user: User = Depends(employee_only), db: Session = Depends(get_db)):
    try:
        return dashboard_service.employee_dashboard(db, user)
    except CAUGHT as exc:
        raise _http(exc)