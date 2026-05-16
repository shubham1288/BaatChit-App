from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.core.rate_limiter import message_rate_limiter
from app.db.database import get_db
from app.db.models import User, GroupMember
from app.schemas.group_schema import (
    AddMemberRequest,
    GroupCreate,
    GroupMessageCreate,
    GroupMessageOut,
    GroupMessageUpdate,
    GroupOut,
    PaginatedGroupMessages,
)
from app.services.group_service import (
    add_member,
    clear_group_messages,
    create_group,
    delete_group,
    get_group,
    get_group_members,
    get_group_messages,
    get_user_groups,
    is_admin,
    is_member,
    remove_member,
    send_group_message,
    update_group_message,
    delete_group_message,
)
from app.services.user_service import get_user_by_username

router = APIRouter(prefix="/groups", tags=["Groups"])


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_new_group(
    payload: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_group(db, payload.name, current_user.id)


@router.get("", response_model=list[GroupOut])
def my_groups(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    groups = get_user_groups(db, current_user.id)
    out = []
    for g in groups:
        # Find the specific membership record for current user to get their key
        membership = db.query(GroupMember).filter(
            GroupMember.group_id == g.id, 
            GroupMember.user_id == current_user.id
        ).first()
        
        g_out = GroupOut(
            id=g.id,
            name=g.name,
            admin_id=g.admin_id,
            created_at=g.created_at
        )
        out.append(g_out)
    return out


@router.get("/{group_id}", response_model=GroupOut)
def group_detail(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    group = get_group(db, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    if not is_member(db, group_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")
    return group


@router.get("/{group_id}/members")
def group_members(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_member(db, group_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")
    return get_group_members(db, group_id)


@router.post("/{group_id}/members", status_code=status.HTTP_201_CREATED)
def add_group_member(
    group_id: int,
    payload: AddMemberRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_admin(db, group_id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    target = get_user_by_username(db, payload.username)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    result = add_member(db, group_id, target.id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="User is already a member"
        )
    return {"detail": f"{target.username} added to group"}


@router.delete("/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_group_member(
    group_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_admin(db, group_id, current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Admin cannot remove themselves"
        )
    removed = remove_member(db, group_id, user_id)
    if not removed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")


@router.get("/{group_id}/messages", response_model=PaginatedGroupMessages)
def group_chat_history(
    group_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_member(db, group_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")

    from app.core.crypto import decrypt_content
    from app.services.group_service import get_group_message_aggregate_status
    out = [
        GroupMessageOut(
            id=m.id,
            group_id=m.group_id,
            sender_id=m.sender_id,
            sender_username=m.sender.username if m.sender else None,
            content=decrypt_content(m.content),
            created_at=m.created_at,
            reply_to_id=m.reply_to_id,
            reply_content=decrypt_content(m.reply_to.content) if m.reply_to else None,
            reply_sender=m.reply_to.sender.username if m.reply_to and m.reply_to.sender else None,
            is_edited=m.is_edited,
            is_deleted=m.is_deleted,
            status=get_group_message_aggregate_status(db, m.id)
        )
        for m in msgs
    ]
    return PaginatedGroupMessages(messages=out, total=total, skip=skip, limit=limit)




@router.post(
    "/{group_id}/messages",
    response_model=GroupMessageOut,
    status_code=status.HTTP_201_CREATED,
)
def send_group_msg(
    group_id: int,
    payload: GroupMessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """REST fallback for group messages."""
    message_rate_limiter.check(current_user.username)
    if not is_member(db, group_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")
    msg = send_group_message(db, group_id, current_user.id, payload.content, payload.reply_to_id)
    from app.core.crypto import decrypt_content
    return GroupMessageOut(
        id=msg.id,
        group_id=msg.group_id,
        sender_id=msg.sender_id,
        sender_username=current_user.username,
        content=decrypt_content(msg.content),
        created_at=msg.created_at,
        reply_to_id=msg.reply_to_id,
        reply_content=decrypt_content(msg.reply_to.content) if msg.reply_to else None,
        reply_sender=msg.reply_to.sender.username if msg.reply_to and msg.reply_to.sender else None,
        is_edited=msg.is_edited,
        is_deleted=msg.is_deleted,
    )


@router.post("/{group_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_member(db, group_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not a member")
    
    # Check if they are the only admin? For now just allow leaving.
    # If the app requires at least one admin, we might need logic here.
    remove_member(db, group_id, current_user.id)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def resolve_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_admin(db, group_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    
    delete_group(db, group_id)


@router.delete("/{group_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
def delete_group_chat(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not is_member(db, group_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member")
    
    clear_group_messages(db, group_id)


@router.patch("/messages/{message_id}", response_model=GroupMessageOut)
def edit_group_msg(
    message_id: int,
    payload: GroupMessageUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit a group message. Only the sender can do this."""
    from app.db.models import GroupMessage
    msg = db.query(GroupMessage).filter(GroupMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the sender can edit")
    
    updated = update_group_message(db, message_id, payload.content)
    return GroupMessageOut.from_orm(updated)


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group_msg(
    message_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a group message. Only the sender can do this."""
    from app.db.models import GroupMessage
    msg = db.query(GroupMessage).filter(GroupMessage.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    if msg.sender_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the sender can delete")
    
    delete_group_message(db, message_id)

