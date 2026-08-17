"""Conversation API routes — CRUD and RAG query."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.rag.chain import generate_summary, query_rag
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    MessageResponse,
    QueryRequest,
    QueryResponse,
)
from app.services.conversation_service import (
    add_message,
    create_conversation,
    delete_conversation,
    get_conversation_by_id,
    get_conversations_for_user,
    get_messages,
    update_summary,
)

# How many assistant+user message pairs = 1 round to keep in recent window
RECENT_ROUNDS = 5
SUMMARY_INTERVAL = RECENT_ROUNDS * 2  # 10 messages = every 5 rounds

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.post("", response_model=ConversationResponse, status_code=201)
def new_conversation(
    req: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = create_conversation(db, title=req.title, created_by=current_user.id)
    return conv


@router.get("", response_model=list[ConversationResponse])
def list_conversations(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    convs = get_conversations_for_user(db, current_user.id, skip=skip, limit=limit)
    result = []
    for conv in convs:
        result.append(ConversationResponse(
            id=conv.id,
            title=conv.title,
            created_by=conv.created_by,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            message_count=len(conv.messages) if hasattr(conv, "messages") else 0,
        ))
    return result


@router.get("/{conv_id}", response_model=ConversationResponse)
def get_conversation(
    conv_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = get_conversation_by_id(db, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.created_by != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    return ConversationResponse(
        id=conv.id,
        title=conv.title,
        created_by=conv.created_by,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        message_count=len(conv.messages) if hasattr(conv, "messages") else 0,
    )


@router.delete("/{conv_id}")
def remove_conversation(
    conv_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = get_conversation_by_id(db, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.created_by != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    if not delete_conversation(db, conv_id):
        raise HTTPException(status_code=500, detail="Failed to delete conversation")
    return {"message": "Conversation deleted successfully"}


@router.get("/{conv_id}/messages", response_model=list[MessageResponse])
def list_messages(
    conv_id: str,
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = get_conversation_by_id(db, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.created_by != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")
    return get_messages(db, conv_id, skip=skip, limit=limit)


@router.post("/{conv_id}/query", response_model=QueryResponse)
def query_conversation(
    conv_id: str,
    req: QueryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = get_conversation_by_id(db, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.created_by != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Access denied")

    # Build history: summary for old rounds + recent messages
    prior_msgs = get_messages(db, conv_id, limit=100)
    total = len(prior_msgs)

    if total > RECENT_ROUNDS * 2:
        # We have a summary — only keep the last RECENT_ROUNDS rounds
        recent_raw = prior_msgs[-(RECENT_ROUNDS * 2):]
        recent_history = [
            {"role": m.role, "content": m.content}
            for m in recent_raw
        ]
        history = recent_history
        summary = conv.summary
    else:
        # Not enough messages to need summarization — use all
        history = [
            {"role": m.role, "content": m.content}
            for m in prior_msgs
        ]
        summary = None

    # Always run RAG against the single document collection
    try:
        result = query_rag(query=req.query, top_k=req.top_k, history=history, summary=summary)
        answer = result["answer"]
        sources = result["sources"]
    except Exception as e:
        answer = f"RAG query failed: {str(e)}"
        sources = []

    # Save user message
    add_message(db, conv_id, role="user", content=req.query)
    # Save assistant message
    add_message(db, conv_id, role="assistant", content=answer, sources=sources)

    # Trigger summary regeneration every SUMMARY_INTERVAL messages
    # (total+2 because we just added 2 messages: user + assistant)
    if (total + 2) >= SUMMARY_INTERVAL and (total + 2) % SUMMARY_INTERVAL == 0:
        try:
            all_msgs = get_messages(db, conv_id, limit=100)
            history_all = [
                {"role": m.role, "content": m.content}
                for m in all_msgs
            ]
            new_summary = generate_summary(history_all)
            update_summary(db, conv_id, new_summary)
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning("Failed to generate conversation summary: %s", e)

    return QueryResponse(answer=answer, sources=sources)