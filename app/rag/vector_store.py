"""Chroma vector store wrapper — single collection for all documents."""

from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever

from app.config import settings
from app.rag.embeddings import get_embedding_model


@lru_cache
def get_vector_store() -> Chroma:
    """返回Chroma向量存储实例。"""
    return Chroma(
        collection_name=settings.chroma_collection_name,
        embedding_function=get_embedding_model(),
        persist_directory=str(settings.chroma_persist_path),
    )


def add_documents_to_store(docs: list[Document]) -> list[str]:
    """向Chroma collection中添加文档。"""
    vs = get_vector_store()
    return vs.add_documents(docs)


def delete_documents_from_store(doc_ids: list[str]) -> None:
    """根据ID从Chroma collection中删除文档。"""
    vs = get_vector_store()
    vs.delete(doc_ids)


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
