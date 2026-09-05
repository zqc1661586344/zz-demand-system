"""风险识别 Skill（app/compliance/skills/risk_skill.py）——能力层统一封装。

职责（设计文档 F02）：逐条审查条款，识别 5 类风险 × 3 级，输出 RiskItem 列表
（含修改建议与法规引用候选）。实现复用 ReviewerAgent（reviewer.py）：
  - test 模式：Playbook keyword 命中 → 确定性风险项（无 LLM 调用，端到端可验证）
  - openai/ollama：结构化 LLM（低温度）+ Playbook 命中线索融合

ctx 输入键（由 review_graph 的 review 节点注入）：
    clauses: 条款列表 [{clause_number, content, clause_type, ...}]
    rules:   (可选) 活跃 Playbook 规则（service 层按合同类型过滤）
    regulation_hits: (可选) {clause_number: 法规命中列表}——LLM 引用候选

返回：{"ok": True, "data": {"risks": [RiskItem 兼容 dict]}}；失败 {"ok": False}。
"""

from app.compliance.agents.base import get_llm_for_compliance
from app.compliance.agents.reviewer import ReviewerAgent
from app.compliance.skills.base import SkillBase


class RiskSkill(SkillBase):
    name = "risk"

    def execute(self, ctx: dict) -> dict:
        clauses = ctx.get("clauses") or []
        rules = ctx.get("rules") or []
        regulation_hits = ctx.get("regulation_hits") or {}
        if not clauses:
            return self.err("risk skill: 缺 clauses")

        try:
            agent = ReviewerAgent(llm=get_llm_for_compliance())
            items = agent.review_all(clauses, rules, regulation_hits)
            risks = [i.model_dump() if hasattr(i, "model_dump") else dict(i) for i in items]
            self.log(f"{len(clauses)} clauses -> {len(risks)} risks")
            return {"ok": True, "data": {"risks": risks}}
        except Exception as e:  # noqa: BLE001 — 审查异常统一降级返回
            self.log(f"risk skill failed: {e}")
            return self.err(f"risk skill 失败：{e}")