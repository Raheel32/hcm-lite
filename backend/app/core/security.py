from datetime import datetime, timedelta, timezone

import secrets
import bcrypt
import jwt

from app.config import settings

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60


def hash_password(password: str) -> str:
    """Turn a plain password into a one-way hash for the database."""
    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a login attempt against the stored hash."""
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_access_token(user_id: int, role: str) -> str:
    """Create a signed login token that expires after 60 minutes."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "role": role, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str):
    """Return the token's data, or None if it is fake, expired, or tampered with."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None

# No look-alike characters (0/O, 1/l/I) so the password is easy to read out loud
_TEMP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def generate_temp_password(length: int = 12) -> str:
    """Random password with at least one uppercase letter, one lowercase letter and one digit."""
    while True:
        password = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))
        if (
            any(c.isupper() for c in password)
            and any(c.islower() for c in password)
            and any(c.isdigit() for c in password)
        ):
            return password