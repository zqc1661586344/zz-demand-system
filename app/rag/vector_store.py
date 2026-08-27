"""PGVector vector store wrapper — 单 collection 承载全部文档（pgvector 替代 Chroma）。

对外接口与 Chroma 版保持一致，调用方（pipeline / retrievers / chain / document_service）
无需改动：get_vector_store / add_documents_to_store / delete_documents_from_store /
get_retriever / similarity_search / mmr_search / similarity_search_with_relevance / _user_where。
"""

from functools import lru_cache
from sqlalchemy import create_engine, text
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_postgres.vectorstores import DistanceStrategy, PGVector
from app.config import settings
from app.logging_config import get_logger
from app.rag.embeddings import get_embedding_model

logger = get_logger(__name__)

# bge-m3 向量维度（PGVector 必须固定维度，否则 embedding 列无固定长度、无法建 HNSW 索引）
EMBEDDING_DIM = 1024

# HNSW 索引是否已确保创建（模块级标志，避免每次 add_documents 都执行 CREATE INDEX）
_hnsw_index_ensured = False


def _user_where(user_id: str | None) -> dict | None:
    """构建用户级过滤条件：普通用户只能看到自己的私有文档 + 所有共享文档；superuser 不过滤。"""
    if user_id is None:
        return None
    return {"$or": [{"uploaded_by": {"$eq": user_id}}, {"visibility": {"$eq": "shared"}}]}


@lru_cache
def _maintenance_engine():
    """维护用 SQLAlchemy engine（psycopg3），用于按 metadata 删除向量与建 HNSW 索引。"""
    return create_engine(
        settings.vector_store_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=settings.db_pool_pre_ping,
        pool_recycle=settings.db_pool_recycle,
    )


def _ensure_hnsw_index() -> None:
    """幂等地为 embedding 建 HNSW（cosine）索引；无索引时向量检索退化为全表扫描。
    使用模块级标志避免每次 add_documents 都执行 CREATE INDEX。
    """
    global _hnsw_index_ensured
    if _hnsw_index_ensured:
        return
    try:
        with _maintenance_engine().begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_documents_hnsw "
                    "ON langchain_pg_embedding USING hnsw (embedding vector_cosine_ops)"
                )
            )
            logger.info("HNSW index ensured on langchain_pg_embedding")
            _hnsw_index_ensured = True
    except Exception as e:  # noqa: BLE001 — 索引缺失只影响性能，不应阻断写入
        logger.warning("failed to ensure HNSW index: %s", e)


@lru_cache
def get_vector_store() -> PGVector:
    """返回 pgvector 向量库实例（单例，lru_cache 缓存，跨请求复用）。"""
    return PGVector(
        embeddings=get_embedding_model(),
        collection_name=settings.vector_collection_name,
        connection=settings.vector_store_url,
        embedding_length=EMBEDDING_DIM,
        distance_strategy=DistanceStrategy.COSINE,
        use_jsonb=True,
        create_extension=True,
    )


def add_documents_to_store(docs: list[Document]) -> list[str]:
    """向 PG 向量库添加文档；首次写入后自动确保 HNSW 索引存在。"""
    vs = get_vector_store()
    ids = vs.add_documents(docs)
    _ensure_hnsw_index()
    return ids


def delete_documents_from_store(doc_id: str) -> None:
    """按 document_id 元数据删除向量。

    PGVector.delete 只支持按 id 删除（不支持按 metadata 过滤），
    故直接对 langchain_pg_embedding 表执行 SQL（cmetadata 为 JSONB，支持 -> 取值）。
    向量表尚未创建（首次上传/库为空）时无需删除，直接返回。
    """
    try:
        with _maintenance_engine().begin() as conn:
            # 表还没创建（首次上传）时无需删除
            if not conn.dialect.has_table(conn, "langchain_pg_embedding"):
                logger.debug("pgvector tables not created yet, skip delete for %s", doc_id)
                return
            conn.execute(
                text(
                    "DELETE FROM langchain_pg_embedding "
                    "WHERE collection_id = (SELECT uuid FROM langchain_pg_collection WHERE name = :c) "
                    "AND cmetadata->>'document_id' = :d"
                ),
                {"c": settings.vector_collection_name, "d": doc_id},
            )
            logger.info("pgvector delete: removed vectors for document %s", doc_id)
    except Exception as e:
        logger.warning(f"pgvector delete failed for document {doc_id}: {e}")


def get_retriever(k: int = 5, user_id: str | None = None) -> VectorStoreRetriever:
    """返回检索器，支持按用户过滤。"""
    vs = get_vector_store()
    search_kwargs: dict = {"k": k}
    where = _user_where(user_id)
    if where is not None:
        search_kwargs["filter"] = where
    return vs.as_retriever(search_kwargs=search_kwargs)


def similarity_search(query: str, k: int = 5, user_id: str | None = None) -> list[Document]:
    """相似度搜索，支持按用户过滤。"""
    vs = get_vector_store()
    return vs.similarity_search(query, k=k, filter=_user_where(user_id))


def mmr_search(
    query: str, k: int = 5, fetch_k: int = 20, lambda_mult: float = 0.7, user_id: str | None = None
) -> list[Document]:
    """按 Maximal Marginal Relevance 检索——平衡相关性与多样性，支持按用户过滤。"""
    vs = get_vector_store()
    return vs.max_marginal_relevance_search(
        query, k=k, fetch_k=fetch_k, lambda_mult=lambda_mult, filter=_user_where(user_id)
    )


def similarity_search_with_relevance(
    query: str, k: int = 5, user_id: str | None = None
) -> list[tuple[Document, float]]:
    """相似度搜索，返回 (Document, relevance_score) 元组列表，支持按用户过滤。

    PGVector 的 similarity_search_with_score 返回 cosine 距离（越小越近），
    这里换算为 relevance = 1 - distance，与 Chroma 版的分数语义完全一致
    （正常相关文档落在 [0, 1] 区间，越高越相关）。
    """
    vs = get_vector_store()
    docs_and_dist = vs.similarity_search_with_score(query, k=k, filter=_user_where(user_id))
    return [(doc, 1.0 - dist) for doc, dist in docs_and_dist]
