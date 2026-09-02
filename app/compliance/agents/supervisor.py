"""编排 Agent（app/compliance/agents/supervisor.py）——审查计划制定。

职责（设计文档 F01/F05）：文档分类 → 制定审查计划（哪些阶段、哪些条款优先抓）。

执行路径：
  - test 模式：确定性 mock——直接产出固定阶段序列（分解析/提取/逐条审查/引用/报告），
    供审查图（review_graph）端到端可验证。
  - openai/ollama：结构化输出阶段计划（预留 `with_structured_output`），MVP 阶段
    因计划项由审查图固定（parse→supervise→extract→review→reflect→report），
    本 Agent 主要做文档分类复核与计划条目标注。
"""

from typing import Optional

from app.compliance.agents.base import AgentBase

# 审查流水线固定阶段（与 review_graph 的节点一一对应）
DEFAULT_PLAN = [
    "解析文档并确认合同类型",
    "提取条款结构与关键信息",
    "逐条风险审查（5 类 × 3 级）",
    "法规引用检索与强制校验",
    "自反思质量复核",
    "生成审查报告",
]


class SupervisorAgent(AgentBase):
    name = "supervisor"

    def plan_review(
        self,
        parsing_result: Optional[dict] = None,
        contract_type_override: Optional[str] = None,
    ) -> dict:
        """制定审查计划。

        Args:
            parsing_result: 解析结果（含 doc_type/doc_confidence/clauses 数等）。
            contract_type_override: 用户指定合同类型（优先级高于自动判定）。

        Returns:
            {"doc_type": str, "confidence": float, "plan": list[str]}
        """
        doc_type = (
            contract_type_override
            or (parsing_result or {}).get("doc_type")
            or "other"
        )
        confidence = (parsing_result or {}).get("doc_confidence", 0.5)

        if not self.test_mode:
            # 非 test：预留 LLM 复核分类（MVP 直接用自动判定结果，不额外调 LLM 保性能）
            self.log(f"doc_type={doc_type} (conf={confidence}), plan ready")
        else:
            self.log(f"test plan: doc_type={doc_type}")

        return {"doc_type": doc_type, "confidence": confidence, "plan": list(DEFAULT_PLAN)}