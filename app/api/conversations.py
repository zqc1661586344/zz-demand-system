"""Conversation API routes — CRUD and RAG query."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.rag.chain import query_rag
from app.rag.embeddings import get_llm
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
)

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

    # Always run RAG against the single document collection
    try:
        result = query_rag(query=req.query, top_k=req.top_k)
        answer = result["answer"]
        sources = result["sources"]
    except Exception as e:
        answer = f"RAG query failed: {str(e)}"
        sources = []

    # Save user message
    add_message(db, conv_id, role="user", content=req.query)
    # Save assistant message
    add_message(db, conv_id, role="assistant", content=answer, sources=sources)

    return QueryResponse(answer=answer, sources=sources)