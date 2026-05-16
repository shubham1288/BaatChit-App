from sqlalchemy.orm import Session

from app.db.models import Group, GroupMember, GroupMessage, User, GroupMessageStatus, MessageStatus
from app.core.crypto import encrypt_content, decrypt_content



# ─────────────────────────────────────────────
# GROUP CRUD
# ─────────────────────────────────────────────

def create_group(db: Session, name: str, admin_id: int) -> Group:
    group = Group(name=name, admin_id=admin_id)
    db.add(group)
    db.flush()  # Get ID before committing

    # Add creator as admin member
    member = GroupMember(group_id=group.id, user_id=admin_id, is_admin=True)
    db.add(member)
    db.commit()
    db.refresh(group)
    return group


def get_group(db: Session, group_id: int) -> Group | None:
    return db.query(Group).filter(Group.id == group_id).first()


def get_user_groups(db: Session, user_id: int) -> list[Group]:
    return (
        db.query(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .filter(GroupMember.user_id == user_id)
        .order_by(Group.created_at.desc())
        .all()
    )


# ─────────────────────────────────────────────
# MEMBER MANAGEMENT
# ─────────────────────────────────────────────

def is_member(db: Session, group_id: int, user_id: int) -> bool:
    return (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        .first()
        is not None
    )


def is_admin(db: Session, group_id: int, user_id: int) -> bool:
    m = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        .first()
    )
    return bool(m and m.is_admin)


def add_member(db: Session, group_id: int, user_id: int) -> GroupMember | None:
    if is_member(db, group_id, user_id):
        return None
    member = GroupMember(group_id=group_id, user_id=user_id, is_admin=False)
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


def remove_member(db: Session, group_id: int, user_id: int) -> bool:
    m = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
        .first()
    )
    if not m:
        return False
    db.delete(m)
    db.commit()
    return True


def get_group_members(db: Session, group_id: int) -> list[dict]:
    members = (
        db.query(GroupMember)
        .filter(GroupMember.group_id == group_id)
        .all()
    )
    result = []
    for m in members:
        user = db.query(User).filter(User.id == m.user_id).first()
        result.append(
            {
                "user_id": m.user_id,
                "username": user.username if user else None,
                "is_admin": m.is_admin,
                "joined_at": m.joined_at.isoformat(),
            }
        )
    return result


def get_member_ids(db: Session, group_id: int) -> list[int]:
    return [
        m.user_id
        for m in db.query(GroupMember.user_id)
        .filter(GroupMember.group_id == group_id)
        .all()
    ]


# ─────────────────────────────────────────────
# GROUP MESSAGING
# ─────────────────────────────────────────────

def send_group_message(
    db: Session, group_id: int, sender_id: int, content: str, reply_to_id: int = None
) -> GroupMessage:
    msg = GroupMessage(
        group_id=group_id,
        sender_id=sender_id,
        content=encrypt_content(content),
        reply_to_id=reply_to_id,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


def update_group_message(
    db: Session, message_id: int, content: str
) -> GroupMessage | None:
    msg = db.query(GroupMessage).filter(GroupMessage.id == message_id).first()
    if not msg or msg.is_deleted:
        return None
    msg.content = encrypt_content(content)
    msg.is_edited = True
    db.commit()
    db.refresh(msg)
    return msg


def delete_group_message(db: Session, message_id: int) -> GroupMessage | None:
    msg = db.query(GroupMessage).filter(GroupMessage.id == message_id).first()
    if not msg:
        return None
    msg.is_deleted = True
    msg.content = encrypt_content("This message was deleted")
    db.commit()
    db.refresh(msg)
    return msg


def get_group_messages(
    db: Session, group_id: int, skip: int = 0, limit: int = 50
) -> tuple[list[GroupMessage], int]:
    base = db.query(GroupMessage).filter(GroupMessage.group_id == group_id)
    total = base.count()
    msgs = base.order_by(GroupMessage.created_at.desc()).offset(skip).limit(limit).all()
    
    # Decrypt messages
    for m in msgs:
        m.content = decrypt_content(m.content)
        if m.reply_to:
            m.reply_to.content = decrypt_content(m.reply_to.content)
        # Add aggregate status
        m.status = get_group_message_aggregate_status(db, m.id)
            
    return msgs, total


def mark_group_message_delivered(db: Session, message_id: int, user_id: int):
    status = db.query(GroupMessageStatus).filter(
        GroupMessageStatus.message_id == message_id,
        GroupMessageStatus.user_id == user_id
    ).first()
    if not status:
        status = GroupMessageStatus(message_id=message_id, user_id=user_id, status=MessageStatus.delivered)
        db.add(status)
        db.commit()
    elif status.status == MessageStatus.sent:
        status.status = MessageStatus.delivered
        db.commit()


def mark_group_message_read(db: Session, message_id: int, user_id: int):
    status = db.query(GroupMessageStatus).filter(
        GroupMessageStatus.message_id == message_id,
        GroupMessageStatus.user_id == user_id
    ).first()
    if not status:
        status = GroupMessageStatus(message_id=message_id, user_id=user_id, status=MessageStatus.read)
        db.add(status)
        db.commit()
    elif status.status != MessageStatus.read:
        status.status = MessageStatus.read
        db.commit()


def get_group_message_aggregate_status(db: Session, message_id: int) -> MessageStatus:
    msg = db.query(GroupMessage).filter(GroupMessage.id == message_id).first()
    if not msg:
        return MessageStatus.sent
    
    member_ids = get_member_ids(db, msg.group_id)
    # Exclude sender from aggregate status check
    other_member_ids = [uid for uid in member_ids if uid != msg.sender_id]
    
    if not other_member_ids:
        return MessageStatus.read

    statuses = db.query(GroupMessageStatus).filter(
        GroupMessageStatus.message_id == message_id,
        GroupMessageStatus.user_id.in_(other_member_ids)
    ).all()
    
    status_map = {s.user_id: s.status for s in statuses}
    
    all_read = True
    all_delivered = True
    
    for uid in other_member_ids:
        s = status_map.get(uid)
        if s != MessageStatus.read:
            all_read = False
        if s not in [MessageStatus.delivered, MessageStatus.read]:
            all_delivered = False
            
    if all_read:
        return MessageStatus.read
    if all_delivered:
        return MessageStatus.delivered
    return MessageStatus.sent



def delete_group(db: Session, group_id: int) -> bool:
    group = db.query(Group).filter(Group.id == group_id).first()
    if not group:
        return False
    db.delete(group)
    db.commit()
    return True


def clear_group_messages(db: Session, group_id: int):
    db.query(GroupMessage).filter(GroupMessage.group_id == group_id).delete()
    db.commit()
