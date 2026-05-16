import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import relationship

from app.db.database import Base


# ─────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────

class MessageStatus(str, enum.Enum):
    sent = "sent"
    delivered = "delivered"
    read = "read"


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────
# USER
# ─────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Relationships
    sent_messages = relationship(
        "PrivateMessage",
        foreign_keys="PrivateMessage.sender_id",
        back_populates="sender",
        cascade="all, delete",
    )
    received_messages = relationship(
        "PrivateMessage",
        foreign_keys="PrivateMessage.receiver_id",
        back_populates="receiver",
        cascade="all, delete",
    )
    group_memberships = relationship(
        "GroupMember", back_populates="user", cascade="all, delete"
    )
    group_messages = relationship(
        "GroupMessage", back_populates="sender", cascade="all, delete"
    )
    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete"
    )

    def __repr__(self) -> str:
        return f"<User {self.username}>"


# ─────────────────────────────────────────────
# PRIVATE MESSAGE
# ─────────────────────────────────────────────

class PrivateMessage(Base):
    __tablename__ = "private_messages"

    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    receiver_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content = Column(String(5000), nullable=False)
    status = Column(
        Enum(MessageStatus), default=MessageStatus.sent, nullable=False, index=True
    )
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    reply_to_id = Column(
        Integer, ForeignKey("private_messages.id", ondelete="SET NULL"), nullable=True
    )
    reactions = Column(JSON, nullable=True)
    is_edited = Column(Boolean, default=False, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)

    sender = relationship("User", foreign_keys=[sender_id], back_populates="sent_messages")
    receiver = relationship("User", foreign_keys=[receiver_id], back_populates="received_messages")
    reply_to = relationship("PrivateMessage", remote_side=[id])

    def __repr__(self) -> str:
        return f"<PrivateMessage {self.sender_id} → {self.receiver_id}>"


# ─────────────────────────────────────────────
# GROUP
# ─────────────────────────────────────────────

class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    admin_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    admin = relationship("User", foreign_keys=[admin_id])
    members = relationship("GroupMember", back_populates="group", cascade="all, delete")
    messages = relationship("GroupMessage", back_populates="group", cascade="all, delete")

    def __repr__(self) -> str:
        return f"<Group {self.name}>"


# ─────────────────────────────────────────────
# GROUP MEMBER
# ─────────────────────────────────────────────

class GroupMember(Base):
    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_admin = Column(Boolean, default=False, nullable=False)
    joined_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    group = relationship("Group", back_populates="members")
    user = relationship("User", back_populates="group_memberships")

    def __repr__(self) -> str:
        return f"<GroupMember group={self.group_id} user={self.user_id}>"


# ─────────────────────────────────────────────
# GROUP MESSAGE
# ─────────────────────────────────────────────

class GroupMessage(Base):
    __tablename__ = "group_messages"

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(
        Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sender_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content = Column(String(5000), nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    reply_to_id = Column(
        Integer, ForeignKey("group_messages.id", ondelete="SET NULL"), nullable=True
    )
    reactions = Column(JSON, nullable=True)
    is_edited = Column(Boolean, default=False, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)

    group = relationship("Group", back_populates="messages")
    sender = relationship("User", back_populates="group_messages")
    reply_to = relationship("GroupMessage", remote_side=[id])

    def __repr__(self) -> str:
        return f"<GroupMessage group={self.group_id} sender={self.sender_id}>"


# ─────────────────────────────────────────────
# GROUP MESSAGE STATUS
# ─────────────────────────────────────────────

class GroupMessageStatus(Base):
    __tablename__ = "group_message_statuses"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_group_msg_status"),)

    id = Column(Integer, primary_key=True, index=True)
    message_id = Column(
        Integer, ForeignKey("group_messages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = Column(
        Enum(MessageStatus), default=MessageStatus.sent, nullable=False, index=True
    )
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    message = relationship("GroupMessage", backref="statuses")
    user = relationship("User")

    def __repr__(self) -> str:
        return f"<GroupMessageStatus msg={self.message_id} user={self.user_id} status={self.status}>"



# ─────────────────────────────────────────────
# REFRESH TOKEN
# ─────────────────────────────────────────────

class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token = Column(String(512), nullable=False, unique=True, index=True)
    revoked = Column(Boolean, default=False, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    user = relationship("User", back_populates="refresh_tokens")

    def __repr__(self) -> str:
        return f"<RefreshToken user={self.user_id}>"