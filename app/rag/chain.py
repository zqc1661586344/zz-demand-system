"""RAG chain — query, retrieve, generate."""

import threading

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

from app.config import settings
from app.logging_config import get_logger
from app.rag.llms import get_llm
from app.rag.vector_store import mmr_search, similarity_search_with_relevance

logger = get_logger(__name__)

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
    return "\n\n".join(parts) if parts else ""


# RAG链的模块级缓存——一次构建，跨查询复用
_rag_chain = None
_chain_lock = threading.Lock()


def build_rag_chain():
    """构建或返回RAG链缓存: prompt → LLM → output parser（线程安全）。"""
    global _rag_chain

    if _rag_chain is None:
        with _chain_lock:
            if _rag_chain is None:  # double-check
                llm = get_llm()
                _rag_chain = RunnablePassthrough() | RAG_PROMPT | llm | StrOutputParser()

    return _rag_chain


# RAG 检索为空时的"自由聊天"提示词——不依赖文档上下文，纯 LLM 自身知识回答。
FREE_CHAT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are a helpful assistant. The user's question was checked against an "
            "internal knowledge base but no relevant document was found. Answer the "
            "user's question based on your own knowledge. If you are "
            "unsure, say so honestly.\n\n"
            "Conversation history:\n{history}",
        ),
        ("human", "{question}"),
    ]
)

# 自由聊天链缓存
_free_chat_chain = None


def _build_free_chat_chain():
    """构建或返回自由聊天链缓存（线程安全）。"""
    global _free_chat_chain

    if _free_chat_chain is None:
        with _chain_lock:
            if _free_chat_chain is None:
                llm = get_llm()
                _free_chat_chain = FREE_CHAT_PROMPT | llm | StrOutputParser()

    return _free_chat_chain


def _retrieve_relevant_docs(query: str, top_k: int, user_id: str | None = None) -> list:
    """按配置的检索算法取回文档，并用相关性阈值过滤掉不相关的结果。

    返回的列表为空表示"文档中没有相关内容"，调用方应回退到自由聊天。
    """

    # 混合检索（spread判定已在hybrid_search内部完成，无需再重复查 Chroma）
    if settings.rag_search_type == "hybrid":
        logger.info("rag search type is hybrid")

        from app.rag.retrievers import hybrid_search

        return hybrid_search(query, top_k=top_k, user_id=user_id)

    # 最大边际相关性——先查再按cosine分数阈值过滤，不够的走 free chat
    elif settings.rag_search_type == "mmr":
        logger.info("rag search type is mmr")
        docs = mmr_search(query, k=top_k, user_id=user_id)
        if not docs:
            return []
        scored = similarity_search_with_relevance(query, k=len(docs), user_id=user_id)
        if scored and scored[0][1] < settings.rag_min_score:
            logger.info(
                "MMR top-1=%.3f below min_score=%.3f → reverting to free chat",
                scored[0][1],
                settings.rag_min_score,
            )
            return []
        return docs

    # 普通纯向量相关性
    else:
        scored = similarity_search_with_relevance(query, k=top_k, user_id=user_id)
        logger.info(
            "rag serach type is chroma, query=%r scores=%s threshold=%s → retained document count=%d",
            query[:50],
            [round(s, 3) for _, s in scored],
            settings.rag_min_score,
            sum(1 for _, s in scored if s >= settings.rag_min_score),
        )

        return [doc for doc, score in scored if score >= settings.rag_min_score]


def query_rag(
    query: str,
    top_k: int = 5,
    history: list[dict] | None = None,
    summary: str | None = None,
    user_id: str | None = None,
) -> dict:
    """运行完整的RAG查询: retrieve contexts, generate answer, return sources."""
    # Format history into prompt context
    history_text = format_history(history, summary=summary)

    # 检索相关文档（带相关性分数，用于阈值过滤）
    docs = _retrieve_relevant_docs(query, top_k, user_id=user_id)

    if not docs:
        # 检索为空或相关性不足 → 不走 RAG，改为纯 LLM 自由聊天（基于自身知识回答，不附带来源）。
        # 【根治】："找不到答案"提示语由前端按 free_chat 标记渲染，不进入模型输出路径，
        # 从而不会污染存库的历史消息，避免下一轮 LLM 模仿复述该提示语导致重复。
        chain = _build_free_chat_chain()
        answer = chain.invoke({"question": query, "history": history_text})
        return {
            "answer": answer,  # 纯模型回答，不含提示语
            "sources": [],
            "chunks": [],
            "free_chat": True,
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
    query: str,
    top_k: int = 5,
    history: list[dict] | None = None,
    summary: str | None = None,
    user_id: str | None = None,
):
    """流式RAG查询，逐个token产出，最后一个event包含sources和完整answer。"""
    history_text = format_history(history, summary=summary)

    # 检索相关文档（带相关性分数，用于阈值过滤）
    docs = _retrieve_relevant_docs(query, top_k, user_id=user_id)

    if not docs:
        # 提示语由前端按 free_chat 标记渲染，不进入模型输出路径
        yield {"type": "free_chat", "data": True}
        full_answer = ""
        chain = _build_free_chat_chain()
        for chunk in chain.stream({"question": query, "history": history_text}):
            full_answer += chunk
            yield {"type": "token", "data": chunk}
        yield {"type": "sources", "data": [], "full_answer": full_answer}
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
    """构建或返回summary链: prompt → LLM → output parser（线程安全）。多轮对话时用于生成摘要。"""
    global _summary_chain

    if _summary_chain is None:
        with _chain_lock:
            if _summary_chain is None:
                llm = get_llm()
                _summary_chain = SUMMARY_PROMPT | llm | StrOutputParser()

    return _summary_chain


def generate_summary(messages: list[dict]) -> str:
    """根据一系列{角色, 内容}的消息，生成一个简洁的摘要。"""
    text = format_history(messages, summary=None)
    chain = _build_summary_chain()
    return chain.invoke({"conversation": text})
