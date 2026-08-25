"""Rate limiting — slowapi integration for API endpoints."""
from app.config import settings

__all__ = []

_limiter = None


def get_limiter():
    """Lazy-init limiter singleton（避免 import 时无 settings）。"""
    global _limiter
    if _limiter is None:
        from slowapi import Limiter
        from slowapi.util import get_remote_address

        storage_uri = None
        if settings.rate_limit_enabled and settings.celery_broker_url:
            # 复用 Redis 作为限流后端（跨 worker 一致）
            storage_uri = settings.celery_broker_url

        _limiter = Limiter(
            key_func=get_remote_address,
            default_limits=[settings.rate_limit_default] if settings.rate_limit_enabled else [],
            storage_uri=storage_uri,
        )
    return _limiter