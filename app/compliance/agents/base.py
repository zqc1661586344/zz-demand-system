"""Agent 基类 + 审查专用结构化 LLM 抽象（app/compliance/agents/base.py）。

背景：现有 `app/rag/llms.py:get_llm()` 返回单例 LLM（OpenAI 兼容 / Ollama / test mock），
且**不支持指定 temperature**（硬编码 0.3）。审查要求低温度（0.1）与结构化输出
（Pydantic function calling），但按项目约定不能改动 app/rag/llms.py（原有 RAG 层零修改）。

本模块提供两个能力：
  1. `get_llm_for_compliance()` —— 返回审查专用 LLM：复用 get_llm()，用 `.bind()` 覆盖
     低温度与可选独立模型（不改 get_llm 本身，满足零修改约束）。
  2. `get_structured_llm(schema)` —— 结构化输出封装；test 模式返回 None（skills 走
     确定性 mock），openai/ollama 返回 `.with_structured_output(schema)`。
"""

from functools import lru_cache
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def is_test_mode() -> bool:
    """当前是否为 test/local LLM 模式（skills 据此走确定性 mock，不调外部 LLM）。"""
    return settings.llm_provider in ("test", "local")


@lru_cache
def get_llm_for_compliance():
    """返回审查专用 LLM：复用 `app/rag/llms.get_llm()`，但绑定低温度（默认 0.1）与可选独立模型。

    - test 模式：直接返回 get_llm() 的 mock（确定性，供端到端验证）。
    - openai/ollama：用 `.bind(temperature=settings.compliance_llm_temperature)` 覆盖；
      compliance_llm_model 非空时同时 bind(model=...)（test mock 忽略）。
    """
    from app.rag.llms import get_llm

    base = get_llm()
    if is_test_mode():
        return base

    try:
        bind_kwargs = {"temperature": settings.compliance_llm_temperature}
        if settings.compliance_llm_model:
            bind_kwargs["model"] = settings.compliance_llm_model
        return base.bind(**bind_kwargs)
    except Exception as e:  # noqa: BLE001 — bind 失败降级原 LLM，审查仍可用
        logger.warning("compliance llm bind failed (fall back to base): %s", e)
        return base


def get_structured_llm(schema):
    """返回能产出给定 Pydantic schema 的结构化 LLM；test 模式返回 None。

    Args:
        schema: Pydantic BaseModel 类（如 RiskItem、KeyInfo、ReviewResult）。

    Returns:
        - openai/ollama：`get_llm_for_compliance().with_structured_output(schema)`
        - test 模式：None（调用方 skills 走确定性 mock，见 app/compliance/skills/*）
    """
    if is_test_mode():
        return None
    try:
        llm = get_llm_for_compliance()
        return llm.with_structured_output(schema)
    except Exception as e:  # noqa: BLE001 — 结构化失败降级 None，由调用方 mock/降级
        logger.warning("with_structured_output failed, fall back to None: %s", e)
        return None


class AgentBase:
    """所有审查 Agent 的基类：统一构造、结构化输出、测试模式感知。

    子类实现各自的业务方法（supervisor/extractor/reviewer/researcher/reporter）；
    需要结构化输出时用 `self.structured(schema)`（test 模式返回 None，子类负责 mock）。
    """

    name: str = "agent"

    def __init__(self, llm=None):
        self._llm = llm

    @property
    def llm(self):
        """惰性获取审查专用 LLM（未显式传入时）。"""
        if self._llm is None:
            self._llm = get_llm_for_compliance()
        return self._llm

    def structured(self, schema):
        """获取当前 agent 的某个 schema 的结构化 LLM（test 模式 None）。"""
        return get_structured_llm(schema)

    @property
    def test_mode(self) -> bool:
        return is_test_mode()

    def log(self, msg: str):
        """统一日志入口（带 agent 名前缀）。"""
        logger.info("[%s] %s", self.name, msg)