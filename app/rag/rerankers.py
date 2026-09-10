"""Reranker configuration — local HuggingFace or remote API (硅基流动等)."""

import threading
from functools import lru_cache
from typing import Any

import httpx
from langchain_community.cross_encoders.base import BaseCrossEncoder
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.logging_config import get_logger


logger = get_logger(__name__)


class RemoteAPIRerank(BaseModel, BaseCrossEncoder):
    """通用远端 Reranker，兼容硅基流动等国内厂商的 /rerank API。

    请求格式遵循 OpenAI Completions API 风格:
        POST {api_url}/rerank
        {"model": ..., "query": ..., "documents": [...], "top_n": ...}
    响应格式:
        {"results": [{"index": 0, "relevance_score": 0.95}, ...]}
    """

    api_url: str
    api_key: str
    model: str
    top_n: int = 5
    timeout: float = 10.0

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    def score(self, text_pairs: list[tuple[str, str]]) -> list[float]:
        if not text_pairs:
            return []
        query = text_pairs[0][0]
        documents = [p[1] for p in text_pairs]

        resp = httpx.post(
            self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": self.top_n,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        scores = [0.0] * len(documents)
        for item in data.get("results", []):
            scores[item["index"]] = item["relevance_score"]
        return scores


def _resolve_api_url() -> str:
    url = settings.rag_rerank_api_url.strip()
    if url:
        return url
    base = settings.llm_api_base.rstrip("/") if hasattr(settings, "llm_api_base") else ""
    if base:
        return f"{base}/rerank"
    return ""


def _resolve_api_key() -> str:
    key = settings.rag_rerank_api_key.strip()
    if key:
        return key
    return settings.llm_api_key if hasattr(settings, "llm_api_key") else ""


_built_reranker = None
_reranker_lock = threading.Lock()


def get_reranker():
    """延迟构建并缓存 CrossEncoderReranker（线程安全）。

    返回 langchain 的 CrossEncoderReranker（可直接传给 langchain_classic 的
    ContextualCompressionRetriever），或 None 表示不可用。
    """
    global _built_reranker
    if _built_reranker is not None:
        return _built_reranker if _built_reranker is not False else None

    with _reranker_lock:
        if _built_reranker is not None:
            return _built_reranker if _built_reranker is not False else None

        if not settings.rag_rerank_enabled:
            logger.info("reranker disabled by config")
            _built_reranker = False
            return None

        try:
            from langchain_classic.retrievers.document_compressors import CrossEncoderReranker

            provider = settings.rag_rerank_provider

            if provider == "siliconflow":
                api_url = _resolve_api_url()
                api_key = _resolve_api_key()
                if not api_url or not api_key:
                    logger.error(
                        "reranker provider=siliconflow but api_url/api_key not configured "
                        "(api_url=%r, api_key_set=%s)",
                        api_url,
                        bool(api_key),
                    )
                    _built_reranker = False
                    return None
                model = RemoteAPIRerank(
                    api_url=api_url,
                    api_key=api_key,
                    model=settings.rag_rerank_model,
                    top_n=settings.rag_rerank_top_n,
                )
            elif provider == "local":
                from langchain_community.cross_encoders import HuggingFaceCrossEncoder

                model = HuggingFaceCrossEncoder(model_name=settings.rag_rerank_model)
            else:
                logger.error(f"unsupported reranker provider: {provider}")
                _built_reranker = False
                return None

            _built_reranker = CrossEncoderReranker(model=model, top_n=settings.rag_rerank_top_n)
            logger.info(
                f"reranker loaded: provider={provider}, model={settings.rag_rerank_model}, "
                f"top_n={settings.rag_rerank_top_n}"
            )
            return _built_reranker
        except ImportError as exc:
            logger.error(f"reranker dependencies not installed: {exc}")
            _built_reranker = False
            return None
        except Exception as exc:
            logger.error(f"failed to load reranker: {exc}")
            _built_reranker = False
            return None
