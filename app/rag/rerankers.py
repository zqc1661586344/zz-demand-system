"""Reranker configuration — local HuggingFace or remote API (硅基流动等)."""

import threading
import time

import httpx
from langchain_community.cross_encoders.base import BaseCrossEncoder
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.logging_config import get_logger


logger = get_logger(__name__)

_RERANK_COOLDOWN_SEC = 300  # 单次失败后冷却 5 分钟再重试


class RemoteAPIRerank(BaseModel, BaseCrossEncoder):
    """通用远端 Reranker，兼容硅基流动等国内厂商的 /rerank API。

    请求格式遵循 OpenAI Completions API 风格:
        POST {api_url}/rerank
        {"model": ..., "query": ..., "documents": [...], "top_n": ...}
    响应格式:
        {"results": [{"index": 0, "relevance_score": 0.95}, ...]}
    """

    api_url: str
    api_key: str = Field(repr=False)
    model: str
    top_n: int = 5
    timeout: float = 10.0
    max_retries: int = 2

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    def score(self, text_pairs: list[tuple[str, str]]) -> list[float]:
        if not text_pairs:
            return []
        query = text_pairs[0][0]
        documents = [p[1] for p in text_pairs]

        transport = httpx.HTTPTransport(retries=self.max_retries)
        try:
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
                transport=transport,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.warning(
                f"rerank API returned HTTP {e.response.status_code}: {e.response.text[:200]}"
            )
            raise
        except httpx.RequestError as e:
            logger.warning(f"rerank API request failed after retries: {e}")
            raise

        data = resp.json()

        if not isinstance(data, dict) or "results" not in data:
            logger.warning(
                f"rerank API response missing 'results' key, keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}"
            )
            return [0.0] * len(documents)

        scores = [0.0] * len(documents)
        for item in data["results"]:
            if "index" not in item or "relevance_score" not in item:
                continue
            try:
                scores[item["index"]] = item["relevance_score"]
            except IndexError:
                continue
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
_reranker_fail_ts: float | None = None


def get_reranker():
    """延迟构建并缓存 CrossEncoderReranker（线程安全）。

    返回 langchain 的 CrossEncoderReranker（可直接传给 langchain_classic 的
    ContextualCompressionRetriever），或 None 表示不可用。

    临时故障（API 超时/500）走冷却机制：记录失败时间，_RERANK_COOLDOWN_SEC 内
    返回 None，超时后重试。配置缺失/依赖安装失败则是永久 False（直到进程重启）。
    """
    global _built_reranker, _reranker_fail_ts

    with _reranker_lock:
        if _built_reranker is False:
            return None

        if _built_reranker is not None:
            if _reranker_fail_ts is not None:
                if time.time() - _reranker_fail_ts < _RERANK_COOLDOWN_SEC:
                    return None
                _reranker_fail_ts = None
            return _built_reranker

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


def mark_reranker_failed() -> None:
    """_maybe_rerank 异常时调用，触发冷却降级而非永久禁用。"""
    global _reranker_fail_ts
    with _reranker_lock:
        _reranker_fail_ts = time.time()
        logger.warning(f"reranker marked failed, cooling down for {_RERANK_COOLDOWN_SEC}s")
