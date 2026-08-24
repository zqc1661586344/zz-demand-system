"""Chroma vector store wrapper — single collection for all documents."""

from functools import lru_cache

from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever

from app.config import settings
from app.logging_config import get_logger
from app.rag.embeddings import get_embedding_model

logger = get_logger(__name__)


@lru_cache
def get_vector_store() -> Chroma:
    """返回Chroma向量存储实例。

    这里显式关闭 Chroma 匿名遥测（anonymized_telemetry=False）：
    Chroma 默认会上报 telemetry，其内部用 posthog SDK 发 HTTP（httpx）请求，
    既产生噪音日志（httpx POST api.edgefn.net），又可能因 SDK 版本不兼容
    抛错并被 chromadb.telemetry.product.posthog 打印 ERROR（capture() takes 1
    positional argument...）。从源头关掉整条链路最干净，也无需为每个组件单独降噪。
    """
    return Chroma(
        collection_name=settings.chroma_collection_name,
        embedding_function=get_embedding_model(),
        persist_directory=str(settings.chroma_persist_path),
        # 关闭匿名遥测，从源头消除 posthog/httpx 噪音
        client_settings=ChromaSettings(anonymized_telemetry=False),
        # 固定用余弦度量（cosine）+ 显式 cosine relevance 换算函数，
        # 使 relevance_score 语义统一为 1 - cosine_distance（越高越相关），
        # 且不依赖从索引遗留配置解析度量（可避开 _select_relevance_score_fn 抛错）。
        # 【注意】该度量只在"创建新 collection"时生效——旧索引需删除重建：
        #   rm -rf data/chroma
        collection_metadata={"hnsw:space": "cosine"},
        relevance_score_fn=lambda distance: 1.0 - distance,
    )


def add_documents_to_store(docs: list[Document]) -> list[str]:
    """向Chroma collection中添加文档。"""
    vs = get_vector_store()
    return vs.add_documents(docs)


def delete_documents_from_store(doc_id: str) -> None:
    """根据document_id元数据从Chroma collection中删除所有相关向量。

    由于 add_documents_to_store 在入库时已将 document_id 写入每个 chunk 的 metadata，
    这里通过 where 过滤条件精准删除该文档的全部向量，无需事先保存 Chroma 内部 ID。

    pipeline.py 的 process_document 在重新处理前也应调用此函数，避免重复累积。
    """
    try:
        vs = get_vector_store()
        logger.info(f"Chroma delete: removing vectors with where={{document_id: {doc_id!r}}}")
        vs.delete(where={"document_id": {"$eq": doc_id}})
        logger.info(f"Chroma delete: successfully removed vectors for document {doc_id}")
    except Exception as e:
        logger.exception(f"Chroma delete failed for document {doc_id}: {e}")


def get_retriever(k: int = 5) -> VectorStoreRetriever:
    """为单个collection返回一个检索器。"""
    vs = get_vector_store()
    return vs.as_retriever(search_kwargs={"k": k})


def similarity_search(query: str, k: int = 5) -> list[Document]:
    """对单个collection进行相似度搜索。"""
    vs = get_vector_store()
    return vs.similarity_search(query, k=k)


def mmr_search(
    query: str, k: int = 5, fetch_k: int = 20, lambda_mult: float = 0.7
) -> list[Document]:
    """按 Maximal Marginal Relevance 检索——平衡相关性与多样性。"""
    vs = get_vector_store()
    return vs.max_marginal_relevance_search(
        query, k=k, fetch_k=fetch_k, lambda_mult=lambda_mult
    )


def similarity_search_with_relevance(query: str, k: int = 5) -> list[tuple[Document, float]]:
    """相似度搜索，返回 (Document, relevance_score) 元组列表。

    relevance_score 由 get_vector_store 里显式指定的 cosine 换算函数计算：
    score = 1 - cosine_distance，值域约 [0, 2]，越高越相关（>1 表示极强相关，
    正常相关文档通常在 [0, 1] 区间）。由调用方按 rag_min_score 阈值判断是否采用。
    """
    vs = get_vector_store()
    # relevance_score_fn 已在 Chroma 构造时显式给定，因此无论集合实际度量如何，
    # 这里都会用同一换算逻辑，不会因旧 l2 索引而错乱。
    return vs.similarity_search_with_relevance_scores(query, k=k)
