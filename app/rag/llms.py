"""LLM configuration — OpenAI, Ollama, or local for testing."""

from functools import lru_cache

from langchain_core.runnables import RunnableLambda
from langchain_core.messages import AIMessage

from app.config import settings


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
