from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.database import get_db
from app.models.employee import EmployeeStatus
from app.models.user import Role, User
from app.schemas.employee import (
    CreatedEmployeeOut,
    EmployeeCreate,
    EmployeeFullOut,
    EmployeeListOut,
    EmployeeProfileOut,
    EmployeeUpdate,
    ExitRequest,
    SelfUpdate,
)
from app.services import employee_service as service
from app.services import user_service

router = APIRouter(prefix="/employees", tags=["Employees"])

staff = require_roles(Role.admin, Role.hr)
hr_only = require_roles(Role.hr)
employee_only = require_roles(Role.employee)


def _not_found():
    return HTTPException(status_code=404, detail="Employee not found")


# ---------- Employee self-service (must come BEFORE the /{employee_id} routes) ----------

@router.get("/me", response_model=EmployeeProfileOut)
def my_profile(user: User = Depends(employee_only), db: Session = Depends(get_db)):
    try:
        return service.get_employee_by_user(db, user.id)
    except service.EmployeeNotFound:
        raise HTTPException(status_code=404, detail="No employee record for this login")


@router.put("/me", response_model=EmployeeProfileOut)
def update_my_profile(
    data: SelfUpdate,
    user: User = Depends(employee_only),
    db: Session = Depends(get_db),
):
    try:
        return service.update_self(db, user, data.phone, data.address)
    except service.EmployeeNotFound:
        raise HTTPException(status_code=404, detail="No employee record for this login")


# ---------- HR / Admin ----------

@router.post("", response_model=CreatedEmployeeOut, status_code=201)
def register_employee(
    data: EmployeeCreate,
    user: User = Depends(hr_only),
    db: Session = Depends(get_db),
):
    try:
        employee, temp_password = service.register_employee(db, data)
    except service.InvalidDepartment:
        raise HTTPException(status_code=400, detail="Unknown or inactive department")
    except service.InvalidDesignation:
        raise HTTPException(status_code=400, detail="Unknown or inactive designation")
    except service.CodeTaken:
        raise HTTPException(status_code=409, detail="This employee code is already used")
    except user_service.EmailTaken:
        raise HTTPException(status_code=409, detail="A user with this email already exists")
    return {"employee": employee, "temporary_password": temp_password}


@router.get("", response_model=EmployeeListOut)
def list_employees(
    search: str | None = None,
    department_id: int | None = None,
    designation_id: int | None = None,
    status_filter: EmployeeStatus | None = Query(None, alias="status"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(staff),
    db: Session = Depends(get_db),
):
    items, total = service.list_employees(
        db, search, department_id, designation_id, status_filter, limit, offset
    )
    return {"items": items, "total": total}


@router.get("/{employee_id}", response_model=EmployeeFullOut)
def get_employee(employee_id: int, user: User = Depends(staff), db: Session = Depends(get_db)):
    try:
        return service.get_employee(db, employee_id)
    except service.EmployeeNotFound:
        raise _not_found()


@router.put("/{employee_id}", response_model=EmployeeFullOut)
def update_employee(
    employee_id: int,
    data: EmployeeUpdate,
    user: User = Depends(hr_only),
    db: Session = Depends(get_db),
):
    try:
        return service.update_employee(db, employee_id, data)
    except service.EmployeeNotFound:
        raise _not_found()
    except service.AlreadyExited:
        raise HTTPException(status_code=409, detail="This employee has already exited")
    except service.InvalidDepartment:
        raise HTTPException(status_code=400, detail="Unknown or inactive department")
    except service.InvalidDesignation:
        raise HTTPException(status_code=400, detail="Unknown or inactive designation")


@router.post("/{employee_id}/exit", response_model=EmployeeFullOut)
def exit_employee(
    employee_id: int,
    data: ExitRequest,
    user: User = Depends(hr_only),
    db: Session = Depends(get_db),
):
    try:
        return service.exit_employee(db, employee_id, data.exit_date)
    except service.EmployeeNotFound:
        raise _not_found()
    except service.AlreadyExited:
        raise HTTPException(status_code=409, detail="This employee has already exited")
    except service.BadExitDate:
        raise HTTPException(
            status_code=400,
            detail="Exit date must be between the join date and today",
        )