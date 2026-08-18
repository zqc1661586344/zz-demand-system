"""RAG chain — query, retrieve, generate."""

import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

from app.rag.llms import get_llm
from app.rag.vector_store import similarity_search

logger = logging.getLogger(__name__)

# RAG提示词模板
RAG_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful assistant for internal knowledge base queries. "
            "Use the following context to answer the user's question. "
            "If you don't know the answer based on the context, say so clearly. "
            "Always cite the source document names in your answer.\n\n"
            "Context:\n{context}\n\n"
            "Conversation history:\n{history}",
        ),
        ("human", "{question}"),
    ]
)


def format_sources(docs: list) -> list[dict]:
    """从检索到的文档中提取源元数据。"""
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
    """将检索到的文档格式化为上下文字符串。"""
    context_parts = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("filename", "Unknown")
        context_parts.append(f"[Source {i}: {source}]\n{doc.page_content}")
    return "\n\n".join(context_parts)


def format_history(messages: list[dict] | None = None, summary: str | None = None) -> str:
    """将对话历史格式化为可读的字符串，并可选择添加摘要前缀。"""
    parts = []
    if summary:
        parts.append(f"[Summary of earlier conversation]\n{summary}")
    if messages:
        lines = []
        for m in messages:
            role = "User" if m["role"] == "user" else "Assistant"
            lines.append(f"{role}: {m['content']}")
        if summary:
            parts.append("[Recent messages]")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else "(no prior conversation)"


# RAG链的模块级缓存——一次构建，跨查询复用
_rag_chain = None


def build_rag_chain():
    """构建或返回RAG链缓存: prompt → LLM → output parser."""
    global _rag_chain

    if _rag_chain is None:
        llm = get_llm()
        _rag_chain = RunnablePassthrough() | RAG_PROMPT | llm | StrOutputParser()

    return _rag_chain


def query_rag(
    query: str, top_k: int = 5, history: list[dict] | None = None, summary: str | None = None
) -> dict:
    """运行完整的RAG查询: retrieve contexts, generate answer, return sources."""
    # Format history into prompt context
    history_text = format_history(history, summary=summary)

    # 检索相关文档
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
    answer = chain.invoke({"context": context, "question": query, "history": history_text})

    return {
        "answer": answer,
        "sources": sources,
        "chunks": [{"content": d.page_content, "metadata": d.metadata} for d in docs],
    }


def query_rag_stream(
    query: str, top_k: int = 5, history: list[dict] | None = None, summary: str | None = None
):
    """流式RAG查询，逐个token产出，最后一个event包含sources和完整answer。"""
    history_text = format_history(history, summary=summary)

    docs = similarity_search(query, k=top_k)

    if not docs:
        yield {"type": "token", "data": "No relevant documents found."}
        yield {"type": "sources", "data": [], "full_answer": "No relevant documents found."}
        return

    context = format_context(docs)
    sources = format_sources(docs)
    chain = build_rag_chain()

    full_answer = ""
    for chunk in chain.stream({"context": context, "question": query, "history": history_text}):
        full_answer += chunk
        yield {"type": "token", "data": chunk}

    yield {"type": "sources", "data": sources, "full_answer": full_answer}


# ---------- Conversation summarization ----------

# 总结摘要提示词
SUMMARY_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are an expert at conversation summarization. "
            "Read the following conversation between a User and an Assistant, "
            "and produce a concise summary that captures all key information: "
            "facts the user has mentioned, questions asked, and answers given. "
            "Keep the summary to 3-5 sentences.",
        ),
        ("human", "{conversation}"),
    ]
)


_summary_chain = None


def _build_summary_chain():
    """构建或返回summary链: prompt → LLM → output parser。多轮对话时用于生成摘要。"""
    global _summary_chain

    if _summary_chain is None:
        llm = get_llm()
        _summary_chain = SUMMARY_PROMPT | llm | StrOutputParser()

    return _summary_chain


def generate_summary(messages: list[dict]) -> str:
    """根据一系列{角色, 内容}的消息，生成一个简洁的摘要。"""
    text = format_history(messages, summary=None)
    chain = _build_summary_chain()
    return chain.invoke({"conversation": text})
