from datetime import date, timedelta

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.employee import Employee, EmployeeStatus
from app.models.org import Department, Designation
from app.models.user import Role, User
from app.schemas.employee import EmployeeCreate, EmployeeUpdate
from app.services import user_service
from app.services import audit_service as audit

EMPLOYEE_FIELDS = (
    "employee_code", "full_name", "phone", "address", "gender", "department_id",
    "designation_id", "join_date", "exit_date", "status", "basic_salary",
)


class InvalidDepartment(Exception):
    pass


class InvalidDesignation(Exception):
    pass


class CodeTaken(Exception):
    pass


class EmployeeNotFound(Exception):
    pass


class AlreadyExited(Exception):
    pass


class BadExitDate(Exception):
    pass


def _require_active(db: Session, model, item_id: int, current_id: int | None, error):
    """The item must exist and be active, unless the employee already has it (it may have been deactivated later)."""
    item = db.get(model, item_id)
    if item is None:
        raise error()
    if not item.is_active and item_id != current_id:
        raise error()


def _code_taken(db: Session, code: str) -> bool:
    return db.query(Employee.id).filter(Employee.employee_code == code).first() is not None


def _next_code(db: Session) -> str:
    number = (db.query(func.max(Employee.id)).scalar() or 0) + 1
    while True:
        code = f"EMP-{number:04d}"
        if not _code_taken(db, code):
            return code
        number += 1


def register_employee(db: Session, data: EmployeeCreate) -> tuple[Employee, str]:
    # Check everything that can fail BEFORE creating the login
    _require_active(db, Department, data.department_id, None, InvalidDepartment)
    _require_active(db, Designation, data.designation_id, None, InvalidDesignation)

    if data.employee_code:
        if _code_taken(db, data.employee_code):
            raise CodeTaken()
        code = data.employee_code
    else:
        code = _next_code(db)

    try:
        # Login and employee are saved together or not at all
        user, temp_password = user_service.add_login(db, data.email, Role.employee)
        employee = Employee(
            user_id=user.id,
            employee_code=code,
            full_name=data.full_name,
            phone=data.phone,
            address=data.address,
            gender=data.gender,
            department_id=data.department_id,
            designation_id=data.designation_id,
            join_date=data.join_date,
            status=data.status,
            basic_salary=data.basic_salary,
        )
        db.add(employee)
        db.flush()
        audit.record(
            db, "employee.create", "employee", employee.id,
            f"Registered employee {employee.full_name} ({employee.employee_code})",
            after={**audit.snapshot(employee, EMPLOYEE_FIELDS), "email": user.email},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(employee)
    return employee, temp_password


def list_employees(
    db: Session,
    search: str | None = None,
    department_id: int | None = None,
    designation_id: int | None = None,
    status: EmployeeStatus | None = None,
    limit: int = 20,
    offset: int = 0,
):
    query = db.query(Employee).join(User, Employee.user_id == User.id)

    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Employee.full_name.ilike(pattern),
                Employee.employee_code.ilike(pattern),
                User.email.ilike(pattern),
            )
        )
    if department_id is not None:
        query = query.filter(Employee.department_id == department_id)
    if designation_id is not None:
        query = query.filter(Employee.designation_id == designation_id)
    if status is not None:
        query = query.filter(Employee.status == status)

    total = query.count()
    items = (
        query.options(
            joinedload(Employee.department),
            joinedload(Employee.designation),
            joinedload(Employee.user),
        )
        .order_by(func.lower(Employee.full_name), Employee.id)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return items, total


def get_employee(db: Session, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise EmployeeNotFound()
    return employee


def get_employee_by_user(db: Session, user_id: int) -> Employee:
    employee = db.query(Employee).filter(Employee.user_id == user_id).first()
    if employee is None:
        raise EmployeeNotFound()
    return employee


def update_employee(db: Session, employee_id: int, data: EmployeeUpdate) -> Employee:
    employee = get_employee(db, employee_id)
    if employee.status == EmployeeStatus.exited:
        raise AlreadyExited()

    _require_active(db, Department, data.department_id, employee.department_id, InvalidDepartment)
    _require_active(db, Designation, data.designation_id, employee.designation_id, InvalidDesignation)

    before = audit.snapshot(employee, EMPLOYEE_FIELDS)
    employee.full_name = data.full_name
    employee.phone = data.phone
    employee.address = data.address
    employee.gender = data.gender
    employee.department_id = data.department_id
    employee.designation_id = data.designation_id
    employee.join_date = data.join_date
    employee.status = data.status
    employee.basic_salary = data.basic_salary
    audit.record_update(
        db, "employee", employee.id,
        f"Updated employee {employee.full_name} ({employee.employee_code})",
        before, audit.snapshot(employee, EMPLOYEE_FIELDS),
    )
    db.commit()
    db.refresh(employee)
    return employee


def exit_employee(db: Session, employee_id: int, exit_date: date) -> Employee:
    employee = get_employee(db, employee_id)
    if employee.status == EmployeeStatus.exited:
        raise AlreadyExited()
    if exit_date < employee.join_date or exit_date > date.today():
        raise BadExitDate()

    before = {"status": employee.status, "exit_date": employee.exit_date, "login_active": employee.user.is_active}
    employee.status = EmployeeStatus.exited
    employee.exit_date = exit_date
    employee.user.is_active = False  # the login stops working immediately
    audit.record(
        db, "employee.exit", "employee", employee.id,
        f"Marked {employee.full_name} ({employee.employee_code}) as exited on {exit_date.isoformat()}",
        before=before,
        after={"status": employee.status, "exit_date": exit_date, "login_active": False},
    )
    db.commit()
    db.refresh(employee)
    return employee


def update_self(db: Session, user: User, phone: str | None, address: str | None) -> Employee:
    employee = get_employee_by_user(db, user.id)
    before = {"phone": employee.phone, "address": employee.address}
    employee.phone = phone
    employee.address = address
    audit.record_update(
        db, "employee", employee.id, f"{employee.full_name} updated their contact details",
        before, {"phone": phone, "address": address},
        action="employee.self_update",
    )
    db.commit()
    db.refresh(employee)
    return employee