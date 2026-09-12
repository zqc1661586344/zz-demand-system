"""PGVector vector store wrapper — 业务文档 collection。

对外接口与 Chroma 版保持一致，调用方（pipeline / retrievers / chain / document_service /
main）无需改动。底层基础设施（engine / HNSW 索引 / store 工厂 / 删除 helper）统一抽取到
app.rag._pgvector_base，本文件只保留业务语义层：
  - collection = settings.vector_collection_name（"documents"）
  - metadata key = document_id（删除时）
  - 过滤逻辑 = _user_where（用户权限）
"""

from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever

from app.config import settings
from app.logging_config import get_logger
from app.rag._pgvector_base import (
    delete_by_metadata,
    ensure_hnsw_index,
    make_vector_store,
)

logger = get_logger(__name__)

_COLLECTION = settings.vector_collection_name
_METADATA_KEY_ID = "document_id"


def _user_where(user_id: str | None) -> dict | None:
    """构建用户级过滤条件：普通用户只能看到自己的私有文档 + 所有共享文档；superuser 不过滤。"""
    if user_id is None:
        return None
    return {"$or": [{"uploaded_by": {"$eq": user_id}}, {"visibility": {"$eq": "shared"}}]}


def get_vector_store():
    """返回 pgvector 向量库实例（单例，lru_cache 按 collection_name 缓存）。"""
    return make_vector_store(_COLLECTION)


def add_documents_to_store(docs: list[Document]) -> list[str]:
    """向 PG 向量库添加文档；首次写入后自动确保 HNSW 索引存在。"""
    try:
        vs = get_vector_store()
        ids = vs.add_documents(docs)
        ensure_hnsw_index()
        return ids
    except Exception as e:  # noqa: BLE001
        logger.error("pgvector add_documents failed: %s", e)
        return []


def delete_documents_from_store(doc_id: str) -> None:
    """按 document_id 元数据删除向量（幂等）。"""
    delete_by_metadata(_COLLECTION, _METADATA_KEY_ID, doc_id)


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
    try:
        vs = get_vector_store()
        return vs.similarity_search(query, k=k, filter=_user_where(user_id))
    except Exception as e:  # noqa: BLE001
        logger.error("pgvector similarity_search failed: %s", e)
        return []


def mmr_search(
    query: str, k: int = 5, fetch_k: int = 20, lambda_mult: float = 0.7, user_id: str | None = None
) -> list[Document]:
    """按 Maximal Marginal Relevance 检索——平衡相关性与多样性，支持按用户过滤。"""
    try:
        vs = get_vector_store()
        return vs.max_marginal_relevance_search(
            query, k=k, fetch_k=fetch_k, lambda_mult=lambda_mult, filter=_user_where(user_id)
        )
    except Exception as e:  # noqa: BLE001
        logger.error("pgvector mmr_search failed: %s", e)
        return []


def similarity_search_with_relevance(
    query: str, k: int = 5, user_id: str | None = None
) -> list[tuple[Document, float]]:
    """相似度搜索，返回 (Document, relevance_score) 元组列表，支持按用户过滤。

    PGVector 的 similarity_search_with_score 返回 cosine 距离（越小越近），换算为
    relevance = 1 - dist，与 Chroma 版语义一致（正常相关文档落在 [0, 1] 区间）。
    """
    try:
        vs = get_vector_store()
        docs_and_dist = vs.similarity_search_with_score(query, k=k, filter=_user_where(user_id))
        return [(doc, 1.0 - dist) for doc, dist in docs_and_dist]
    except Exception as e:  # noqa: BLE001
        logger.error("pgvector similarity_search_with_relevance failed: %s", e)
        return []
