"""Celery tasks — document processing (persistent, retryable)."""
import logging

from app.celery_app import celery_app
from app.database import SessionLocal
from app.models.document import Document
from app.rag.pipeline import process_document as _process_document


def _get_openai_errors() -> tuple[type[Exception], ...]:
    """OpenAI SDK 瞬时异常（仅在 SDK 已安装时可用）。"""
    try:
        import openai
    except ImportError:
        return ()
    return (
        openai.APIConnectionError,
        openai.APITimeoutError,
        openai.RateLimitError,
        openai.InternalServerError,
    )


RETRY_EXCEPTIONS = (ConnectionError, TimeoutError, OSError) + _get_openai_errors()


@celery_app.task(
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=RETRY_EXCEPTIONS,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_document_task(self, doc_id: str) -> dict:
    """处理文档：加载→分块→向量化索引→更新状态。持久化任务，可重试。"""
    logger = logging.getLogger(__name__)

    # 检查文档是否存在、未被删除
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).first()
        if doc is None:
            logger.error("document %s not found, aborting task", doc_id)
            return {"status": "skipped", "reason": "document_not_found"}
        logger.info(
            "processing document %s (%s) via celery task",
            doc_id,
            doc.original_filename,
        )
    finally:
        db.close()

    # 不可重试的异常（ValueError、FileNotFoundError）不重试
    try:
        _process_document(doc_id)
        # 任务执行完，回查 DB 真实状态作为返回值（pipeline 内部可能吞掉非重试异常置 failed，
        # 直接返回 "indexed" 会与 DB 实际状态矛盾，造成监控/告警失真）。
        db = SessionLocal()
        try:
            st = db.query(Document.status).filter(Document.id == doc_id).scalar()
        finally:
            db.close()
        return {"status": st or "unknown", "doc_id": doc_id}
    except (ValueError, FileNotFoundError) as exc:
        logger.error("non-retryable error processing %s: %s", doc_id, exc)
        # 标记为 failed，不重试
        db = SessionLocal()
        try:
            from app.services.document_service import update_document_status

            update_document_status(db, doc_id, "failed", error_message=str(exc))
        finally:
            db.close()
        return {"status": "failed", "doc_id": doc_id, "error": str(exc)}