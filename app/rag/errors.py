"""RAG 链路错误分类 + 业务异常 + 结构化错误响应。

把基础设施层/检索层/生成层抛出的原始异常，统一映射为可识别、可重试、可向用户暴露的结构化格式，支撑 API 层的差异化降级策略。
"""

from __future__ import annotations

from typing import Any

from app.logging_config import get_logger

logger = get_logger(__name__)


class RAGErrorCode:
    """错误码常量 — 按失败层级和是否可重试分组。

    码值区间：
      1xxx — 基础设施层（DB / PGVector / Redis 断连）
      2xxx — 模型层（LLM / Embedding API 不可用、鉴权失败、限流）
      3xxx — 检索层（BM25 构建异常、重排器异常）
      4xxx — 数据层（无文档、文档索引失败、文档被删）
      5xxx — 未知/兜底
    """

    # 1xxx 基础设施
    VECTOR_STORE_UNAVAILABLE = "1001"
    DATABASE_UNAVAILABLE = "1002"
    REDIS_UNAVAILABLE = "1003"

    # 2xxx 模型
    LLM_TIMEOUT = "2001"
    LLM_AUTH_INVALID = "2002"
    LLM_RATE_LIMITED = "2003"
    LLM_UNAVAILABLE = "2004"
    EMBEDDING_TIMEOUT = "2011"
    EMBEDDING_AUTH_INVALID = "2012"
    EMBEDDING_UNAVAILABLE = "2013"

    # 3xxx 检索
    BM25_BUILD_FAILED = "3001"
    RERANKER_FAILED = "3002"
    SPARSE_SEARCH_FAILED = "3003"

    # 4xxx 数据
    NO_DOCUMENTS = "4001"
    DOC_NOT_INDEXED = "4002"

    # 5xxx 未知
    UNKNOWN = "5000"


RETRYABLE_CODES = {
    RAGErrorCode.VECTOR_STORE_UNAVAILABLE,
    RAGErrorCode.DATABASE_UNAVAILABLE,
    RAGErrorCode.REDIS_UNAVAILABLE,
    RAGErrorCode.LLM_TIMEOUT,
    RAGErrorCode.LLM_UNAVAILABLE,
    RAGErrorCode.EMBEDDING_TIMEOUT,
    RAGErrorCode.EMBEDDING_UNAVAILABLE,
    RAGErrorCode.BM25_BUILD_FAILED,
    RAGErrorCode.RERANKER_FAILED,
    RAGErrorCode.SPARSE_SEARCH_FAILED,
    RAGErrorCode.UNKNOWN,
}


