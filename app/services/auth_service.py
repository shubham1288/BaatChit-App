from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
)
from app.db.models import RefreshToken, User


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username.lower()).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, username: str, password: str) -> User | None:
    if get_user_by_username(db, username):
        return None
    user = User(
        username=username.lower(),
        hashed_password=hash_password(password)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user


def generate_tokens(username: str) -> tuple[str, str]:
    access = create_access_token({"sub": username})
    refresh = create_refresh_token({"sub": username})
    return access, refresh


def store_refresh_token(db: Session, user_id: int, token: str) -> RefreshToken:
    from app.core.config import settings
    from datetime import timedelta

    expires = datetime.now(timezone.utc) + timedelta(
        days=settings.refresh_token_expire_days
    )
    record = RefreshToken(user_id=user_id, token=token, expires_at=expires)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def revoke_refresh_token(db: Session, token: str) -> bool:
    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token == token, RefreshToken.revoked == False)
        .first()
    )
    if not record:
        return False
    record.revoked = True
    db.commit()
    return True


def get_valid_refresh_token(db: Session, token: str) -> RefreshToken | None:
    now = datetime.now(timezone.utc)
    return (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token == token,
            RefreshToken.revoked == False,
            RefreshToken.expires_at > now,
        )
        .first()
    )