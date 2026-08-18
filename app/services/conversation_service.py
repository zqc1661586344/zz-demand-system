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


def add_message(
    db: Session,
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
) -> Message:
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role=role,
        content=content,
        sources=json.dumps(sources, ensure_ascii=False) if sources else None,
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
