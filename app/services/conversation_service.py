"""Conversation service — CRUD and message management."""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.conversation import Conversation, Message


def create_conversation(db: Session, created_by: str, title: str | None = None) -> Conversation:
    conv = Conversation(
        id=str(uuid.uuid4()),
        title=title or "New conversation",
        created_by=created_by,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def get_conversations_for_user(
    db: Session, user_id: str, skip: int = 0, limit: int = 100
) -> list[Conversation]:
    return (
        db.query(Conversation)
        .filter(Conversation.created_by == user_id)
        .order_by(Conversation.updated_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_conversation_by_id(db: Session, conv_id: str) -> Conversation | None:
    return db.query(Conversation).filter(Conversation.id == conv_id).first()


def delete_conversation(db: Session, conv_id: str) -> bool:
    conv = get_conversation_by_id(db, conv_id)
    if conv is None:
        return False
    db.delete(conv)
    db.commit()
    return True


def get_messages(
    db: Session, conversation_id: str, skip: int = 0, limit: int = 200
) -> list[Message]:
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def count_messages(db: Session, conversation_id: str) -> int:
    """返回对话中的消息总数（用于摘要触发判断，不受分页 limit 截断）。"""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .count()
    )


def get_recent_messages(
    db: Session, conversation_id: str, limit: int = 100
) -> list[Message]:
    """返回最近 N 条消息，已反转为时间正序（DESC 取头 + 反转，保证不丢最新消息）。

    注意与 get_messages 的区别：get_messages(ASC + LIMIT) 取的是【最早】N 条，
    消息超过 N 条后会丢失最新上下文；本函数专用于构建 LLM 对话历史。
    """
    return list(
        reversed(
            db.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )
    )


def add_message(
    db: Session,
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    free_chat: bool = False,
) -> Message:
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role=role,
        content=content,
        sources=json.dumps(sources, ensure_ascii=False) if sources else None,
        free_chat=1 if free_chat else 0,
    )
    db.add(msg)

    # Update conversation timestamp
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if conv:
        conv.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(msg)
    return msg


def update_summary(db: Session, conversation_id: str, summary: str) -> None:
    """Store or update the compressed summary on a conversation."""
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if conv:
        conv.summary = summary
        db.commit()
