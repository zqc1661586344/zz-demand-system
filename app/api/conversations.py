"""Conversation API routes — CRUD and RAG query."""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.rag.chain import generate_summary, query_rag, query_rag_stream
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


def _build_history(conv, db, conv_id):
    """Build history + summary from prior messages. Returns (history, summary, total)."""
    prior_msgs = get_messages(db, conv_id, limit=100)
    total = len(prior_msgs)

    if total > RECENT_ROUNDS * 2:
        recent_raw = prior_msgs[-(RECENT_ROUNDS * 2) :]
        history = [{"role": m.role, "content": m.content} for m in recent_raw]
        summary = conv.summary
    else:
        history = [{"role": m.role, "content": m.content} for m in prior_msgs]
        summary = None

    return history, summary, total


def _maybe_summarize(db, conv_id, total):
    """Trigger summary regeneration every SUMMARY_INTERVAL messages."""
    if (total + 2) >= SUMMARY_INTERVAL and (total + 2) % SUMMARY_INTERVAL == 0:
        try:
            all_msgs = get_messages(db, conv_id, limit=100)
            history_all = [{"role": m.role, "content": m.content} for m in all_msgs]
            new_summary = generate_summary(history_all)
            update_summary(db, conv_id, new_summary)
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning("Failed to generate conversation summary: %s", e)


@router.post("", response_model=ConversationResponse, status_code=201)
def new_conversation(
    req: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = create_conversation(db, title=req.title, created_by=current_user.id)  # type: ignore[assignment]
    return conv


@router.get("", response_model=list[ConversationResponse])
def list_conversations(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    convs = get_conversations_for_user(db, current_user.id, skip=skip, limit=limit)  # type: ignore[assignment]
    result = []
    for conv in convs:
        result.append(
            ConversationResponse(
                id=conv.id,  # type: ignore[assignment]
                title=conv.title,  # type: ignore[assignment]
                created_by=conv.created_by,  # type: ignore[assignment]
                created_at=conv.created_at,  # type: ignore[assignment]
                updated_at=conv.updated_at,  # type: ignore[assignment]
                message_count=len(conv.messages) if hasattr(conv, "messages") else 0,
            )
        )
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
    if conv.created_by != current_user.id and not current_user.is_superuser:  # type: ignore[assignment]
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
    if conv.created_by != current_user.id and not current_user.is_superuser:  # type: ignore[assignment]
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
    if conv.created_by != current_user.id and not current_user.is_superuser:  # type: ignore[assignment]
        raise HTTPException(status_code=403, detail="Access denied")
    return get_messages(db, conv_id, skip=skip, limit=limit)


@router.post("/{conv_id}/query", response_model=QueryResponse)
def query_conversation(
    conv_id: str,  # 对话ID，字符串类型
    req: QueryRequest,  # 查询请求对象，包含查询内容和相关参数
    db: Session = Depends(get_db),  # 数据库会话，依赖注入获取
    current_user: User = Depends(get_current_user),  # 当前用户，依赖注入获取
):
    # 根据ID获取对话
    conv = get_conversation_by_id(db, conv_id)
    # 如果对话不存在，抛出404异常
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # 检查用户权限：只有创建者或超级用户可以访问
    if conv.created_by != current_user.id and not current_user.is_superuser:  # type: ignore[assignment]
        raise HTTPException(status_code=403, detail="Access denied")

    # Build history: summary for old rounds + recent messages
    history, summary, total = _build_history(conv, db, conv_id)

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
    _maybe_summarize(db, conv_id, total)

    return QueryResponse(answer=answer, sources=sources)


def _save_messages_background(conv_id: str, query: str, answer: str, sources: list):
    """Background task: save messages with its own DB session after stream completes."""
    db = SessionLocal()
    try:
        add_message(db, conv_id, role="user", content=query)
        add_message(db, conv_id, role="assistant", content=answer, sources=sources)
        db.commit()
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("Failed to save streamed messages: %s", e)
        db.rollback()
    finally:
        db.close()


@router.post("/{conv_id}/query/stream")
def query_conversation_stream(
    conv_id: str,
    req: QueryRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """流式RAG查询 — SSE标记流，然后是源数据+数据库保存。"""

    conv = get_conversation_by_id(db, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if conv.created_by != current_user.id and not current_user.is_superuser:  # type: ignore[assignment]
        raise HTTPException(status_code=403, detail="Access denied")

    history, summary, total = _build_history(conv, db, conv_id)

    def event_stream():
        try:
            for event in query_rag_stream(
                query=req.query, top_k=req.top_k, history=history, summary=summary
            ):
                if event["type"] == "token":
                    yield f"data: {json.dumps({'token': event['data']})}\n\n"
                elif event["type"] == "sources":
                    sources = event["data"]
                    # Schedule background DB write
                    background_tasks.add_task(
                        _save_messages_background,
                        conv_id,
                        req.query,
                        event["full_answer"],
                        sources,
                    )
                    # Also trigger summary regeneration in background
                    background_tasks.add_task(_maybe_summarize_background, conv_id, total)
                    yield f"data: {json.dumps({'sources': sources, 'done': True})}\n\n"
                    yield "data: [DONE]\n\n"
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.warning("Streaming RAG query failed: %s", e)
            yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _maybe_summarize_background(conv_id: str, total: int):
    """后台任务：使用自己的数据库会话触发摘要重新生成。"""
    db = SessionLocal()
    try:
        _maybe_summarize(db, conv_id, total)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
