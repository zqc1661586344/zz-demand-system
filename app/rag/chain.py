"""RAG chain — query, retrieve, generate."""

import logging

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough

from app.config import settings
from app.rag.llms import get_llm
from app.rag.vector_store import mmr_search, similarity_search_with_relevance

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
    """构建或返回自由聊天链缓存（无文档上下文，纯 LLM 回答）。"""
    global _free_chat_chain

    if _free_chat_chain is None:
        llm = get_llm()
        _free_chat_chain = FREE_CHAT_PROMPT | llm | StrOutputParser()

    return _free_chat_chain


def _retrieve_relevant_docs(query: str, top_k: int) -> list:
    """按配置的检索算法取回文档，并用相关性阈值过滤掉不相关的结果。

    返回的列表为空表示"文档中没有相关内容"，调用方应回退到自由聊天。
    """
    if settings.rag_search_type == "hybrid":
        from app.rag.retrievers import hybrid_search

        docs = hybrid_search(query, top_k=top_k)
        if docs:
            # 后置过滤：hybrid 的 RRF 分数不是相似度语义，不可直接阈值过滤，改用
            # 稠密检索的真实相似度分数来判定 query 是否真的命中了文档。
            # bge-m3 对不相关 query 也可能打出较高的绝对分数（紧凑分布），因此
            # 用「绝对阈值 + 分数离散度」两个判据：top-1 过低，或与 top-2 的差距
            # 太小（无区分度、呈平带），都视为未命中，回退 free chat。
            scored = similarity_search_with_relevance(query, k=2)
            if scored:
                top1 = scored[0][1]
                if len(scored) < 2:
                    spread = 1.0
                else:
                    spread = top1 - scored[1][1]
                if top1 < settings.rag_min_score or spread < settings.rag_hybrid_min_spread:
                    logger.info(
                        "Hybrid top-1=%.3f spread=%.3f (min_score=%.3f, min_spread=%.3f) "
                        "→ 判定未命中，回退自由聊天",
                        top1,
                        spread,
                        settings.rag_min_score,
                        settings.rag_hybrid_min_spread,
                    )
                    return []
        return docs

    if settings.rag_search_type == "mmr":
        # MMR 不直接返回距离分，此处保持与阈值无关（文档非空即视为相关）。
        return mmr_search(query, k=top_k)

    scored = similarity_search_with_relevance(query, k=top_k)
    # 诊断日志：打印本次检索的实际相关性分数，便于判断阈值是否合理、
    # 或是否因旧的 l2 索引导致分数失真（分数远超出 [0,1] 即说明索引不是 cosine）。
    logger.info(
        "检索 query=%r scores=%s 阈值=%s → 保留文档数=%d",
        query[:50],
        [round(s, 3) for _, s in scored],
        settings.rag_min_score,
        sum(1 for _, s in scored if s >= settings.rag_min_score),
    )
    # 相关性分数低于阈值的认定为"文档中找不到相关内容"
    return [doc for doc, score in scored if score >= settings.rag_min_score]


def query_rag(
    query: str, top_k: int = 5, history: list[dict] | None = None, summary: str | None = None
) -> dict:
    """运行完整的RAG查询: retrieve contexts, generate answer, return sources."""
    # Format history into prompt context
    history_text = format_history(history, summary=summary)

    # 检索相关文档（带相关性分数，用于阈值过滤）
    docs = _retrieve_relevant_docs(query, top_k)

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
    query: str, top_k: int = 5, history: list[dict] | None = None, summary: str | None = None
):
    """流式RAG查询，逐个token产出，最后一个event包含sources和完整answer。"""
    history_text = format_history(history, summary=summary)

    docs = _retrieve_relevant_docs(query, top_k)

    if not docs:
        # 【根治】：提示语由前端按 free_chat 标记渲染，不进入模型输出路径
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
