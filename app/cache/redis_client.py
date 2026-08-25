"""Shared Redis client singleton for BM25 cache timestamps.

Usage:
    from app.cache.redis_client import get_redis_client
    r = get_redis_client()
    if r is not None:
        r.setex("bm25:ts:user_123", 300, time.time())

Design:
    Reuses the same Redis URL as the Celery broker (settings.celery_broker_url)
    but uses DB 2 to avoid collision with Celery (DB 0) and Celery result backend
    (DB 1). Falls back gracefully to None when Redis is not configured or
    unreachable — the BM25 cache then operates in local-only mode.
"""

import redis

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_client: "redis.Redis | None" = None


def get_redis_client() -> "redis.Redis | None":
    """Return a shared Redis client, or None if Redis is not configured/unreachable."""
    global _client
    if _client is not None:
        return _client

    if not settings.celery_broker_url:
        logger.info("Redis not configured (celery_broker_url is empty) — BM25 cache local-only")
        return None

    try:
        _client = redis.Redis.from_url(
            settings.celery_broker_url,
            db=2,  # BM25 namespace: DB 0 = Celery broker, DB 1 = result backend
            socket_connect_timeout=2,
            decode_responses=True,
        )
        _client.ping()
        logger.info("Redis BM25 cache client connected (db=2)")
        return _client
    except Exception:
        logger.warning("Redis BM25 cache unavailable — falling back to local-only cache")
        return None