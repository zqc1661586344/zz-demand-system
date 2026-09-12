"""法规向量库 wrapper — 独立 PGVector collection=compliance_regulations。

对外 API：add_regulations_to_store / delete_regulations_from_store /
get_regulation_vector_store / search_regulations / _build_filter。
底层基础设施（engine / HNSW / store 工厂 / delete helper）统一走
app.rag._pgvector_base，与业务文档 collection 共用同一张 langchain_pg_embedding 表
和同一个 HNSW 索引，消除了之前的 DRY 违反。
"""

from langchain_core.documents import Document

from app.config import settings
from app.rag._pgvector_base import (
    delete_by_metadata,
    ensure_hnsw_index,
    make_vector_store,
)

_COLLECTION = settings.compliance_vector_collection
_METADATA_KEY_ID = "regulation_id"


def get_regulation_vector_store():
    """返回法规 PGVector 实例（单例，按 collection_name 缓存）。"""
    return make_vector_store(_COLLECTION)


def add_regulations_to_store(docs: list[Document]) -> list[str]:
    """将法规条款文档写入法规向量库，首次写入后自动确保 HNSW 索引存在。"""
    vs = get_regulation_vector_store()
    ids = vs.add_documents(docs)
    ensure_hnsw_index()
    return ids


def delete_regulations_from_store(regulation_id: str) -> None:
    """按 metadata.regulation_id 删除某部法规的全部条款向量（幂等）。"""
    delete_by_metadata(_COLLECTION, _METADATA_KEY_ID, regulation_id)


def _build_filter(metadata: dict | None) -> dict | None:
    """把平铺的 metadata 过滤键转成 langchain-postgres 的 filter 结构。"""
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
    """法规语义检索，返回 (Document, score) 元组列表，score ∈ [0,1] 越高越相关。"""
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
