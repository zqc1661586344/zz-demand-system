"""Checkpointer 构建 — 三级回退（app/compliance/harness/checkpointer.py）。

设计文档 §5.5.3：PostgresSaver 复用现有 PostgreSQL（断点恢复）。但三处限制需处理：
  1. **psycopg3**：PostgresSaver 需要 psycopg3 连接串；现有项目用 psycopg2-binary（SQLAlchemy），
     两者可共存。DSN 需把 `postgresql+psycopg://`（langchain-postgres 风格）规范化为
     纯 `postgresql://`（psycopg3 可解析）。
  2. **开发 SQLite / test 模式**：`database_url` 默认空→SQLite，无 PG 可用 → 回退
     `langgraph.checkpoint.memory.InMemorySaver`（进程内，审查可在 test 模式端到端跑通）。
  3. **依赖未装**：langgraph 在 compliance extra；未安装时回退 None（graph.compile 无
     checkpointer 也能跑，只是不可断点恢复）。

回退链：PostgresSaver（PG 可用）→ InMemorySaver（无 PG）→ None（langgraph 未装）。
"""

from functools import lru_cache

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def _is_pg_url(url: str) -> bool:
    """是否 PostgreSQL 连接串（postgresql:// 或 postgresql+psycopg:// 前缀）。"""
    return url.startswith("postgresql")


def normalize_pg_dsn(url: str) -> str:
    """把带驱动后缀的 `postgresql+psycopg://` / `postgresql+psycopg2://` 规范化为 `postgresql://`。

    PostgresSaver 走 psycopg3，只认 `postgresql://` 前缀；`+psycopg`/`+psycopg2` 是
    SQLAlchemy 驱动后缀，需要完整移除才能被 psycopg_pool 正确解析。
    """
    import re

    return re.sub(r"^postgresql\+\w+:", "postgresql:", url, count=1)


def _preferred_pg_url() -> str | None:
    """取首选 PG 连接串：优先 VECTOR_STORE_URL（psycopg3 格式），其次 database_url。"""
    if _is_pg_url(settings.vector_store_url or ""):
        return settings.vector_store_url
    if _is_pg_url(settings.database_url or ""):
        return settings.database_url
    return None


def build_checkpointer():
    """构建 checkpointer（三级回退：PostgresSaver → InMemorySaver → None）。

    PG 模式用 psycopg_pool 连接池（进程级常驻，不是上下文管理器）。
    langgraph-checkpoint-postgres >= 3.0 的 PostgresSaver 接受 pool 对象直接构造，
    而 from_conn_string() 返回的是 _GeneratorContextManager（必须 with 包裹），
    被当作 PostgresSaver 实例调用 setup() 会抛 AttributeError —— 这是旧代码静默
    失败、永远回退 InMemorySaver 的根因。

    降级策略：
      - production / use_celery_task=True：PG 初始化失败直接 raise，不静默降级
      - 开发态（无 PG / SQLite）：回退 InMemorySaver，打 CRITICAL 日志提示风险
    """
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError:
        logger.warning("langgraph not installed — checkpointer None")
        return None

    pg_url = _preferred_pg_url()
    if pg_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool

            conn_info = normalize_pg_dsn(pg_url)
            pool = ConnectionPool(
                conninfo=conn_info,
                min_size=2,
                max_size=10,
                kwargs={"autocommit": True, "row_factory": "dict_row"},
            )
            saver = PostgresSaver(pool)
            saver.setup()
            logger.info(
                "checkpointer: PostgresSaver via psycopg_pool on %s (min=2 max=10)",
                conn_info.split("@")[-1],
            )
            return saver
        except ImportError as e:
            logger.warning("PostgresSaver deps missing (%s) — fall back InMemory", e)
        except Exception as e:  # noqa: BLE001
            is_prod = getattr(settings, "app_env", "") == "production" or getattr(
                settings, "use_celery_task", False
            )
            if is_prod:
                logger.critical(
                    "PostgresSaver setup FAILED in production — raising to prevent silent "
                    "data loss. Error: %s",
                    e,
                    exc_info=True,
                )
                raise
            logger.critical(
                "PostgresSaver setup failed (%s) — falling back InMemorySaver. "
                "HITL resume WILL fail after restart! Fix PG connection before deploy.",
                e,
                exc_info=True,
            )

    saver = InMemorySaver()
    logger.warning(
        "checkpointer: InMemorySaver (no PostgreSQL). "
        "HITL resume WILL fail after restart or across worker processes."
    )
    return saver


@lru_cache
def get_checkpointer():
    """checkpointer 单例（lru_cache，供 harness 重用）。"""
    return build_checkpointer()
