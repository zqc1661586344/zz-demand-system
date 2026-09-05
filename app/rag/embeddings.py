"""Embedding model configuration — OpenAI, Ollama, or local for testing."""

from functools import lru_cache

from app.config import settings
from app.logging_config import get_logger


logger = get_logger(__name__)


class FakeEmbeddings:
    """在不调用外部API的情况下，返回用于测试的零向量。"""

    def __init__(self, dimension: int = 1024):
        self.dimension = dimension
        self.model_name = "fake"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimension for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self.dimension


@lru_cache
def get_embedding_model():
    """根据配置返回embedding模型实例。"""
    provider = settings.embedding_provider

    if provider == "local" or provider == "test":
        logger.info("the local embeeding provider test")
        return FakeEmbeddings()

    logger.info(f"the embeeding provider is: {provider}")

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.embedding_model or "BAAI/bge-m3",
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_api_base,
            check_embedding_ctx_length=False,
            timeout=settings.embedding_timeout_seconds,
            max_retries=settings.embedding_max_retries,
        )

    elif provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=settings.ollama_embedding_model or "nomic-embed-text",
            base_url=settings.ollama_base_url,
            timeout=settings.embedding_timeout_seconds,
        )

    else:
        logger.error(f"unsupported llm provider: {provider}")
        raise ValueError(f"Unsupported embedding provider: {provider}")
