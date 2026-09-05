"""LLM configuration — OpenAI, Ollama, or local for testing."""

from functools import lru_cache

from langchain_core.runnables import RunnableLambda
from langchain_core.messages import AIMessage

from app.config import settings

from app.logging_config import get_logger


logger = get_logger(__name__)


def _fake_llm_invoke(input) -> AIMessage:
    """从LangChain输入中提取用户提示，并返回一个模拟答案。"""
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


# 预构建的RunnableLambda使得“|”操作符可以在链式操作中工作
_fake_llm_runnable = RunnableLambda(_fake_llm_invoke)


@lru_cache
def get_llm():
    """返回已配置的LLM实例，用于生成答案。"""
    provider = settings.llm_provider

    if provider == "local" or provider == "test":
        logger.info("use fake model")
        return _fake_llm_runnable

    # TODO: 后续加上其他模型适配
    if provider == "openai":
        logger.info("use a model compliant with the OpenAI protocol")

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model or "gpt-4o-mini",
            temperature=0.3,
            api_key=settings.llm_api_key,
            base_url=settings.llm_api_base,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    elif provider == "ollama":
        logger.info(f"use local ollama model: {settings.ollama_model} ")
        from langchain_community.chat_models import ChatOllama

        return ChatOllama(
            model=settings.ollama_model or "qwen2.5:7b",
            temperature=0.3,
            base_url=settings.ollama_base_url,
            timeout=settings.llm_timeout_seconds,
        )

    else:
        logger.error(f"unsupported llm provider: {provider}")
        raise ValueError(f"Unsupported LLM provider: {provider}")
