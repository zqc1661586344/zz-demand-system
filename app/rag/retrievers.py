"""混合检索器——BM25稀疏+Chroma密集+RRF融合+可选交叉编码器重新排序。

Usage:
    hybrid_search(query, top_k=5) → list[Document]
        chain.py中的检索入口点。
        根据配置，使用similarity_search、mmr_search或混合（BM25 + RRF）算法。

    refresh_bm25_index_from_chroma()
        根据Chroma的完整区块内容重建BM25索引。
        在文档上传或删除后调用。
"""

from app.logging_config import get_logger

import jieba
from langchain_core.documents import Document

from app.config import settings
from app.rag.vector_store import get_vector_store, similarity_search_with_relevance


logger = get_logger(__name__)

jieba.initialize()  # 模块加载时预热，不要等到查询


def _chinese_tokenizer(text: str) -> list[str]:
    """中英混合分词：对中文用 jieba 精确模式切词（词语级），英文按默认方式切分。

    BM25Retriever 默认 tokenizer 只做 lowercase + 按非字母数字字符 split，
    对中文会退化成单字（unigram）匹配，查准率低。用 jieba 后整个词语作为一个
    term 参与 BM25 的 IDF / 词频计算，显著提升中文相关性。
    """
    # jieba.lcut 对中文精确分词；对纯英文串 jieba 会原样返回（作为一个 token）。
    # 这里对每个 token 再走一次 default 的前处理仅作边界处理，无需过度复杂。
    return [t for t in jieba.lcut(text) if t.strip()]


# ---------------------------------------------------------------------------
# Global BM25 retriever (in-memory, rebuilt from Chroma on changes)
# ---------------------------------------------------------------------------
_bm25_retriever = None  # BM25Retriever instance


def get_bm25_retriever():
    """BM25检索器支持延迟访问；首次调用时，从Chroma自动刷新。"""
    global _bm25_retriever
    if _bm25_retriever is None:
        _refresh_bm25_from_chroma()
    return _bm25_retriever


def refresh_bm25_index_from_chroma() -> None:
    """重新读取Chroma中的所有数据块，并重建BM25索引。"""
    _refresh_bm25_from_chroma()
    n = _bm25_retriever.docs if _bm25_retriever else None
    logger.info("BM25 index refreshed from Chroma (%d chunks)", len(n) if n else 0)


def _refresh_bm25_from_chroma() -> None:
    """内部任务：通过从Chroma中提取文本和元数据来重建_bm25_检索器。"""
    global _bm25_retriever

    from langchain_community.retrievers import BM25Retriever

    try:
        vs = get_vector_store()
        data = vs.get()  # {"documents": [...], "metadatas": [...], "ids": [...]}
        texts = data.get("documents", []) or []
        metadatas = data.get("metadatas", []) or []

        if not texts:
            logger.info("Chroma 为空 — BM25 索引置为 None")
            _bm25_retriever = None
            return

        _bm25_retriever = BM25Retriever.from_texts(
            texts,
            metadatas=metadatas,
            k=settings.rag_rerank_top_n if settings.rag_rerank_enabled else 5,
            preprocess_func=_chinese_tokenizer,
        )
        logger.info("BM25 index built from %d chunks", len(texts))
    except Exception as exc:
        logger.warning("BM25 index refresh failed: %s", exc)
        _bm25_retriever = None


# ---------------------------------------------------------------------------
# Hybrid search — Chroma dense + BM25 sparse → Ensemble (RRF fusion)
# ---------------------------------------------------------------------------
def hybrid_search(query: str, top_k: int = 5) -> list[Document]:
    """混合检索：Chroma稠密 + BM25稀疏 → RRF融合（通过EnsembleRetriever）。

    返回按RRF分数降序排序的文档。无阈值过滤。已应用（RRF分数并非相似性语义）。结果数量为限制在*top_k*以内。
    当`rag_search_type != "hybrid"`时，将回退到纯密集（相似度/最大相关度）。
    """
    if settings.rag_search_type != "hybrid":
        # Backward-compatible fallback to non-hybrid modes
        from app.rag.vector_store import mmr_search

        if settings.rag_search_type == "mmr":
            return mmr_search(query, k=top_k)

        scored = similarity_search_with_relevance(query, k=top_k)
        return [doc for doc, score in scored if score >= settings.rag_min_score]

    from langchain_classic.retrievers import EnsembleRetriever

    # 1. Dense retriever from Chroma
    vs = get_vector_store()
    dense_retriever = vs.as_retriever(search_kwargs={"k": top_k})

    # 2. Sparse retriever (BM25)
    sparse = get_bm25_retriever()
    if sparse is None:
        # Chroma 为空（例如所有文档被删除后）→ 无 BM25 可融合，回退到纯稠密检索
        return dense_retriever.invoke(query)[:top_k]
    sparse.k = top_k

    # 3. Ensemble with weighted RRF
    alpha = settings.rag_hybrid_alpha
    ensemble = EnsembleRetriever(
        retrievers=[dense_retriever, sparse],
        weights=[alpha, 1.0 - alpha],
        c=60,
    )

    docs = ensemble.invoke(query)

    # 可选项3，可以执行rerank
    reranked = _maybe_rerank(query, docs)
    if reranked is not None:
        return reranked

    return docs[:top_k]


# ---------------------------------------------------------------------------
# Optional cross-encoder reranker
# ---------------------------------------------------------------------------
def _maybe_rerank(query: str, docs: list[Document]) -> list[Document] | None:
    """如果重新排序器已启用且依赖关系可用，则对*文档*进行重新排序。

    返回重新排序后的前N个文档，或者当重新排序器被禁用或不可用时返回None。
    """
    if not settings.rag_rerank_enabled:
        return None

    reranker = _build_reranker()
    if reranker is None:
        return None

    from langchain_classic.retrievers import ContextualCompressionRetriever

    # Use a thin InMemoryVectorStore to let the compressor work over *docs*
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
    except Exception as exc:
        logger.warning("Reranker failed, falling back to unranked results: %s", exc)
        return None


_built_reranker = None


def _build_reranker():
    """延迟构建并缓存跨编码器压缩器。"""
    global _built_reranker
    if _built_reranker is not None:
        return _built_reranker

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
