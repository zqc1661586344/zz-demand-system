"""Chroma vector store wrapper — single collection for all documents."""

from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.vectorstores import VectorStoreRetriever

from app.config import settings
from app.rag.embeddings import get_embedding_model

COLLECTION_NAME = "documents"


@lru_cache
def get_vector_store() -> Chroma:
    """Return a Chroma vector store instance for the single document collection."""
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embedding_model(),
        persist_directory=str(settings.chroma_persist_path),
    )


def add_documents_to_store(docs: list[Document]) -> list[str]:
    """Add documents to the Chroma collection."""
    vs = get_vector_store()
    return vs.add_documents(docs)


def delete_documents_from_store(doc_ids: list[str]) -> None:
    """Delete documents from the Chroma collection by their IDs."""
    vs = get_vector_store()
    vs.delete(doc_ids)


def get_retriever(k: int = 5) -> VectorStoreRetriever:
    """Return a retriever for the single collection."""
    vs = get_vector_store()
    return vs.as_retriever(search_kwargs={"k": k})


def similarity_search(query: str, k: int = 5) -> list[Document]:
    """Run a similarity search against the single collection."""
    vs = get_vector_store()
    return vs.similarity_search(query, k=k)