from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.company import Company
from app.models.user import User, Role
from app.schemas.setup import SetupRequest
from app.services import audit_service as audit


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
    db.flush()  # gives both an id
    audit.record(
        db, "setup.complete", "company", company.id,
        f"First-run setup: created company {company.name} and its Admin account",
        after={"company": company.name, "admin_email": admin.email},
        actor=admin,  # nobody is logged in yet, so the new Admin is the actor
    )
    db.commit()
    db.refresh(company)
    return company