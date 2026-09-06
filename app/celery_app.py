"""Celery application instance — async task queue for document processing."""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "zz_demand_system",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_acks_late=True,  # worker 崩溃后重新投递任务
    task_reject_on_worker_lost=True,
    task_track_started=True,
    result_expires=3600,  # 结果 1 小时后过期
    worker_prefetch_multiplier=1,  # 每个 worker 每次只取一个任务
)

celery_app.autodiscover_tasks(["app.rag", "app.compliance"])

# 部署自检：若 broker_url 为空但 use_celery_task=True，打印 WARNING 提示部署错误
if settings.use_celery_task and not settings.celery_broker_url:
    from app.logging_config import get_logger

    get_logger(__name__).critical(
        "USE_CELERY_TASK=true but CELERY_BROKER_URL is empty — tasks will fail. "
        "Set CELERY_BROKER_URL (e.g. redis://localhost:6379/0) or disable celery."
    )