class RAGError(Exception):
    """RAG 链路结构化业务异常。

    Attributes:
        code: RAGErrorCode 常量值。
        message: 面向日志/调试的详细描述。
        user_message: 面向最终用户的安全友好消息（不含内部堆栈）。
        retryable: 是否可以自动重试。
        retry_after: 建议等待秒数（429 等场景）。
        details: 结构化上下文（如原始异常类型、影响的资源名）。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        user_message: str | None = None,
        retryable: bool | None = None,
        retry_after: int | None = None,
        details: dict[str, Any] | None = None,
        cause: BaseException | None = None,
    ):
        self.code = code
        self.message = message
        self.user_message = user_message or "抱歉，问答服务暂时不可用，请稍后再试。"
        self.retryable = retryable if retryable is not None else code in RETRYABLE_CODES
        self.retry_after = retry_after
        self.details = details or {}
        self.cause = cause
        super().__init__(message)


def classify_exception(exc: BaseException) -> tuple[str, str, bool]:
    """根据异常类型推断 RAGErrorCode 和可重试性。

    返回: (code, user_message, retryable)
    """
    name = type(exc).__name__
    msg = str(exc).lower()

    _provider = exc.__class__.__module__

    # ---- HTTP 相关（httpx / requests） ----
    if "HTTPStatusError" in name or "status_error" in _provider.lower():
        if (
            "401" in msg
            or "403" in msg
            or "unauthorized" in msg
            or "invalid api" in msg
            or "invalid key" in msg
            or "auth" in msg
            and "fail" in msg
        ):
            return RAGErrorCode.LLM_AUTH_INVALID, "LLM 服务鉴权失败，请检查 API Key 配置。", False
        if "429" in msg or "rate limit" in msg or "too many" in msg:
            return RAGErrorCode.LLM_RATE_LIMITED, "请求过于频繁，请稍后再试。", False
        if "503" in msg or "502" in msg:
            return RAGErrorCode.LLM_UNAVAILABLE, "LLM 服务暂时不可用，请稍后再试。", True

    if "ConnectError" in name or "ConnectionError" in name or "ConnectionRefusedError" in name:
        return RAGErrorCode.LLM_UNAVAILABLE, "无法连接到 LLM 服务。", True

    if "TimeoutError" in name or "ReadTimeout" in name:
        return RAGErrorCode.LLM_TIMEOUT, "LLM 服务响应超时，请稍后再试。", True

    # ---- PGVector / SQLAlchemy 相关 ----
    if (
        "psycopg" in _provider.lower()
        or "sqlalchemy" in _provider.lower()
        or "OperationalError" in name
    ):
        if "vector" in msg or "pgvector" in msg:
            return RAGErrorCode.VECTOR_STORE_UNAVAILABLE, "向量库暂时不可用，请稍后再试。", True
        return RAGErrorCode.DATABASE_UNAVAILABLE, "数据库暂时不可用，请稍后再试。", True

    # ---- 通用 message 关键字 fallback（真实库异常不在上述精确匹配路径时） ----
    if "unauthorized" in msg or ("401" in msg and "api" in msg):
        return RAGErrorCode.LLM_AUTH_INVALID, "LLM 服务鉴权失败，请检查 API Key 配置。", False
    if "rate limit" in msg or ("429" in msg and "too many" in msg):
        return RAGErrorCode.LLM_RATE_LIMITED, "请求过于频繁，请稍后再试。", False
    if "timeout" in msg or "timed out" in msg:
        return RAGErrorCode.LLM_TIMEOUT, "LLM 服务响应超时，请稍后再试。", True
    if "connection refused" in msg or "connection reset" in msg or "no route" in msg:
        return RAGErrorCode.LLM_UNAVAILABLE, "无法连接到 LLM 服务。", True
    if "pgvector" in msg or "vector store" in msg:
        return RAGErrorCode.VECTOR_STORE_UNAVAILABLE, "向量库暂时不可用，请稍后再试。", True
    if "psycopg" in msg or "sqlalchemy" in msg or "database" in msg and "connect" in msg:
        return RAGErrorCode.DATABASE_UNAVAILABLE, "数据库暂时不可用，请稍后再试。", True
    if "embed" in msg or "embedding" in msg:
        if "unauthorized" in msg or "401" in msg or "invalid" in msg:
            return RAGErrorCode.EMBEDDING_AUTH_INVALID, "Embedding 服务鉴权失败。", False
        if "timeout" in msg:
            return RAGErrorCode.EMBEDDING_TIMEOUT, "Embedding 服务响应超时。", True
        return RAGErrorCode.EMBEDDING_UNAVAILABLE, "Embedding 服务暂时不可用。", True

    return RAGErrorCode.UNKNOWN, "抱歉，问答服务出现未知错误，请稍后再试。", True


def to_structured_dict(
    exc: BaseException, *, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """把任意异常转为 API 层可以直接返回的结构化字典。

    如果是 RAGError，直接用其中的 code/message 等字段；否则先走 classify_exception 推断。
    """
    if isinstance(exc, RAGError):
        code = exc.code
        user_msg = exc.user_message
        retryable = exc.retryable
        details = {**exc.details}
        if exc.retry_after:
            details["retry_after"] = exc.retry_after
            retry_after = exc.retry_after
        else:
            retry_after = None
    else:
        code, user_msg, retryable = classify_exception(exc)
        retry_after = None
        details = {"exception_type": type(exc).__name__, "exception_msg": str(exc)[:200]}

    if extra:
        details.update(extra)

    result: dict[str, Any] = {
        "error_code": code,
        "message": user_msg,
        "retryable": retryable,
    }
    if retry_after:
        result["retry_after"] = retry_after
    if details:
        result["details"] = details
    return result
