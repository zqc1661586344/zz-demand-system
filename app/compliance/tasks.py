"""Compliance Celery tasks — persistent, retryable background review execution."""

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
    max_retries=settings.compliance_max_retry,
    default_retry_delay=60,
    autoretry_for=RETRY_EXCEPTIONS,
    acks_late=True,
    reject_on_worker_lost=True,
)
def run_compliance_review(self, payload: dict) -> dict:
    """执行合规审查（持久化任务，可重试）。

    Args:
        payload: ReviewService.run_review 透传的 keyword args dict，包含
                 review_id / document_id / compliance_doc_id / file_path 等。
    """
    from app.compliance.harness.runtime import get_harness

    review_id = payload.get("review_id", "unknown")
    logger.info("celery: running compliance review %s", review_id)

    try:
        harness = get_harness()
        result = harness.start_review(**payload)
        logger.info("celery: review %s finished with status=%s", review_id, result.get("status"))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("celery: review %s failed: %s", review_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error("celery: review %s max retries exceeded", review_id)
            return {"review_id": review_id, "status": "failed", "error": str(exc)}


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    autoretry_for=RETRY_EXCEPTIONS,
    acks_late=True,
    reject_on_worker_lost=True,
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
        logger.exception("celery: resume review %s failed: %s", review_id, exc)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.error("celery: resume review %s max retries exceeded", review_id)
            return {"review_id": review_id, "status": "failed", "error": str(exc)}
