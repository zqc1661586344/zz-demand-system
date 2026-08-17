"""Embedding model configuration — OpenAI, Ollama, or local for testing."""

from functools import lru_cache

from langchain_core.runnables import RunnableLambda
from langchain_core.messages import AIMessage

from app.config import settings


class FakeEmbeddings:
    """Returns zero-vectors for testing without external API calls."""

    def __init__(self, dimension: int = 768):
        self.dimension = dimension
        self.model_name = "fake"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimension for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self.dimension


def _fake_llm_invoke(input) -> AIMessage:
    """Extract the user prompt from a LangChain input and return a mock answer."""
    if hasattr(input, "to_messages"):
        messages = input.to_messages()
    elif isinstance(input, list):
        messages = input
    elif isinstance(input, str):
        messages = [("human", input)]
    else:
        messages = [input]

    prompt = ""
    for m in messages:
        if hasattr(m, "type") and m.type == "human":
            prompt = m.content if hasattr(m, "content") else str(m)
        elif isinstance(m, dict) and m.get("role") == "user":
            prompt = m.get("content", "")
        elif isinstance(m, tuple) and len(m) == 2 and m[0] == "human":
            prompt = m[1]

    return AIMessage(
        content=f"This is a test answer using mock LLM. The query was: '{prompt[:200]}'"
    )


# Pre-built RunnableLambda so | operator works in chains
_fake_llm_runnable = RunnableLambda(_fake_llm_invoke)


@lru_cache
def get_embedding_model():
    """Return the configured embedding model instance based on settings."""
    provider = settings.embedding_provider

    if provider == "local" or provider == "test":
        return FakeEmbeddings()

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(
            model=settings.embedding_model or "text-embedding-ada-002",
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
        )

    elif provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=settings.ollama_embedding_model or "nomic-embed-text",
            base_url=settings.ollama_base_url,
        )

    else:
        raise ValueError(f"Unsupported embedding provider: {provider}")


@lru_cache
def get_llm():
    """Return the configured LLM instance for answer generation."""
    provider = settings.llm_provider

    if provider == "local" or provider == "test":
        return _fake_llm_runnable

    # TODO: 后续加上其他模型适配
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model or "gpt-4o-mini",
            temperature=0.3,
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
        )

    elif provider == "ollama":
        from langchain_community.chat_models import ChatOllama

        return ChatOllama(
            model=settings.ollama_model or "qwen2.5:7b",
            temperature=0.3,
            base_url=settings.ollama_base_url,
        )

    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")
