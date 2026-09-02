"""审查 Agent 包（app/compliance/agents/）——每个 Agent 一个职责（设计文档 §5.6）。

- SupervisorAgent  编排：文档分类 → 审查计划制定（supervisor.py）
- ExtractorAgent   提取：条款类型分类 + 关键信息提取（extractor.py）
- ReviewerAgent    审查：5 类风险 × 3 级，输出 RiskItem（reviewer.py）
- ResearcherAgent  检索：法规检索 + 引用强制校验（researcher.py）
- ReporterAgent    报告：摘要 + 报告数据组装（reporter.py）

LLM 装配（agents/base.py，不改动 app/rag 层）：
- get_llm_for_compliance()：复用 get_llm() + .bind(低温度 / 可选独立模型)
- get_structured_llm(schema)：结构化输出；test 模式返回 None（skills 走确定性 mock）
"""

from app.compliance.agents.base import (
    get_llm_for_compliance,
    get_structured_llm,
    is_test_mode,
)
from app.compliance.agents.supervisor import SupervisorAgent
from app.compliance.agents.extractor import ExtractorAgent
from app.compliance.agents.reviewer import ReviewerAgent
from app.compliance.agents.researcher import ResearcherAgent
from app.compliance.agents.reporter import ReporterAgent

__all__ = [
    "get_llm_for_compliance",
    "get_structured_llm",
    "is_test_mode",
    "SupervisorAgent",
    "ExtractorAgent",
    "ReviewerAgent",
    "ResearcherAgent",
    "ReporterAgent",
]