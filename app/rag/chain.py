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

# RAG提示词模板（中文，适配中文文档/中文问答场景）
RAG_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是一个知识库问答助手。请根据以下上下文回答用户问题。"
            "如果上下文不足以回答问题，请如实说明，不要编造。回答时请标注信息来源。\n\n"
            "上下文：\n{context}\n\n"
            "对话历史：\n{history}",
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
            "你是一个通用问答助手。用户的问题未能在知识库中找到相关文档，"
            "请基于你自身的知识回答。如果不确定，请如实说明。\n\n"
            "对话历史：\n{history}",
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


# 查询改写提示词：将多轮对话中的指代/省略问题改写为独立查询
CONTEXTUALIZE_Q_SYSTEM = (
    "给定以下对话历史和用户最新问题，请将用户问题改写为一个独立完整的查询，"
    "使其在不看对话历史的情况下也能被理解。如果无需改写，请原样返回。"
)


def _rewrite_query(query: str, history: list[dict] | None) -> str:
    """将当前问题结合历史改写为独立查询；失败或无历史时返回原问题。"""
    if not history:
        return query
    try:
        messages: list[tuple[str, str]] = [("system", CONTEXTUALIZE_Q_SYSTEM)]
        for msg in history:
            role = "user" if msg.get("role") == "user" else "assistant"
            messages.append((role, msg.get("content", "")))
        messages.append(("human", query))
        prompt = ChatPromptTemplate.from_messages(messages)
        chain = prompt | get_llm() | StrOutputParser()
        rewritten = chain.invoke({})
        return (rewritten or query).strip() or query
    except Exception:
        logger.warning("query rewrite failed, falling back to original query", exc_info=True)
        return query


def _retrieve_relevant_docs(
    query: str, top_k: int, user_id: str | None = None, history: list[dict] | None = None
) -> list:
    """按配置的检索算法取回文档，并用相关性阈值过滤掉不相关的结果。

    返回的列表为空表示"文档中没有相关内容"，调用方应回退到自由聊天。
    """
    # 多轮对话：先改写为独立查询，再用于检索
    query = _rewrite_query(query, history)

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
        if not scored:
            # cosine 无任何命中 → MMR 的多样性结果不能作为回答依据，回退 free chat
            logger.info("MMR returned %d docs but cosine found no matches → free chat", len(docs))
            return []
        if scored[0][1] < settings.rag_min_score:
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
    docs = _retrieve_relevant_docs(query, top_k, user_id=user_id, history=history)

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
    docs = _retrieve_relevant_docs(query, top_k, user_id=user_id, history=history)

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
            (
                "You are an expert at conversation summarization. "
                "Read the following conversation between a User and an Assistant, "
                "and produce a concise summary that captures all key information: "
                "facts the user has mentioned, questions asked, and answers given. "
                "Keep the summary to 3-5 sentences."
            ),
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
