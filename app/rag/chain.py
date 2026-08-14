"""RAG chain — query, retrieve, generate."""

import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

from app.rag.embeddings import get_llm
from app.rag.vector_store import similarity_search

logger = logging.getLogger(__name__)

# Default RAG prompt template
RAG_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant for internal knowledge base queries. "
        "Use the following context to answer the user's question. "
        "If you don't know the answer based on the context, say so clearly. "
        "Always cite the source document names in your answer.\n\n"
        "Context:\n{context}",
    ),
    ("human", "{question}"),
])


def format_sources(docs: list) -> list[dict]:
    """Extract source metadata from retrieved documents."""
    seen = set()
    sources = []
    for doc in docs:
        filename = doc.metadata.get("filename", "Unknown")
        page = doc.metadata.get("page", None)
        key = f"{filename}:{page}" if page else filename
        if key not in seen:
            seen.add(key)
            entry = {"filename": filename}
            if page is not None:
                entry["page"] = page
            sources.append(entry)
    return sources


def format_context(docs: list) -> str:
    """Format retrieved documents into a context string."""
    context_parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("filename", "Unknown")
        context_parts.append(f"[Source {i}: {source}]\n{doc.page_content}")
    return "\n\n".join(context_parts)


def build_rag_chain():
    """Build the full RAG chain (retrieve → prompt → LLM → output)."""
    llm = get_llm()

    chain = (
        RunnablePassthrough()
        | RAG_PROMPT
        | llm
        | StrOutputParser()
    )

    return chain


def query_rag(query: str, top_k: int = 5) -> dict:
    """Run a full RAG query: retrieve contexts, generate answer, return sources."""
    # Retrieve relevant documents
    docs = similarity_search(query, k=top_k)

    if not docs:
        return {
            "answer": "No relevant documents found.",
            "sources": [],
            "chunks": [],
        }

    # Format context and sources
    context = format_context(docs)
    sources = format_sources(docs)

    # Build and invoke chain
    chain = build_rag_chain()
    answer = chain.invoke({"context": context, "question": query})

    return {
        "answer": answer,
        "sources": sources,
        "chunks": [{"content": d.page_content, "metadata": d.metadata} for d in docs],
    }