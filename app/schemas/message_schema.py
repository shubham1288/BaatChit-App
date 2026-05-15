from datetime import datetime

from pydantic import BaseModel

#define model for incoming websocket message from client to backend
class MessageCreate(BaseModel):

    content:str

#define mode for outgoing message response
class MessageResponse(BaseModel):

    username:str
    content:str
    created_at:datetime

    #convert sqlalchemy object directly into the schema
    class Config:

        from_attributes = True
