"""法规向量库 — 独立 PGVector collection=compliance_regulations。

复刻 `app/rag/vector_store.py` 的 langchain-postgres 封装模式，但与业务文档
（collection=`documents`）完全隔离。向量存同一张 `langchain_pg_embedding` 表，
靠 collection uuid 区分；HNSW 索引只需幂等建一次（对整表），法规 collection 复用。

用法：
    add_regulations_to_store(docs)                  # docs: list[langchain Document]
    delete_regulations_from_store(regulation_id)    # 按 metadata.regulation_id 删除
    search_regulations(query, k=5, **metadata)      # → list[(Document, score)]
"""

from functools import lru_cache

from langchain_core.documents import Document
from langchain_postgres.vectorstores import DistanceStrategy, PGVector
from sqlalchemy import create_engine, text

from app.config import settings
from app.logging_config import get_logger
from app.rag.embeddings import get_embedding_model

logger = get_logger(__name__)

# bge-m3 向量维度（与业务库一致；PGVector 必须固定维度才能建 HNSW 索引）
EMBEDDING_DIM = 1024
_hnsw_index_ensured = False


@lru_cache
def _maintenance_engine():
    """维护用 SQLAlchemy engine（psycopg3），用于建 HNSW 索引与按 metadata 删除向量。"""
    return create_engine(
        settings.vector_store_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=settings.db_pool_pre_ping,
        pool_recycle=settings.db_pool_recycle,
    )


def _ensure_hnsw_index() -> None:
    """幂等地为 embedding 建 HNSW（cosine）索引；无索引时向量检索退化为全表扫描。

    与 app/rag/vector_store 同建在 langchain_pg_embedding 上（同一表），
    CREATE INDEX IF NOT EXISTS 幂等，业务/法规共用一个索引即可。
    """
    global _hnsw_index_ensured
    if _hnsw_index_ensured:
        return
    try:
        with _maintenance_engine().begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw "
                    "ON langchain_pg_embedding USING hnsw (embedding vector_cosine_ops)"
                )
            )
            logger.info("HNSW index ensured on langchain_pg_embedding (compliance)")
            _hnsw_index_ensured = True
    except Exception as e:  # noqa: BLE001 — 索引缺失只影响性能，不应阻断写入
        logger.warning("failed to ensure compliance HNSW index: %s", e)


@lru_cache
def get_regulation_vector_store() -> PGVector:
    """返回法规向量库实例（单例，lru_cache 缓存，跨请求复用）。"""
    return PGVector(
        embeddings=get_embedding_model(),
        collection_name=settings.compliance_vector_collection,  # "compliance_regulations"
        connection=settings.vector_store_url,
        embedding_length=EMBEDDING_DIM,
        distance_strategy=DistanceStrategy.COSINE,
        use_jsonb=True,
        create_extension=True,
    )


def add_regulations_to_store(docs: list[Document]) -> list[str]:
    """将法规条款文档写入法规向量库，首次写入后自动确保 HNSW 索引存在。

    docs 的 metadata 需含：regulation_id / article_number / chapter / section /
    regulation_type / title，供结构化关联与语义检索过滤。
    """
    vs = get_regulation_vector_store()
    ids = vs.add_documents(docs)
    _ensure_hnsw_index()
    return ids


def delete_regulations_from_store(regulation_id: str) -> None:
    """按 metadata.regulation_id 删除某部法规的全部条款向量（幂等）。

    PGVector.delete 只支持按 id，故直接对 langchain_pg_embedding 表执行 SQL，
    按 collection uuid + cmetadata->>'regulation_id' 过滤。
    """
    try:
        with _maintenance_engine().begin() as conn:
            if not conn.dialect.has_table(conn, "langchain_pg_embedding"):
                return
            conn.execute(
                text(
                    "DELETE FROM langchain_pg_embedding "
                    "WHERE collection_id = (SELECT uuid FROM langchain_pg_collection "
                    "  WHERE name = :c) "
                    "AND cmetadata->>'regulation_id' = :rid"
                ),
                {"c": settings.compliance_vector_collection, "rid": regulation_id},
            )
            logger.info("compliance vector delete: removed vectors for regulation %s", regulation_id)
    except Exception as e:
        logger.warning("compliance vector delete failed for regulation %s: %s", regulation_id, e)


def _build_filter(metadata: dict | None) -> dict | None:
    """把平铺的 metadata 过滤键转成 langchain-postgres 的 filter 结构。

    支持：regulation_id / regulation_type / status（active 等）。空字典返回 None（不过滤）。
    """
    if not metadata:
        return None
    conds = []
    for k, v in metadata.items():
        if v is not None:
            conds.append({k: {"$eq": v}})
    if not conds:
        return None
    if len(conds) == 1:
        return conds[0]
    return {"$and": conds}


def search_regulations(
    query: str,
    k: int = 5,
    regulation_id: str | None = None,
    regulation_type: str | None = None,
    status: str | None = None,
) -> list[tuple[Document, float]]:
    """法规语义检索，返回 (Document, score) 元组列表，score ∈ [0,1] 越高越相关。

    PGVector 的 similarity_search_with_score 返回 cosine 距离（越小越近），
    换算为 score = 1 - dist，与业务库 app/rag/vector_store.similarity_search_with_relevance 语义一致。
    """
    vs = get_regulation_vector_store()
    filter_ = _build_filter(
        {
            "regulation_id": regulation_id,
            "regulation_type": regulation_type,
            "status": status,
        }
    )
    docs_and_dist = vs.similarity_search_with_score(query, k=k, filter=filter_)
    return [(doc, 1.0 - dist) for doc, dist in docs_and_dist]