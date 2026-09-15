"""Conversation API routes — CRUD and RAG query."""

import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal, get_db
from app.dependencies import get_current_user
from app.logging_config import get_logger
from app.middleware.rate_limit import get_limiter
from app.models.conversation import Conversation
from app.models.user import User
from app.services.rag_service import (
    generate_summary,
    query_rag,
    query_rag_stream,
    sanitize_citations,
)
from app.rag.errors import to_structured_dict
from app.schemas.common import PaginatedResponse
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    MessageResponse,
    QueryRequest,
    QueryResponse,
)
from app.services.conversation_service import (
    add_message,
    count_messages,
    create_conversation,
    delete_conversation,
    get_conversation_by_id,
    get_conversations_for_user,
    get_messages,
    get_recent_messages,
    update_summary,
)

# 在最近窗口中保留多少个: 助手+用户消息对 = 1轮
RECENT_ROUNDS = 20
SUMMARY_INTERVAL = RECENT_ROUNDS * 2  # 40消息 = 每20轮

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

logger = get_logger(__name__)


def _build_history(conv: Conversation, db: Session, conv_id: str):
    """根据之前的消息构建历史记录和摘要。返回（历史记录，摘要，总数）。

    total 用 count_messages 取真实总数（旧实现用截断后的 len(<=100) 会导致超长对话丢失最新上下文且摘要停更）；历史取最近 RECENT_ROUNDS*2 条（时间正序）。
    """
    total = count_messages(db, conv_id)
    recent_msgs = get_recent_messages(db, conv_id, limit=RECENT_ROUNDS * 2)
    history = [{"role": m.role, "content": m.content} for m in recent_msgs]
    summary = conv.summary if total > RECENT_ROUNDS * 2 else None

    return history, summary, total


def _maybe_summarize(db: Session, conv_id: str, total: int):
    """每达到SUMMARY_INTERVAL条消息时触发摘要重新生成。"""
    if (total + 2) >= SUMMARY_INTERVAL and (total + 2) % SUMMARY_INTERVAL == 0:
        try:
            # 取全量消息（limit=total+2 覆盖真实总数，不再被硬编码 100 截断）
            all_msgs = get_recent_messages(db, conv_id, limit=total + 2)
            history_all = [{"role": m.role, "content": m.content} for m in all_msgs]
            new_summary = generate_summary(history_all)
            update_summary(db, conv_id, new_summary)
        except Exception as e:
            logger.error("failed to generate conversation summary: %s", e)


def _get_owned_conv(conv_id: str, db: Session, current_user: User) -> Conversation:
    """取对话 + 鉴权。对话不存在抛 404，非 owner 且非 superuser 抛 403。"""
    conv = get_conversation_by_id(db, conv_id)
    if conv is None:
        logger.error("conversation %s not found", conv_id)
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conv.created_by != current_user.id and not current_user.is_superuser:  # type: ignore[assignment]
        logger.error("user %s not owner of conversation %s", current_user.id, conv_id)
        raise HTTPException(status_code=403, detail="Access denied")
    return conv


def _prepare_query(conv_id: str, db: Session, current_user: User):
    """路由层公共前置：取对话 + 鉴权 + 构建历史 + 计算 uid。

    返回 (conv, history, summary, total, uid)，权限/对话不存在时直接抛 HTTPException。
    """
    conv = _get_owned_conv(conv_id, db, current_user)
    history, summary, total = _build_history(conv, db, conv_id)
    logger.debug(
        f"query conversation history built, total={total}, summary={summary}, history={history}"
    )
    # 如果不让敏感信息泄露，考虑用下面的格式，只显示总数和历史记录长度
    # logger.debug("query conversation history built, total=%d, history_len=%d", total, len(history))

    uid = None if current_user.is_superuser else str(current_user.id)
    logger.debug(f"query conversation uid={uid}")

    return conv, history, summary, total, uid


