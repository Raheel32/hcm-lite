from datetime import date as date_type

from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from app.models.leave_config import Holiday, LeaveType
from app.schemas.leave_config import HolidayCreate, LeaveTypeCreate, LeaveTypeUpdate


class DuplicateName(Exception):
    pass


class DuplicateDate(Exception):
    pass


class ItemNotFound(Exception):
    pass


# ---------- Leave types ----------

def _leave_name_taken(db: Session, name: str, exclude_id: int | None = None) -> bool:
    query = db.query(LeaveType).filter(func.lower(LeaveType.name) == name.lower())
    if exclude_id is not None:
        query = query.filter(LeaveType.id != exclude_id)
    return query.first() is not None


def list_leave_types(db: Session, include_inactive: bool = False):
    query = db.query(LeaveType)
    if not include_inactive:
        query = query.filter(LeaveType.is_active.is_(True))
    return query.order_by(func.lower(LeaveType.name)).all()


def create_leave_type(db: Session, data: LeaveTypeCreate) -> LeaveType:
    if _leave_name_taken(db, data.name):
        raise DuplicateName()
    leave_type = LeaveType(
        name=data.name,
        yearly_quota=data.yearly_quota,
        is_paid=data.is_paid,
        is_active=True,
    )
    db.add(leave_type)
    db.commit()
    db.refresh(leave_type)
    return leave_type


def update_leave_type(db: Session, leave_type_id: int, data: LeaveTypeUpdate) -> LeaveType:
    leave_type = db.get(LeaveType, leave_type_id)
    if leave_type is None:
        raise ItemNotFound()
    if _leave_name_taken(db, data.name, exclude_id=leave_type_id):
        raise DuplicateName()
    leave_type.name = data.name
    leave_type.yearly_quota = data.yearly_quota
    leave_type.is_paid = data.is_paid
    leave_type.is_active = data.is_active
    db.commit()
    db.refresh(leave_type)
    return leave_type


# ---------- Holidays ----------

def _date_taken(db: Session, day: date_type, exclude_id: int | None = None) -> bool:
    query = db.query(Holiday).filter(Holiday.date == day)
    if exclude_id is not None:
        query = query.filter(Holiday.id != exclude_id)
    return query.first() is not None


def list_holidays(db: Session, year: int | None = None):
    query = db.query(Holiday)
    if year is not None:
        query = query.filter(extract("year", Holiday.date) == year)
    return query.order_by(Holiday.date).all()


def create_holiday(db: Session, data: HolidayCreate) -> Holiday:
    if _date_taken(db, data.date):
        raise DuplicateDate()
    holiday = Holiday(date=data.date, name=data.name)
    db.add(holiday)
    db.commit()
    db.refresh(holiday)
    return holiday


def update_holiday(db: Session, holiday_id: int, data: HolidayCreate) -> Holiday:
    holiday = db.get(Holiday, holiday_id)
    if holiday is None:
        raise ItemNotFound()
    if _date_taken(db, data.date, exclude_id=holiday_id):
        raise DuplicateDate()
    holiday.date = data.date
    holiday.name = data.name
    db.commit()
    db.refresh(holiday)
    return holiday


def delete_holiday(db: Session, holiday_id: int) -> None:
    holiday = db.get(Holiday, holiday_id)
    if holiday is None:
        raise ItemNotFound()
    db.delete(holiday)
    db.commit()