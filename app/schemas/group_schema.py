from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class GroupOut(BaseModel):
    id: int
    name: str
    admin_id: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}


class GroupMemberOut(BaseModel):
    user_id: int
    username: Optional[str] = None
    is_admin: bool
    joined_at: datetime

    model_config = {"from_attributes": True}


class AddMemberRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)


class GroupMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)
    reply_to_id: Optional[int] = None


class GroupMessageUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=5000)


class GroupMessageOut(BaseModel):
    id: int
    group_id: int
    sender_id: int
    sender_username: Optional[str] = None
    content: str
    created_at: datetime
    reply_to_id: Optional[int] = None
    reply_content: Optional[str] = None
    reply_sender: Optional[str] = None
    is_edited: bool = False
    is_deleted: bool = False

    model_config = {"from_attributes": True}


class PaginatedGroupMessages(BaseModel):
    messages: list[GroupMessageOut]
    total: int
    skip: int
    limit: int
