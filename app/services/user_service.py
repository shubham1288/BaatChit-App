from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func

from app.db.models import User


def get_users(
    db: Session, current_username: str | None = None, search: str | None = None, limit: int = 100
) -> list[User]:
    limit = min(limit, 500)
    query = db.query(User).filter(User.is_active == True)
    if current_username:
        query = query.filter(User.username != current_username.lower())
    if search:
        query = query.filter(User.username.ilike(f"%{search}%"))
    return query.order_by(User.username.asc()).limit(limit).all()


def get_user_by_username(db: Session, username: str) -> User | None:
    if not username:
        return None
    try:
        return (
            db.query(User)
            .filter(func.lower(User.username) == username.lower())
            .first()
        )
    except SQLAlchemyError:
        return None


def get_user_by_id(db: Session, user_id: int) -> User | None:
    try:
        return db.query(User).filter(User.id == user_id).first()
    except SQLAlchemyError:
        return None