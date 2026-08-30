"""Conversation and Message Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationResponse(BaseModel):
    id: str
    title: str | None = None
    created_by: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    sources: str | None = None
    free_chat: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(5, ge=1, le=20)


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict] = []
    free_chat: bool = False  # True = 本轮是自由聊天（前端据此渲染"找不到答案"提示语）
