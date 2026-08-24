"""混合检索器——BM25稀疏+Chroma密集+RRF融合+可选交叉编码器重新排序。

核心设计：每个用户拥有独立的 BM25 索引（含私有文档 + 全部共享文档），
superuser 使用 "__all__" 键的全量索引。

Usage:
    hybrid_search(query, top_k=5, user_id=None) → list[Document]
        chain.py中的检索入口点。
"""

import json
import threading

import jieba
from langchain_core.documents import Document

from app.config import settings
from app.logging_config import get_logger


logger = get_logger(__name__)

jieba.initialize()  # 模块加载时预热，不要等到查询


# ---------------------------------------------------------------------------
# Per-user BM25 indexes (in-memory, rebuilt from DocumentChunk on changes)
# ---------------------------------------------------------------------------
_bm25_map: dict[str, "BM25Retriever | None"] = {}  # key: user_id or "__all__"
_bm25_lock = threading.Lock()
# "加载中"标记：防止 TOCTOU 竞态下多个线程同时重建同一用户的 BM25 索引
_LOADING: "BM25Retriever | None" = object()  # type: ignore[assignment]


def _chinese_tokenizer(text: str) -> list[str]:
    """中英混合分词：对中文用 jieba 精确模式切词（词语级），英文按默认方式切分。

    BM25Retriever 默认 tokenizer 只做 lowercase + 按非字母数字字符 split，
    对中文会退化成单字（unigram）匹配，查准率低。用 jieba 后整个词语作为一个
    term 参与 BM25 的 IDF / 词频计算，显著提升中文相关性。
    """
    return [t for t in jieba.lcut(text) if t.strip()]


# ---------------------------------------------------------------------------
# Public API — per-user BM25 refresh
# ---------------------------------------------------------------------------
def refresh_bm25_for_user(user_id: str) -> None:
    """从 document_chunks 读取该用户的私有 + 全部共享文档，重建 BM25。

    Args:
        user_id: 用户ID。若传入 "__all__" 则重建全量 BM25 索引（供 superuser 使用）。
    """
    if user_id == "__all__":
        refresh_bm25_all()
        return

    from app.database import SessionLocal
    from app.models.document import Document, DocumentChunk

    db = SessionLocal()
    try:
        chunks = (
            db.query(DocumentChunk)
            .join(Document, DocumentChunk.document_id == Document.id)
            .filter(
                (Document.uploaded_by == user_id) | (Document.visibility == "shared"),
                DocumentChunk.content.isnot(None),
            )
            .all()
        )
        texts = [c.content for c in chunks]
        metadatas = [json.loads(c.meta_json) if c.meta_json else {} for c in chunks]

        _rebuild_bm25_for_key(user_id, texts, metadatas)
    finally:
        db.close()


def refresh_bm25_all() -> None:
    """从 document_chunks 读取全部文档，重建全量 BM25（供 superuser "__all__" 使用）。"""
    from app.database import SessionLocal
    from app.models.document import DocumentChunk

    db = SessionLocal()
    try:
        chunks = db.query(DocumentChunk).filter(DocumentChunk.content.isnot(None)).all()
        texts = [c.content for c in chunks]
        metadatas = [json.loads(c.meta_json) if c.meta_json else {} for c in chunks]

        _rebuild_bm25_for_key("__all__", texts, metadatas)
    finally:
        db.close()


def invalidate_other_users_bm25(except_user_id: str | None = None) -> None:
    """清空其他普通用户的 BM25 缓存 —— 共享文档变更时调用。

    上传/删除共享文档后，除了上传者（已单独刷新）和 __all__ 之外，
    其他用户的 BM25 索引仍包含旧数据，需要失效化，下次查询时通过
    get_bm25_for_user 懒加载重建。

    Args:
        except_user_id: 保留的用户 ID（通常是上传者，其 BM25 已单独刷新）
    """
    with _bm25_lock:
        for key in list(_bm25_map.keys()):
            if key == "__all__":
                continue
            if except_user_id is not None and key == except_user_id:
                continue
            _bm25_map.pop(key, None)
    logger.info(
        "Invalidated BM25 for other users (kept %s and __all__)",
        except_user_id or "none",
    )