@router.post("", response_model=ConversationResponse, status_code=201)
def new_conversation(
    req: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = create_conversation(db, title=req.title, created_by=current_user.id)  # type: ignore[assignment]
    return conv


@router.get("", response_model=PaginatedResponse[ConversationResponse])
def list_conversations(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    total = db.query(Conversation).filter(Conversation.created_by == current_user.id).count()
    convs = get_conversations_for_user(db, current_user.id, skip=offset, limit=limit)  # type: ignore[assignment]
    items = []
    for conv in convs:
        items.append(
            ConversationResponse(
                id=conv.id,  # type: ignore[assignment]
                title=conv.title,  # type: ignore[assignment]
                created_by=conv.created_by,  # type: ignore[assignment]
                created_at=conv.created_at,  # type: ignore[assignment]
                updated_at=conv.updated_at,  # type: ignore[assignment]
                message_count=len(conv.messages) if hasattr(conv, "messages") else 0,
            )
        )
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{conv_id}", response_model=ConversationResponse)
def get_conversation(
    conv_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = _get_owned_conv(conv_id, db, current_user)
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
    _get_owned_conv(conv_id, db, current_user)
    if not delete_conversation(db, conv_id):
        raise HTTPException(status_code=500, detail="Failed to delete conversation")
    return {"message": "Conversation deleted successfully"}


@router.get("/{conv_id}/messages", response_model=PaginatedResponse[MessageResponse])
def list_messages(
    conv_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_conv(conv_id, db, current_user)
    total = count_messages(db, conv_id)
    items = get_messages(db, conv_id, skip=offset, limit=limit)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


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
    _, history, summary, total, uid = _prepare_query(conv_id, db, current_user)

    # Always run RAG against the single document collection
    free_chat = False
    try:
        result = query_rag(
            query=req.query, top_k=req.top_k, history=history, summary=summary, user_id=uid
        )
        answer = result["answer"]
        sources = result["sources"]
        free_chat = bool(result.get("free_chat", False))
    except Exception as exc:
        logger.exception("RAG query failed for conversation %s, query=%r", conv_id, req.query)
        answer = to_structured_dict(exc, extra={"conversation_id": conv_id})["message"]
        sources = []

    # Save user message
    add_message(db, conv_id, role="user", content=req.query)
    # 存库的是纯模型回答（前端已按 free_chat 渲染提示语），历史不含提示语非流式路径同样把 free_chat 落库，与流式 _save_messages_background 保持一致的标记。
    add_message(
        db,
        conv_id,
        role="assistant",
        content=answer,
        sources=sources,
        free_chat=free_chat,
    )

    # Trigger summary regeneration every SUMMARY_INTERVAL messages
    _maybe_summarize(db, conv_id, total)

    return QueryResponse(answer=answer, sources=sources, free_chat=free_chat)


def _save_messages_background(conv_id: str, answer: str, sources: list, free_chat: bool = False):
    """后台任务：流处理完成后，使用自己的数据库会话保存助手消息。

    注意：用户消息已在 `event_stream` 生成流之前【同步】写入数据库，以确保下一轮追问的 `_build_history` 一定能读到上一轮的用户问题（避免竞态导致"无记忆"）。这里后台只负责保存助手回答。
    """
    db = SessionLocal()
    try:
        # 落库前剔除越界/错乱的 `[来源 N]`/`[Source N]` 引用，保证存库与前端一致（前端流式已按 token 渲染，前端展示不再改，这里只保证存库干净）。
        clean_answer = sanitize_citations(answer, sources or [])
        add_message(
            db,
            conv_id,
            role="assistant",
            content=clean_answer,
            sources=sources,
            free_chat=free_chat,
        )
        db.commit()
    except Exception as e:
        logger.warning("failed to save streamed messages: %s", e)
        db.rollback()
    finally:
        db.close()


def _maybe_summarize_background(conv_id: str, total: int):
    """后台任务：使用自己的数据库会话触发摘要重新生成。"""
    db = SessionLocal()
    try:
        _maybe_summarize(db, conv_id, total)
        db.commit()
    except Exception as e:
        logger.warning("background summarize failed for %s: %s", conv_id, e)
        db.rollback()
    finally:
        db.close()


def _event_stream(
    conv_id: str,
    query: str,
    top_k: int,
    history: list[dict] | None,
    summary: str | None,
    total: int,
    uid: str | None,
    background_tasks: BackgroundTasks,
):
    """流式 RAG SSE 生成器 —— 逐 token 推送 + sources 收尾 + 异常兜底。

    用户消息在进入流之前就同步写入数据库（避免下一轮追问的 `_build_history` 竞态），assistant 消息和摘要通过 background_tasks 异步完成（不阻塞流输出）。
    """
    free_chat = False

    # 用户消息在流式路径下，必须【同步】写入数据库，才能确保下一轮追问的 `_build_history` 一定能读到上一轮的用户问题（避免竞态导致"无记忆"）。
    try:
        local_db = SessionLocal()
        try:
            add_message(local_db, conv_id, role="user", content=query)
            local_db.commit()
        finally:
            local_db.close()
    except Exception as e:
        logger.error("failed to persist user message for %s: %s", conv_id, e)

    full_answer_buffer = []
    try:
        for event in query_rag_stream(
            query=query, top_k=top_k, history=history, summary=summary, user_id=uid
        ):
            if event["type"] == "token":
                full_answer_buffer.append(event["data"])
                yield f"data: {json.dumps({'token': event['data']})}\n\n"
            elif event["type"] == "free_chat":
                free_chat = True
                yield f"data: {json.dumps({'free_chat': True})}\n\n"
            elif event["type"] == "sources":
                sources = event["data"]
                final_answer = event.get("full_answer", "") or "".join(full_answer_buffer)
                background_tasks.add_task(
                    _save_messages_background,
                    conv_id,
                    final_answer,
                    sources,
                    free_chat,
                )
                background_tasks.add_task(_maybe_summarize_background, conv_id, total)
                yield f"data: {json.dumps({'sources': sources, 'done': True})}\n\n"
                yield "data: [DONE]\n\n"
    except Exception as exc:
        # 处理异常：保存已生成的 token 并返回错误信息，兜底策略
        partial_answer = "".join(full_answer_buffer)
        structured = to_structured_dict(exc, extra={"conversation_id": conv_id})
        logger.warning(
            "streaming RAG query failed for %s (partial=%d chars): error=%s, exc=%s",
            conv_id,
            len(partial_answer),
            structured.get("error_code"),
            exc,
        )
        # 如果有部分生成的 token，返给前端已生成的 token，后面再补上一个错误信息
        if partial_answer:
            _save_messages_background(conv_id, partial_answer, [], True)
            yield f"data: {json.dumps({'token': partial_answer, 'partial': True})}\n\n"
        else:
            # 保存错误信息
            _save_messages_background(
                conv_id,
                f"[回答生成失败: {structured.get('message', '未知错误')}]",
                [],
                True,
            )
        # 返回错误信息
        yield f"data: {json.dumps({'error': structured, 'done': True, 'partial': bool(partial_answer)})}\n\n"
        yield "data: [DONE]\n\n"


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
    """流式 RAG 查询 — 返回 SSE 事件流。

    事件顺序：[free_chat]? → token* → sources（含 full_answer） → [DONE]
    assistant 消息入库和摘要生成为后台异步任务，不阻塞流输出。
    """
    _, history, summary, total, uid = _prepare_query(conv_id, db, current_user)

    return StreamingResponse(
        _event_stream(
            conv_id, req.query, req.top_k, history, summary, total, uid, background_tasks
        ),
        media_type="text/event-stream",
    )
