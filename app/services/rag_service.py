"""RAG Service — 业务语义化的 RAG 门面层。

把 api/ 和 services/ 层对 rag 内部模块的穿透 import（retrievers / vector_store /
pipeline / chain / tasks）统一收敛到本模块，对外只暴露业务语义化接口。
compliance 侧的 import 属于技术依赖（复用 LLM provider / 文件加载器），保持不变。

调用方：
  - api/conversations.py  → query / query_stream / summarize / sanitize_citations
  - api/documents.py       → enqueue_process（统一 Celery / BackgroundTasks）
  - services/document_service.py → delete_document_chunks / notify_data_changed
"""

from __future__ import annotations

from fastapi import BackgroundTasks

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. 摄入：把文件喂给 RAG pipeline
# ---------------------------------------------------------------------------


def enqueue_process(doc_id: str, background_tasks: BackgroundTasks | None = None) -> None:
    """统一入口：根据 settings 决定走 Celery delay 还是 BackgroundTasks.add_task。

    api/documents.py 里原来散落的 Celery/BackgroundTasks 分支判断被收敛到这里，
    调用方只需要传 doc_id 和 background_tasks 实例。
    """
    if settings.celery_broker_url and settings.use_celery_task:
        from app.rag.tasks import process_document_task

        logger.info("enqueue_process: celery delay for doc %s", doc_id)
        process_document_task.delay(doc_id)
    elif background_tasks is not None:
        logger.info("enqueue_process: BackgroundTasks.add_task for doc %s", doc_id)
        background_tasks.add_task(process_document, doc_id)
    else:
        logger.info("enqueue_process: synchronous fallback for doc %s", doc_id)
        process_document(doc_id)


def process_document(doc_id: str) -> None:
    """同步处理一个文档（供 enqueue_process 的 BT/sync 分支及测试调用）。"""
    from app.rag.pipeline import process_document as _process

    _process(doc_id)


# ---------------------------------------------------------------------------
# 2. 检索问答：query / query_stream / summarize / sanitize_citations
# ---------------------------------------------------------------------------


def query_rag(
    query: str,
    top_k: int = 10,
    history: list[dict] | None = None,
    summary: str | None = None,
    user_id: str | None = None,
) -> dict:
    """单次 RAG 问答，返回 dict（answer / sources / free_chat_flag 等）。"""
    from app.rag.chain import query_rag as _query

    return _query(
        query=query,
        top_k=top_k,
        history=history,
        summary=summary,
        user_id=user_id,
    )


def query_rag_stream(
    query: str,
    top_k: int = 5,
    history: list[dict] | None = None,
    summary: str | None = None,
    user_id: str | None = None,
):
    """流式 RAG 问答，yield event dict（token / sources / free_chat）。"""
    from app.rag.chain import query_rag_stream as _query_stream

    return _query_stream(
        query=query,
        top_k=top_k,
        history=history,
        summary=summary,
        user_id=user_id,
    )


def generate_summary(history: list[dict]) -> str:
    """从对话历史生成摘要。"""
    from app.rag.chain import generate_summary as _summary

    return _summary(history)


def sanitize_citations(answer: str, sources: list | None = None) -> str:
    """清洗答案中的引用标注（如 [1] [2]），防止 LLM 幻觉造引用。"""
    from app.rag.chain import sanitize_citations as _sanitize

    return _sanitize(answer, sources or [])


# ---------------------------------------------------------------------------
# 3. 文档生命周期：删除向量 / 通知 BM25 刷新
# ---------------------------------------------------------------------------


def delete_document_chunks(doc_id: str) -> None:
    """按 document_id 元数据从 PGVector 中删除向量。"""
    from app.rag.vector_store import delete_documents_from_store as _del

    _del(doc_id)


def notify_data_changed(user_id: str, shared: bool = False) -> None:
    """通知各 worker BM25 缓存已过期，下次查询时重建。"""
    from app.rag.retrievers import mark_bm25_data_changed as _mark

    _mark(user_id, shared=shared)
