from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.company import Company
from app.models.user import User, Role
from app.schemas.setup import SetupRequest


class SetupAlreadyDone(Exception):
    pass


def is_initialized(db: Session) -> bool:
    return db.query(Company).first() is not None


def run_first_setup(db: Session, data: SetupRequest) -> Company:
    if is_initialized(db):
        raise SetupAlreadyDone()

    company = Company(name=data.company_name.strip())
    admin = User(
        email=data.admin_email.lower(),
        password_hash=hash_password(data.admin_password),
        role=Role.admin,
        is_active=True,
        must_change_password=False,
    )
    db.add(company)
    db.add(admin)
    db.commit()
    db.refresh(company)
    return company