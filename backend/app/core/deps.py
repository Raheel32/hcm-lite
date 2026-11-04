from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.models.user import User, Role
from app.core.security import decode_access_token
from app.database import get_db
from app.models.user import User

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_token_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Valid token + active user. Used by routes that must work even before the password is changed."""
    if credentials is None:
        raise _unauthorized()
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise _unauthorized("Invalid or expired token")
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise _unauthorized("Invalid token")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("User not found or deactivated")
    return user


def get_current_user(user: User = Depends(get_token_user)) -> User:
    """The normal guard: blocks users who still have a temporary password."""
    if user.must_change_password:
        raise HTTPException(status_code=403, detail="Password change required")
    return user