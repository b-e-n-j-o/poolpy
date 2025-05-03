from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

class ChatRequest(BaseModel):
    phone_number: str
    message: str

class ChatResponse(BaseModel):
    response: str

class Message(BaseModel):
    id: str
    user_id: str
    phone_number: str
    content: str
    direction: str
    message_type: str
    metadata: Dict[str, Any]
    created_at: datetime

class Session(BaseModel):
    id: str
    user_id: str
    phone_number: str
    start_time: datetime
    last_activity: datetime
    status: str
    metadata: Dict
    end_time: Optional[datetime] = None

class MessageCreate(BaseModel):
    user_id: str
    phone_number: str
    content: str
    direction: str
    message_type: str
    metadata: Dict[str, Any]

class MessageRead(Message):
    pass 