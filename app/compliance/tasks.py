"""Compliance Celery tasks — persistent, retryable background review execution.

重试策略：autoretry_for 只覆盖网络/OpenAI 瞬时错误；ValueError（payload 缺字段）、
IntegrityError（FK 冲突）、FileNotFoundError（文档被删）等永久失败直接置 review failed，
不再浪费重试预算。
"""

from app.celery_app import celery_app
from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def _get_openai_errors() -> tuple[type[Exception], ...]:
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
    max_retries=settings.compliance_task_max_retries,
    default_retry_delay=60,
    autoretry_for=RETRY_EXCEPTIONS,
    retry_backoff=True,
    retry_backoff_max=300,
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=1800,
    soft_time_limit=1500,
)
def run_compliance_review(self, payload: dict) -> dict:
    """执行合规审查（持久化任务，可重试）。"""
    from app.compliance.harness.runtime import get_harness

    review_id = payload.get("review_id", "unknown")
    logger.info("celery: running compliance review %s", review_id)

    try:
        harness = get_harness()
        result = harness.start_review(**payload)
        logger.info("celery: review %s finished with status=%s", review_id, result.get("status"))
        return result
    except Exception as exc:  # noqa: BLE001 — autoretry_for 已覆盖瞬时错误，到这里是永久失败
        logger.exception("celery: review %s FAILED (permanent): %s", review_id, exc)
        _mark_review_failed(review_id, str(exc))
        return {"review_id": review_id, "status": "failed", "error": str(exc)}


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=RETRY_EXCEPTIONS,
    retry_backoff=True,
    retry_backoff_max=120,
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=600,
    soft_time_limit=500,
)
def resume_compliance_review(self, review_id: str) -> dict:
    """人工确认后续跑 generate_report（HITL resume，可重试）。"""
    from app.compliance.harness.runtime import get_harness

    logger.info("celery: resuming compliance review %s", review_id)
    try:
        harness = get_harness()
        result = harness.resume_review(review_id)
        logger.info("celery: review %s resumed with status=%s", review_id, result.get("status"))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("celery: resume review %s FAILED (permanent): %s", review_id, exc)
        _mark_review_failed(review_id, str(exc))
        return {"review_id": review_id, "status": "failed", "error": str(exc)}


def _mark_review_failed(review_id: str, error: str) -> None:
    """把 review 状态置为 failed 并写 error_message（DB 操作，幂等）。"""
    try:
        from app.database import SessionLocal
        from app.compliance.models.review import ComplianceReview

        db = SessionLocal()
        try:
            row = db.query(ComplianceReview).filter(ComplianceReview.id == review_id).first()
            if row:
                row.status = "failed"
                row.error_message = error[:2000]
                db.commit()
                logger.info("celery: marked review %s as failed", review_id)
        finally:
            db.close()
    except Exception as e:  # noqa: BLE001
        logger.error("celery: failed to mark review %s as failed: %s", review_id, e)