def get_bm25_for_user(user_id: str | None) -> "BM25Retriever | None":
    """懒加载获取用户的 BM25 索引（线程安全，sentinel 防重复重建）。

    Args:
        user_id: 用户ID。None 表示 superuser（使用 "__all__" 索引）。

    Returns:
        BM25Retriever 实例，或 None（无数据/正在加载中）。
    """
    key = user_id if user_id is not None else "__all__"
    with _bm25_lock:
        if key in _bm25_map:
            cached = _bm25_map[key]
            return cached if cached is not _LOADING else None
        # 标记为"加载中"，阻止其他线程重复重建
        _bm25_map[key] = _LOADING  # type: ignore[assignment]
    # 在锁外执行 DB 重建（避免锁内 IO）
    if key == "__all__":
        refresh_bm25_all()
    else:
        refresh_bm25_for_user(key)
    with _bm25_lock:
        return _bm25_map.get(key)


def _rebuild_bm25_for_key(key: str, texts: list[str], metadatas: list[dict]) -> None:
    """线程安全地重建单个用户的 BM25 索引。"""
    from langchain_community.retrievers import BM25Retriever

    with _bm25_lock:
        if not texts:
            _bm25_map[key] = None
            logger.info("BM25 for %s: empty (no chunks)", key)
            return

        _bm25_map[key] = BM25Retriever.from_texts(
            texts,
            metadatas=metadatas,
            k=settings.rag_rerank_top_n if settings.rag_rerank_enabled else 5,
            preprocess_func=_chinese_tokenizer,
        )
        logger.info("BM25 for %s: built from %d chunks", key, len(texts))


# ---------------------------------------------------------------------------
# Backward-compatible alias for existing callers (e.g. tests)
# ---------------------------------------------------------------------------
def refresh_bm25_index_from_chroma() -> None:
    """（已废弃）向后兼容别名。新代码请直接调用 refresh_bm25_for_user。"""
    logger.warning("refresh_bm25_index_from_chroma() is deprecated, use refresh_bm25_for_user()")
    refresh_bm25_all()


# ---------------------------------------------------------------------------
# Hybrid search — Chroma dense + BM25 sparse → Ensemble (RRF fusion)
# ---------------------------------------------------------------------------
def hybrid_search(query: str, top_k: int = 5, user_id: str | None = None) -> list[Document]:
    """混合检索：Chroma稠密 + BM25稀疏 → RRF融合（通过EnsembleRetriever）。

    【相关性判定策略】
      - BM25 有数据 → 跳过 cosine spread 检查（关键词命中本身已说明相关）
      - BM25 无数据 → 回退纯稠密 + spread 检查判断 free_chat

    Args:
        query: 查询字符串
        top_k: 返回文档数量上限
        user_id: 用户ID（None=superuser，全量检索）

    Returns:
        排序后的 Document 列表，或空列表（free chat）。
    """

    from app.rag.vector_store import _user_where, get_vector_store, similarity_search_with_relevance

    # 1. 检查 BM25 是否可用（有 BM25 数据 → 跳过 spread 检查）
    sparse = get_bm25_for_user(user_id)
    if sparse is None:
        # 该用户没有 BM25 数据 → 纯稠密，做 spread 判定
        scored = similarity_search_with_relevance(query, k=min(top_k, 4), user_id=user_id)
        if not scored:
            return []
        top1, spread = scored[0][1], (scored[0][1] - scored[1][1] if len(scored) >= 2 else 1.0)
        if top1 < settings.rag_min_score or spread < settings.rag_hybrid_min_spread:
            logger.info(
                "Dense-only top-1=%.3f spread=%.3f (min=%.3f/%.3f) → free chat",
                top1, spread, settings.rag_min_score, settings.rag_hybrid_min_spread,
            )
            return []
        vs = get_vector_store()
        return vs.as_retriever(search_kwargs={
            "k": top_k, "filter": _user_where(user_id),
        }).invoke(query)[:top_k]

    # 2. BM25 有命中 → 无需 spread，直接 ensemble
    sparse.k = top_k
    vs = get_vector_store()
    dense_retriever = vs.as_retriever(search_kwargs={
        "k": top_k, "filter": _user_where(user_id),
    })

    from langchain_classic.retrievers import EnsembleRetriever

    alpha = settings.rag_hybrid_alpha
    docs = EnsembleRetriever(
        retrievers=[dense_retriever, sparse],
        weights=[alpha, 1.0 - alpha],
        c=60,
    ).invoke(query)

    # 【相关性门控】ensemble 可能因 BM25 的关键词命中而返回文档，
    # 但余弦分数仍可能很低（无关查询）。用 k=1 的 cosine 分数做最终把关，
    # 低于 rag_min_score → free chat。只多一次轻量查询，不做 spread。
    if docs:
        scored = similarity_search_with_relevance(query, k=1, user_id=user_id)
        if not scored or scored[0][1] < settings.rag_min_score:
            logger.info(
                "Hybrid BM25 path but cosine top-1=%.3f below min_score=%.3f → free chat",
                scored[0][1] if scored else 0,
                settings.rag_min_score,
            )
            return []

    reranked = _maybe_rerank(query, docs)
    return (reranked or docs)[:top_k]


