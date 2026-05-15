from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.db.models import MessageStatus


class PrivateMessageCreate(BaseModel):
    receiver: str = Field(min_length=1, max_length=50)
    content: str = Field(min_length=1, max_length=5000)
    reply_to_id: Optional[int] = None
    is_edited: Optional[bool] = False
    is_deleted: Optional[bool] = False


class PrivateMessageUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class PrivateMessageOut(BaseModel):
    id: int
    sender_id: int
    receiver_id: int
    sender_username: Optional[str] = None
    receiver_username: Optional[str] = None
    content: str
    status: MessageStatus
    created_at: datetime
    reply_to_id: Optional[int] = None
    reply_content: Optional[str] = None
    reply_sender: Optional[str] = None
    is_edited: bool = False
    is_deleted: bool = False

    model_config = {"from_attributes": True}


class PaginatedMessages(BaseModel):
    messages: list[PrivateMessageOut]
    total: int
    skip: int
    limit: int