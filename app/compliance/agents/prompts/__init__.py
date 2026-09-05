"""审查提示词包（app/compliance/agents/prompts/）。

各 Agent 的提示词按职责分文件：
- reviewer_prompt.py   审查 Agent（风险识别/分级/建议）——核心提示词
- supervisor_prompt.py 编排 Agent（文档分类/审查计划）【待补】
- extractor_prompt.py  提取 Agent（条款类型/关键信息）【待补】
- reporter_prompt.py   报告 Agent（报告正文生成）【待补】

此处仅导出已就绪的模块；后续文件补齐后在此追加 __all__。
"""

from app.compliance.agents.prompts.reviewer_prompt import (
    SYSTEM_PROMPT,
    build_clause_review_prompt,
)

__all__ = ["SYSTEM_PROMPT", "build_clause_review_prompt"]