# ---------------------------------------------------------------------------
# Optional cross-encoder reranker
# ---------------------------------------------------------------------------
def _maybe_rerank(query: str, docs: list[Document]) -> list[Document] | None:
    """如果重新排序器已启用且依赖关系可用，则对文档进行重新排序。返回重新排序后的前N个文档，或者当重新排序器被禁用或不可用时返回 None。"""
    if not settings.rag_rerank_enabled:
        return None

    reranker = _build_reranker()
    if reranker is None:
        return None

    from langchain_classic.retrievers import ContextualCompressionRetriever
    from langchain_community.vectorstores import InMemoryVectorStore

    from app.rag.embeddings import get_embedding_model

    try:
        im_vs = InMemoryVectorStore.from_documents(docs, get_embedding_model())
        base_retriever = im_vs.as_retriever(search_kwargs={"k": len(docs)})
        compressor = ContextualCompressionRetriever(
            base_compressor=reranker,
            base_retriever=base_retriever,
        )
        return compressor.invoke(query)[: settings.rag_rerank_top_n]
    except Exception as exc:  # noqa: BLE001 — broad catch is intentional: fall back gracefully
        logger.warning("reranker failed, falling back to unranked results: %s", exc)
        return None


_built_reranker = None
_reranker_lock = threading.Lock()


def _build_reranker():
    """延迟构建并缓存跨编码器压缩器（线程安全）。"""
    global _built_reranker
    if _built_reranker is not None:
        return _built_reranker if _built_reranker is not False else None

    with _reranker_lock:
        # double-check
        if _built_reranker is not None:
            return _built_reranker if _built_reranker is not False else None

        try:
            from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
            from langchain_community.cross_encoders import HuggingFaceCrossEncoder

            model = HuggingFaceCrossEncoder(model_name=settings.rag_rerank_model)
            _built_reranker = CrossEncoderReranker(model=model, top_n=settings.rag_rerank_top_n)
            logger.info(
                "Reranker loaded: %s (top_n=%s)",
                settings.rag_rerank_model,
                settings.rag_rerank_top_n,
            )
            return _built_reranker
        except ImportError:
            logger.warning("transformers / torch not installed — reranker unavailable")
            _built_reranker = False  # sentinel: don't retry every call
            return None
        except Exception as exc:
            logger.warning("Failed to load reranker model: %s", exc)
            _built_reranker = False
            return None
