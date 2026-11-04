from sqlalchemy.orm import Session

from app.core.security import generate_temp_password, hash_password
from app.models.user import Role, User


class EmailTaken(Exception):
    pass


class UserNotFound(Exception):
    pass


class ProtectedAccount(Exception):
    pass


def email_exists(db: Session, email: str) -> bool:
    return db.query(User).filter(User.email == email.lower()).first() is not None


def add_login(db: Session, email: str, role: Role) -> tuple[User, str]:
    """Create a user with a temporary password (not committed yet).
    Phase 3 uses this too, so registering an employee and creating the login can be one transaction."""
    if email_exists(db, email):
        raise EmailTaken()
    temp_password = generate_temp_password()
    user = User(
        email=email.lower(),
        password_hash=hash_password(temp_password),
        role=role,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    db.flush()  # gives the user an id without committing
    return user, temp_password


def create_hr_user(db: Session, email: str) -> tuple[User, str]:
    user, temp_password = add_login(db, email, Role.hr)
    db.commit()
    db.refresh(user)
    return user, temp_password


def list_users(
    db: Session,
    role: Role | None = None,
    is_active: bool | None = None,
    search: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    query = db.query(User)
    if role is not None:
        query = query.filter(User.role == role)
    if is_active is not None:
        query = query.filter(User.is_active.is_(is_active))
    if search and search.strip():
        query = query.filter(User.email.ilike(f"%{search.strip()}%"))

    total = query.count()
    items = query.order_by(User.id).limit(limit).offset(offset).all()
    return items, total


def _get_manageable_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise UserNotFound()
    if user.role == Role.admin:
        raise ProtectedAccount()
    return user


def set_active(db: Session, user_id: int, active: bool) -> User:
    user = _get_manageable_user(db, user_id)
    user.is_active = active
    db.commit()
    db.refresh(user)
    return user


def reset_password(db: Session, user_id: int) -> tuple[User, str]:
    user = _get_manageable_user(db, user_id)
    temp_password = generate_temp_password()
    user.password_hash = hash_password(temp_password)
    user.must_change_password = True
    db.commit()
    db.refresh(user)
    return user, temp_password