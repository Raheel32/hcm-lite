from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.services import audit_service as audit


class InvalidCredentials(Exception):
    pass


class AccountDisabled(Exception):
    pass


class WrongCurrentPassword(Exception):
    pass


class SamePassword(Exception):
    pass


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.query(User).filter(User.email == email.lower()).first()
    # Same error for "no such email" and "wrong password" so attackers can't tell which emails exist
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials()
    if not user.is_active:
        raise AccountDisabled()
    return user


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise WrongCurrentPassword()
    if current_password == new_password:
        raise SamePassword()
    #user.password_hash = hash_password(new_password)
    #user.must_change_password = False
    #db.commit()
    was_forced = user.must_change_password
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    audit.record(
        db, "auth.password_changed", "user", user.id,
        f"{user.email} changed their password",
        before={"must_change_password": was_forced},
        after={"must_change_password": False},
    )
    db.commit()