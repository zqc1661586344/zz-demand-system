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
    """把 langchain-postgres 风格的 `postgresql+psycopg://` 规范化为纯 `postgresql://`。

    PostgresSaver 走 psycopg3，accept `postgresql://`；`+psycopg` 驱动后缀是
    psycopg2/asyncpg 扩展，需去掉。
    """
    if url.startswith("postgresql+"):
        return url.replace("postgresql+", "postgresql", 1)
    return url


def _preferred_pg_url() -> str | None:
    """取首选 PG 连接串：优先 VECTOR_STORE_URL（psycopg3 格式），其次 database_url。"""
    if _is_pg_url(settings.vector_store_url or ""):
        return settings.vector_store_url
    if _is_pg_url(settings.database_url or ""):
        return settings.database_url
    return None


def build_checkpointer():
    """构建 checkpointer（三级回退：PostgresSaver → InMemorySaver → None）。

    Returns:
        PostgresSaver（PG 可用且 langgraph-checkpoint-postgres + psycopg3 已装）
        或 InMemorySaver（无 PG）或 None（langgraph 未装，不能断点恢复）。
    """
    # langgraph 未装 → None（审查仍可跑，无断点恢复）
    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError:  # langgraph 未装
        logger.warning("langgraph not installed — checkpointer None")
        return None

    pg_url = _preferred_pg_url()
    if pg_url:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            conn_string = normalize_pg_dsn(pg_url)
            saver = PostgresSaver.from_conn_string(conn_string)
            saver.setup()  # 首次运行创建 checkpoint 表（幂等）
            logger.info("checkpointer: PostgresSaver on %s", conn_string.split("@")[-1])
            return saver
        except ImportError:
            logger.warning("langgraph-checkpoint-postgres not installed — fall back InMemory")
        except Exception as e:  # noqa: BLE001 — PG 连接失败不阻断启动
            logger.warning("PostgresSaver setup failed (%s) — fall back InMemory", e)

    # 无 PG 或 PostgresSaver 不可用 → InMemorySaver（test/SQLite 开发）
    saver = InMemorySaver()
    logger.info("checkpointer: InMemorySaver (no PostgreSQL)")
    return saver


@lru_cache
def get_checkpointer():
    """checkpointer 单例（lru_cache，供 harness 重用）。"""
    return build_checkpointer()