from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.payroll import RunStatus
from app.models.user import Role, User
from app.schemas.payroll import (
    PayslipListOut,
    PayslipOut,
    RunCreate,
    RunListOut,
    RunOut,
    SalaryStructureOut,
    SalaryUpdate,
)
from app.services import employee_service, settings_service
from app.services import payroll_service as service

router = APIRouter(prefix="/payroll", tags=["Payroll"])

hr_only = require_roles(Role.hr)
admin_only = require_roles(Role.admin)
staff = require_roles(Role.admin, Role.hr)
employee_only = require_roles(Role.employee)
anyone = require_roles(Role.admin, Role.hr, Role.employee)

CAUGHT = service.ERRORS + (employee_service.EmployeeNotFound, settings_service.NotInitialized)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, employee_service.EmployeeNotFound):
        return HTTPException(status_code=404, detail="Employee record not found")
    if isinstance(exc, settings_service.NotInitialized):
        return HTTPException(status_code=409, detail="Complete first-run setup first")
    if isinstance(exc, service.NotFound):
        return HTTPException(status_code=404, detail="Payroll record not found")
    if isinstance(exc, service.Invalid):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, service.Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


# ---------- Payroll runs ----------

@router.post("/runs", response_model=RunOut, status_code=201)
def create_run(data: RunCreate, user: User = Depends(hr_only), db: Session = Depends(get_db)):
    try:
        return service.run_out(db, service.create_run(db, user, data.year, data.month))
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/runs", response_model=RunListOut)
def list_runs(
    status_filter: RunStatus | None = Query(None, alias="status"),
    year: int | None = Query(None, ge=2000, le=2100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return service.list_runs(db, status_filter, year, limit, offset)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    try:
        return service.get_run(db, run_id)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/runs/{run_id}/payslips", response_model=PayslipListOut)
def run_payslips(
    run_id: int,
    department_id: int | None = None,
    search: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    try:
        return service.list_run_payslips(db, run_id, department_id, search, limit, offset)
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/runs/{run_id}/recalculate", response_model=RunOut)
def recalculate_run(run_id: int, user: User = Depends(hr_only), db: Session = Depends(get_db)):
    try:
        return service.run_out(db, service.recalculate_run(db, run_id))
    except CAUGHT as exc:
        raise _http(exc)


@router.post("/runs/{run_id}/finalize", response_model=RunOut)
def finalize_run(run_id: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    try:
        return service.run_out(db, service.finalize_run(db, user, run_id))
    except CAUGHT as exc:
        raise _http(exc)


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: int, user: User = Depends(hr_only), db: Session = Depends(get_db)):
    try:
        service.delete_run(db, run_id)
    except CAUGHT as exc:
        raise _http(exc)


# ---------- Payslips (the /me route must come BEFORE the /{payslip_id} routes) ----------

@router.get("/payslips/me", response_model=PayslipListOut)
def my_payslips(
    year: int | None = Query(None, ge=2000, le=2100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
):
    try:
        return service.list_my_payslips(db, user, year, limit, offset)
    except CAUGHT as exc:
        raise _http(exc)


@router.get("/payslips/{payslip_id}", response_model=PayslipOut)
def get_payslip(payslip_id: int, user: User = Depends(anyone), db: Session = Depends(get_db)):
    try:
        return service.get_payslip(db, user, payslip_id)
    except CAUGHT as exc:
        raise _http(exc)


@router.get(
    "/payslips/{payslip_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def payslip_pdf(payslip_id: int, user: User = Depends(anyone), db: Session = Depends(get_db)):
    try:
        content, filename = service.payslip_pdf(db, user, payslip_id)
    except CAUGHT as exc:
        raise _http(exc)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- Salary structure ----------

@router.get("/salary-structure/{employee_id}", response_model=SalaryStructureOut)
def get_salary_structure(employee_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    try:
        return service.salary_structure(db, employee_id)
    except CAUGHT as exc:
        raise _http(exc)


@router.put("/salary-structure/{employee_id}", response_model=SalaryStructureOut)
def update_salary_structure(
    employee_id: int,
    data: SalaryUpdate,
    user: User = Depends(hr_only),
    db: Session = Depends(get_db),
):
    try:
        return service.update_salary_structure(db, employee_id, data.basic_salary)
    except CAUGHT as exc:
        raise _http(exc)