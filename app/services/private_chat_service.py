from sqlalchemy import func, or_, case
from sqlalchemy.orm import Session

from app.db.models import MessageStatus, PrivateMessage, User
from app.core.crypto import encrypt_content, decrypt_content


def create_private_message(
    db: Session, sender_id: int, receiver_id: int, content: str, reply_to_id: int = None
) -> PrivateMessage:
    msg = PrivateMessage(
        sender_id=sender_id,
        receiver_id=receiver_id,
        content=encrypt_content(content),
        status=MessageStatus.sent,
        reply_to_id=reply_to_id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def update_private_message(
    db: Session, message_id: int, content: str
) -> PrivateMessage | None:
    msg = db.query(PrivateMessage).filter(PrivateMessage.id == message_id).first()
    if not msg or msg.is_deleted:
        return None
    msg.content = encrypt_content(content)
    msg.is_edited = True
    db.commit()
    db.refresh(msg)
    return msg


def delete_private_message(db: Session, message_id: int) -> PrivateMessage | None:
    msg = db.query(PrivateMessage).filter(PrivateMessage.id == message_id).first()
    if not msg:
        return None
    msg.is_deleted = True
    msg.content = encrypt_content("This message was deleted")
    db.commit()
    db.refresh(msg)
    return msg


def fetch_conversation(
    db: Session,
    user1_id: int,
    user2_id: int,
    skip: int = 0,
    limit: int = 50,
) -> tuple[list[PrivateMessage], int]:
    base = db.query(PrivateMessage).filter(
        or_(
            (PrivateMessage.sender_id == user1_id)
            & (PrivateMessage.receiver_id == user2_id),
            (PrivateMessage.sender_id == user2_id)
            & (PrivateMessage.receiver_id == user1_id),
        )
    )
    total = base.count()
    messages = (
        base.order_by(PrivateMessage.created_at.desc()).offset(skip).limit(limit).all()
    )
    
    # Decrypt messages
    for m in messages:
        m.content = decrypt_content(m.content)
        if m.reply_to:
            m.reply_to.content = decrypt_content(m.reply_to.content)
            
    return messages, total


def mark_delivered(db: Session, message_id: int) -> PrivateMessage | None:
    msg = db.query(PrivateMessage).filter(PrivateMessage.id == message_id).first()
    if msg and msg.status == MessageStatus.sent:
        msg.status = MessageStatus.delivered
        db.commit()
        db.refresh(msg)
    return msg


def mark_read(db: Session, message_id: int) -> PrivateMessage | None:
    msg = db.query(PrivateMessage).filter(PrivateMessage.id == message_id).first()
    if msg and msg.status != MessageStatus.read:
        msg.status = MessageStatus.read
        db.commit()
        db.refresh(msg)
    return msg


def get_user_conversations(db: Session, user_id: int) -> list[dict]:
    """Return list of conversations with last message and partner username."""
    subq = (
        db.query(
            func.max(PrivateMessage.id).label("last_id"),
        )
        .filter(
            or_(
                PrivateMessage.sender_id == user_id,
                PrivateMessage.receiver_id == user_id,
            )
        )
        .group_by(
            case(
                (PrivateMessage.sender_id == user_id, PrivateMessage.receiver_id),
                else_=PrivateMessage.sender_id,
            )
        )
        .subquery()
    )

    msgs = (
        db.query(PrivateMessage)
        .filter(PrivateMessage.id.in_(db.query(subq.c.last_id)))
        .order_by(PrivateMessage.created_at.desc())
        .all()
    )

    results = []
    for m in msgs:
        partner_id = m.receiver_id if m.sender_id == user_id else m.sender_id
        partner = db.query(User).filter(User.id == partner_id).first()
        results.append(
            {
                "partner_id": partner_id,
                "partner_username": partner.username if partner else None,
                "last_message": decrypt_content(m.content),
                "last_message_at": m.created_at.isoformat(),
                "status": m.status.value,
            }
        )
    return results


def delete_private_conversation(db: Session, user1_id: int, user2_id: int):
    deleted_count = db.query(PrivateMessage).filter(
        or_(
            (PrivateMessage.sender_id == user1_id) & (PrivateMessage.receiver_id == user2_id),
            (PrivateMessage.sender_id == user2_id) & (PrivateMessage.receiver_id == user1_id),
        )
    ).delete(synchronize_session=False)
    db.commit()
    from app.main import logger
    logger.info("Deleted %d messages between users %d and %d", deleted_count, user1_id, user2_id)