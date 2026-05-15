from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.rate_limiter import message_rate_limiter
from app.db.database import get_db
from app.db.models import User
from app.schemas.private_message_schema import (
    PaginatedMessages,
    PrivateMessageCreate,
    PrivateMessageOut,
    PrivateMessageUpdate,
)
from app.services.private_chat_service import (
    create_private_message,
    delete_private_conversation,
    fetch_conversation,
    get_user_conversations,
    mark_read,
    update_private_message,
    delete_private_message,
)
from app.services.user_service import get_user_by_username

router = APIRouter(prefix="/conversations", tags=["Private Chat"])


@router.get("", response_model=list[dict])
def list_conversations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return all private conversations with last message info."""
    return get_user_conversations(db, current_user.id)


@router.get("/{username}", response_model=PaginatedMessages)
def conversation_history(
    username: str,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    other = get_user_by_username(db, username)
    if not other:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    messages, total = fetch_conversation(db, current_user.id, other.id, skip, limit)

    out = []
    for m in messages:
        out.append(
            PrivateMessageOut(
                id=m.id,
                sender_id=m.sender_id,
                receiver_id=m.receiver_id,
                sender_username=m.sender.username if m.sender else None,
                receiver_username=m.receiver.username if m.receiver else None,
                content=m.content,
                status=m.status,
                created_at=m.created_at,
                reply_to_id=m.reply_to_id,
                reply_content=m.reply_to.content if m.reply_to else None,
                reply_sender=m.reply_to.sender.username if m.reply_to and m.reply_to.sender else None,
                is_edited=m.is_edited,
                is_deleted=m.is_deleted,
            )
        )
    return PaginatedMessages(messages=out, total=total, skip=skip, limit=limit)


@router.post("/send", response_model=PrivateMessageOut, status_code=status.HTTP_201_CREATED)
def send_message(
    payload: PrivateMessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """REST fallback for sending private messages when WebSocket is unavailable."""
    message_rate_limiter.check(current_user.username)

    receiver = get_user_by_username(db, payload.receiver)
    if not receiver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Receiver not found")

    if receiver.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot message yourself"
        )

    msg = create_private_message(db, current_user.id, receiver.id, payload.content, payload.reply_to_id)
    from app.core.crypto import decrypt_content
    return PrivateMessageOut(
        id=msg.id,
        sender_id=msg.sender_id,
        receiver_id=msg.receiver_id,
        sender_username=current_user.username,
        receiver_username=receiver.username,
        content=decrypt_content(msg.content),
        status=msg.status,
        created_at=msg.created_at,
        reply_to_id=msg.reply_to_id,
        reply_content=decrypt_content(msg.reply_to.content) if msg.reply_to else None,
        reply_sender=msg.reply_to.sender.username if msg.reply_to and msg.reply_to.sender else None,
        is_edited=msg.is_edited,
        is_deleted=msg.is_deleted,
    )


@router.patch("/{message_id}/read", response_model=PrivateMessageOut)
def mark_message_read(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a message as read. Only the receiver can do this."""
    from app.db.models import PrivateMessage

    msg = db.query(PrivateMessage).filter(PrivateMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    if msg.receiver_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the receiver can mark as read",
        )
    updated = mark_read(db, message_id)
    return PrivateMessageOut(
        id=updated.id,
        sender_id=updated.sender_id,
        receiver_id=updated.receiver_id,
        sender_username=updated.sender.username if updated.sender else None,
        receiver_username=updated.receiver.username if updated.receiver else None,
        content=updated.content,
        status=updated.status,
        created_at=updated.created_at,
        is_edited=updated.is_edited,
        is_deleted=updated.is_deleted,
    )


@router.delete("/{username}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(
    username: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    other = get_user_by_username(db, username)
    if not other:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    
    delete_private_conversation(db, current_user.id, other.id)


@router.patch("/messages/{message_id}", response_model=PrivateMessageOut)
def edit_message(
    message_id: int,
    payload: PrivateMessageUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit a private message. Only the sender can do this."""
    from app.db.models import PrivateMessage
    msg = db.query(PrivateMessage).filter(PrivateMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the sender can edit")
    
    updated = update_private_message(db, message_id, payload.content)
    return PrivateMessageOut.from_orm(updated)


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_msg(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a private message. Only the sender can do this."""
    from app.db.models import PrivateMessage
    msg = db.query(PrivateMessage).filter(PrivateMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the sender can delete")
    
    delete_private_message(db, message_id)

