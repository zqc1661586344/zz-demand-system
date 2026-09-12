"""Shared PGVector infrastructure — engine, HNSW index, store factory, delete helper.

两个 collection（业务文档 / 法规）都共用 langchain_pg_embedding 同一张表，
所以这些底层能力只需维护一份。业务侧 wrapper（rag.vector_store /
compliance.knowledge.vector_store）各自 import 本模块，对外 API 保持语义化。
"""

from functools import lru_cache

from langchain_postgres.vectorstores import DistanceStrategy, PGVector
from sqlalchemy import create_engine, text

from app.config import settings
from app.logging_config import get_logger
from app.rag.embeddings import get_embedding_model

logger = get_logger(__name__)

EMBEDDING_DIM = 1024

_hnsw_index_ensured = False


@lru_cache
def maintenance_engine():
    """唯一的维护用 SQLAlchemy engine（psycopg3），跨 collection 共享。"""
    return create_engine(
        settings.vector_store_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=settings.db_pool_pre_ping,
        pool_recycle=settings.db_pool_recycle,
    )


def ensure_hnsw_index() -> None:
    """幂等地建 HNSW (cosine) 索引——全表只需一个，业务/法规 collection 共用。"""
    global _hnsw_index_ensured
    if _hnsw_index_ensured:
        return
    try:
        with maintenance_engine().begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_langchain_pg_embedding_hnsw "
                    "ON langchain_pg_embedding USING hnsw (embedding vector_cosine_ops)"
                )
            )
            logger.info("HNSW index ensured on langchain_pg_embedding")
            _hnsw_index_ensured = True
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to ensure HNSW index: %s", e)


@lru_cache
def make_vector_store(collection_name: str) -> PGVector:
    """按 collection_name 缓存 PGVector 单例。业务侧 wrapper 传各自的 collection 名即可。"""
    return PGVector(
        embeddings=get_embedding_model(),
        collection_name=collection_name,
        connection=settings.vector_store_url,
        embedding_length=EMBEDDING_DIM,
        distance_strategy=DistanceStrategy.COSINE,
        use_jsonb=True,
        create_extension=True,
    )


def delete_by_metadata(collection_name: str, metadata_key: str, metadata_value: str) -> None:
    """通用删除：按 collection + JSONB metadata key 删除向量（幂等，表未创建时直接返回）。"""
    try:
        with maintenance_engine().begin() as conn:
            if not conn.dialect.has_table(conn, "langchain_pg_embedding"):
                logger.info(
                    "pgvector tables not created yet, skip delete for %s=%s",
                    metadata_key,
                    metadata_value,
                )
                return
            conn.execute(
                text(
                    "DELETE FROM langchain_pg_embedding "
                    "WHERE collection_id = (SELECT uuid FROM langchain_pg_collection "
                    "  WHERE name = :c) "
                    "AND cmetadata->>:k = :v"
                ),
                {"c": collection_name, "k": metadata_key, "v": metadata_value},
            )
            logger.info(
                "pgvector delete: collection=%s %s=%s",
                collection_name,
                metadata_key,
                metadata_value,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "pgvector delete failed (collection=%s %s=%s): %s",
            collection_name,
            metadata_key,
            metadata_value,
            e,
        )
