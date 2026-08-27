"""Conversation API routes — CRUD and RAG query."""

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.dependencies import get_current_user
from app.logging_config import get_logger
from app.middleware.rate_limit import get_limiter
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
RECENT_ROUNDS = 20
SUMMARY_INTERVAL = RECENT_ROUNDS * 2  # 40 messages = every 20 rounds

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

logger = get_logger(__name__)


def _build_history(conv, db, conv_id):
    """根据之前的消息构建历史记录和摘要。返回（历史记录，摘要，总数）。"""
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
    """每达到SUMMARY_INTERVAL条消息时触发摘要重新生成。"""
    if (total + 2) >= SUMMARY_INTERVAL and (total + 2) % SUMMARY_INTERVAL == 0:
        try:
            all_msgs = get_messages(db, conv_id, limit=100)
            history_all = [{"role": m.role, "content": m.content} for m in all_msgs]
            new_summary = generate_summary(history_all)
            update_summary(db, conv_id, new_summary)
        except Exception as e:
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
@get_limiter().limit(settings.rate_limit_llm_query)
def query_conversation(
    conv_id: str,  # 对话ID，字符串类型
    req: QueryRequest,  # 查询请求对象，包含查询内容和相关参数
    request: Request,  # 用于 slowapi 限流识别来源 IP
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

    # 构造 user_id：superuser 传 None（全量检索），普通用户传自己的 id
    uid = None if current_user.is_superuser else str(current_user.id)

    # Always run RAG against the single document collection
    free_chat = False
    try:
        result = query_rag(
            query=req.query, top_k=req.top_k, history=history, summary=summary, user_id=uid
        )
        answer = result["answer"]
        sources = result["sources"]
        free_chat = bool(result.get("free_chat", False))
    except Exception:
        logger.exception("RAG query failed for conversation %s, query=%r", conv_id, req.query)
        answer = "抱歉，问答服务暂时不可用，请稍后再试。"
        sources = []

    # Save user message
    add_message(db, conv_id, role="user", content=req.query)
    # 【根治】：存库的是纯模型回答（前端已按 free_chat 渲染提示语），历史不含提示语
    add_message(db, conv_id, role="assistant", content=answer, sources=sources)

    # Trigger summary regeneration every SUMMARY_INTERVAL messages
    _maybe_summarize(db, conv_id, total)

    return QueryResponse(answer=answer, sources=sources, free_chat=free_chat)


def _save_messages_background(conv_id: str, answer: str, sources: list, free_chat: bool = False):
    """后台任务：流处理完成后，使用自己的数据库会话保存助手消息。

    注意：用户消息已在 `event_stream` 生成流之前【同步】写入数据库，
    以确保下一轮追问的 `_build_history` 一定能读到上一轮的用户问题（避免竞态导致"无记忆"）。
    这里后台只负责保存助手回答。
    """
    db = SessionLocal()
    try:
        add_message(
            db, conv_id, role="assistant", content=answer, sources=sources, free_chat=free_chat
        )
        db.commit()
    except Exception as e:
        logger.warning("Failed to save streamed messages: %s", e)
        db.rollback()
    finally:
        db.close()


@router.post("/{conv_id}/query/stream")
@get_limiter().limit(settings.rate_limit_llm_query)
def query_conversation_stream(
    conv_id: str,
    req: QueryRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """流式RAG查询 — SSE标记流，然后是源数据+数据库保存。"""
    logger.info("stream query begin...")

    # 根据ID获取对话
    conv = get_conversation_by_id(db, conv_id)
    if conv is None:
        logger.error(f"conversation:{conv_id} not found")
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 检查用户权限：只有创建者或超级用户可以访问
    if conv.created_by != current_user.id and not current_user.is_superuser:  # type: ignore[assignment]
        logger.error(f"access denied for user:{current_user.id} to conversation:{conv_id}")
        raise HTTPException(status_code=403, detail="Access denied")

    # TODO: 有可优化的点，如果用户连续提问，可以复用历史
    # 构建历史：旧轮次摘要+最新消息（最近20轮）
    history, summary, total = _build_history(conv, db, conv_id)

    # superuser：None（全量可见）
    # 普通用户： current_user.id
    uid = None if current_user.is_superuser else str(current_user.id)

    def event_stream():
        free_chat = False
        # 使用独立 SessionLocal 保存用户消息，不持有 FastAPI 注入的 db session 跨流
        local_db = SessionLocal()
        try:
            add_message(local_db, conv_id, role="user", content=req.query)
            local_db.commit()
        finally:
            local_db.close()
        try:
            for event in query_rag_stream(
                query=req.query, top_k=req.top_k, history=history, summary=summary, user_id=uid
            ):
                if event["type"] == "token":
                    yield f"data: {json.dumps({'token': event['data']})}\n\n"
                elif event["type"] == "free_chat":
                    free_chat = True
                    yield f"data: {json.dumps({'free_chat': True})}\n\n"
                elif event["type"] == "sources":
                    sources = event["data"]
                    # Schedule background DB write (仅助手回答，用户消息已同步保存)
                    background_tasks.add_task(
                        _save_messages_background,
                        conv_id,
                        event["full_answer"],
                        sources,
                        free_chat,
                    )
                    # 同时在后台触发摘要重新生成
                    background_tasks.add_task(_maybe_summarize_background, conv_id, total)
                    yield f"data: {json.dumps({'sources': sources, 'done': True})}\n\n"
                    yield "data: [DONE]\n\n"
        except Exception as e:
            logger.warning("streaming RAG query failed: %s", e)
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
