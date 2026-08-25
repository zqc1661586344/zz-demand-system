"""混合检索器——BM25稀疏+Chroma密集+RRF融合+可选交叉编码器重新排序。

核心设计：每个用户拥有独立的 BM25 索引（含私有文档 + 全部共享文档），
superuser 使用 "__all__" 键的全量索引。

Usage:
    hybrid_search(query, top_k=5, user_id=None) → list[Document]
        chain.py中的检索入口点。
"""

import json
import time
import threading

import jieba
from langchain_core.documents import Document

from app.config import settings
from app.logging_config import get_logger


logger = get_logger(__name__)

# 模块加载时预热，不要等到查询时才加载，否则第一次查询会非常慢
jieba.initialize()


# ---------------------------------------------------------------------------
# Per-user BM25 indexes (in-memory, rebuilt from DocumentChunk on changes)
# ---------------------------------------------------------------------------
_bm25_map: dict[str, "BM25Retriever | None"] = {}  # key: user_id or "__all__"
_bm25_ts_map: dict[str, float] = {}  # Redis timestamp snapshot per key
_bm25_lock = threading.Lock()
_BM25_LRU_MAX = 500  # 进程内 LRU 缓存上限，防止内存无限增长
# "加载中"标记：防止 TOCTOU 竞态下多个线程同时重建同一用户的 BM25 索引
_LOADING: "BM25Retriever | None" = object()  # type: ignore[assignment]


def _evict_lru() -> None:
    """当 _bm25_map 达到上限时，淘汰一个旧条目。

    策略：弹出第一个键（Python 3.7+ dict 保持插入顺序）。
    不淘汰 _LOADING 条目，因为重建中的 key 应该完成。
    """
    with _bm25_lock:
        while len(_bm25_map) >= _BM25_LRU_MAX:
            for k in list(_bm25_map.keys()):
                if _bm25_map[k] is not _LOADING:
                    _bm25_map.pop(k, None)
                    _bm25_ts_map.pop(k, None)
                    break
            else:
                # 全是 _LOADING 条目——不应发生，但以防万一直接返回
                break


def _chinese_tokenizer(text: str) -> list[str]:
    """中英混合分词：对中文用jieba精确模式切词（词语级），英文按默认方式切分。

    BM25Retriever默认tokenizer只做lowercase + 按非字母数字字符 split，对中文会退化成单字（unigram）匹配，查准率低。用jieba后整个词语作为一个term参与BM25的IDF/词频计算，显著提升中文相关性。
    """
    return [t for t in jieba.lcut(text) if t.strip()]


def _redis_ts_key(user_key: str) -> str:
    """Redis key for BM25 rebuild timestamp."""
    return f"bm25:ts:{user_key}"


def _update_redis_ts(user_key: str) -> None:
    """更新 Redis 中该用户的 BM25 时间戳（原子操作）。"""
    from app.cache.redis_client import get_redis_client

    r = get_redis_client()
    if r is not None:
        try:
            r.setex(_redis_ts_key(user_key), settings.redis_bm25_cache_ttl_seconds, time.time())
        except Exception:
            logger.debug("Failed to update Redis BM25 ts for %s", user_key)


def _invalidate_redis_ts(user_key: str) -> None:
    """删除 Redis 中该用户的 BM25 时间戳，使其立即过期。"""
    from app.cache.redis_client import get_redis_client

    r = get_redis_client()
    if r is not None:
        try:
            r.delete(_redis_ts_key(user_key))
        except Exception:
            pass


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
        _update_redis_ts(user_id)
    finally:
        db.close()


def refresh_bm25_all() -> None:
    """从document_chunks读取全部文档，重建全量 BM25（供 superuser "__all__" 使用）。"""
    from app.database import SessionLocal
    from app.models.document import DocumentChunk

    db = SessionLocal()
    try:
        chunks = db.query(DocumentChunk).filter(DocumentChunk.content.isnot(None)).all()
        texts = [c.content for c in chunks]
        metadatas = [json.loads(c.meta_json) if c.meta_json else {} for c in chunks]

        _rebuild_bm25_for_key("__all__", texts, metadatas)
        _update_redis_ts("__all__")
    finally:
        db.close()


def invalidate_other_users_bm25(except_user_id: str | None = None) -> None:
    """清空其他普通用户的BM25缓存 —— 共享文档变更时调用。

    上传/删除共享文档后，除了上传者（已单独刷新）之外，其他用户的BM25索引以及superuser的全量索引（__all__）都需失效化，下次查询时通过get_bm25_for_user懒加载重建。

    Args:
        except_user_id: 保留的用户ID（通常是上传者，其BM25已单独刷新）
    """
    with _bm25_lock:
        for key in list(_bm25_map.keys()):
            if except_user_id is not None and key == except_user_id:
                continue
            _bm25_map.pop(key, None)
            _bm25_ts_map.pop(key, None)
            _invalidate_redis_ts(key)
    logger.info(
        "invalidated BM25 for other users (kept %s)",
        except_user_id or "none",
    )


