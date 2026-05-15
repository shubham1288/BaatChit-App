from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from jose import jwt

from app.core.config import settings

_ph = PasswordHasher()


# ─────────────────────────────────────────────
# PASSWORD HASHING
# ─────────────────────────────────────────────

def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except VerifyMismatchError:
        return False


# ─────────────────────────────────────────────
# ACCESS TOKEN
# ─────────────────────────────────────────────

def create_access_token(data: dict) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload.update({"exp": expire, "type": "access"})
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> str | None:
    """Returns username (sub) or None if invalid."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        if payload.get("type") != "access":
            return None
        return payload.get("sub")
    except Exception:
        return None


# ─────────────────────────────────────────────
# REFRESH TOKEN
# ─────────────────────────────────────────────

def create_refresh_token(data: dict) -> str:
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    payload.update({"exp": expire, "type": "refresh"})
    return jwt.encode(
        payload, settings.refresh_secret_key, algorithm=settings.algorithm
    )


def decode_refresh_token(token: str) -> str | None:
    """Returns username (sub) or None if invalid."""
    try:
        payload = jwt.decode(
            token,
            settings.refresh_secret_key,
            algorithms=[settings.algorithm],
        )
        if payload.get("type") != "refresh":
            return None
        return payload.get("sub")
    except Exception:
        return None