def get_bm25_for_user(user_id: str | None) -> "BM25Retriever | None":
    """懒加载获取用户的 BM25 索引（线程安全，sentinel 防重复重建）。

    跨 worker 一致性模式：
      - 如果 Redis 可用，比较本地 _bm25_ts_map 与 Redis 时间戳。
        Redis 时间戳更新 → 本地缓存失效并重建。
      - 如果 Redis 不可用，回退纯本地缓存模式（单进程正确）。
      - 如果 rag_bm25_cache_bypass=True，每次都从 DB 重建（最慢但最正确）。

    Args:
        user_id: 用户ID。None 表示 superuser（使用 "__all__" 索引）。

    Returns:
        BM25Retriever 实例，或 None（无数据/正在加载中）。
    """
    key = user_id if user_id is not None else "__all__"

    # 多 worker 绕过模式：每次都从 DB 读取，不缓存（正确但较慢）
    if settings.rag_bm25_cache_bypass:
        if key == "__all__":
            refresh_bm25_all()
        else:
            refresh_bm25_for_user(key)
        with _bm25_lock:
            cached = _bm25_map.get(key)
            return cached if cached is not _LOADING else None

    # ---- Redis 新鲜度检查 ----
    redis_ts: float | None = None
    if settings.celery_broker_url:
        from app.cache.redis_client import get_redis_client

        r = get_redis_client()
        if r is not None:
            try:
                ts_str = r.get(_redis_ts_key(key))
                if ts_str is not None:
                    redis_ts = float(ts_str)
            except Exception:
                pass

    with _bm25_lock:
        if redis_ts is not None:
            # Redis 模式下：比较本地快照与 Redis 时间戳
            local_ts = _bm25_ts_map.get(key, 0.0)
            if key in _bm25_map and _bm25_map[key] is not _LOADING and local_ts >= redis_ts:
                return _bm25_map[key]
        else:
            # 无 Redis：纯本地缓存模式
            if key in _bm25_map:
                cached = _bm25_map[key]
                return cached if cached is not _LOADING else None

        # 缓存缺失 / 过期 → 标记为加载中（在锁内，防止竞态）
        _bm25_map[key] = _LOADING  # type: ignore[assignment]

    # ---- 锁外重建（避免锁内 IO） ----
    try:
        if key == "__all__":
            refresh_bm25_all()
        else:
            refresh_bm25_for_user(key)
    except Exception:
        logger.warning("BM25 rebuild failed for %s", key, exc_info=True)
        with _bm25_lock:
            _bm25_map.pop(key, None)
            _bm25_ts_map.pop(key, None)
        return None

    with _bm25_lock:
        return _bm25_map.get(key)


def _rebuild_bm25_for_key(key: str, texts: list[str], metadatas: list[dict]) -> None:
    """线程安全地重建单个用户的 BM25 索引。"""
    from langchain_community.retrievers import BM25Retriever

    with _bm25_lock:
        if not texts:
            _bm25_map[key] = None
            _bm25_ts_map[key] = time.time()  # 记录空索引时间戳
            logger.info("BM25 for %s: empty (no chunks)", key)
            return

        _evict_lru()  # 插入前触发 LRU 淘汰

        _bm25_map[key] = BM25Retriever.from_texts(
            texts,
            metadatas=metadatas,
            k=settings.rag_rerank_top_n if settings.rag_rerank_enabled else 5,
            preprocess_func=_chinese_tokenizer,
        )
        _bm25_ts_map[key] = time.time()  # 记录本地时间戳
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
                "dense-only top-1=%.3f spread=%.3f (min=%.3f/%.3f) → free chat",
                top1,
                spread,
                settings.rag_min_score,
                settings.rag_hybrid_min_spread,
            )
            return []
        vs = get_vector_store()
        return vs.as_retriever(
            search_kwargs={
                "k": top_k,
                "filter": _user_where(user_id),
            }
        ).invoke(query)[:top_k]

    # 2. BM25 有命中 → 无需 spread，直接 ensemble
    sparse.k = top_k
    vs = get_vector_store()
    dense_retriever = vs.as_retriever(
        search_kwargs={
            "k": top_k,
            "filter": _user_where(user_id),
        }
    )

    from langchain_classic.retrievers import EnsembleRetriever

    alpha = settings.rag_hybrid_alpha
    docs = EnsembleRetriever(
        retrievers=[dense_retriever, sparse],
        weights=[alpha, 1.0 - alpha],
        c=60,
    ).invoke(query)

    # 【相关性门控】ensemble可能因BM25的关键词命中而返回文档，但余弦分数仍可能很低（无关查询）。用k=1的 cosine 分数做最终把关，低于rag_min_score → free chat，只多一次轻量查询，不做 spread。
    if docs:
        scored = similarity_search_with_relevance(query, k=1, user_id=user_id)
        if not scored or scored[0][1] < settings.rag_min_score:
            logger.info(
                "hybrid BM25 path but cosine top-1=%.3f below min_score=%.3f → free chat",
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
    """如果重新排序器已启用且依赖关系可用，则对文档进行重新排序。

    返回重新排序后的前N个文档，或者当重新排序器被禁用或不可用时返回 None。
    """
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
                "reranker loaded: %s (top_n=%s)",
                settings.rag_rerank_model,
                settings.rag_rerank_top_n,
            )
            return _built_reranker
        except ImportError:
            logger.warning("transformers/torch not installed — reranker unavailable")
            _built_reranker = False  # sentinel: don't retry every call
            return None
        except Exception as exc:
            logger.warning("failed to load reranker model: %s", exc)
            _built_reranker = False
            return None
