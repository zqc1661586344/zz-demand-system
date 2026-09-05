"""审查 Agent（app/compliance/agents/reviewer.py）——核心风险识别。

职责（设计文档 F02/§5.6）：逐条审查，识别 5 类风险（legality/equality/clarity/
completeness/reasonableness）× 3 级（high/medium/low），输出结构化 RiskItem + 修改建议。

执行路径：
  - test 模式：确定性 mock——直接复用 `playbook.engine.match_rules_for_clauses()` 的
    Playbook 命中转成风险项（不依赖外部 LLM，端到端可验证）。
  - openai/ollama：结构化 LLM 输出 RiskItem（low temperature 0.1），结合 Playbook 命中
    线索与法规引用候选（reviewer_prompt.build_clause_review_prompt）。

风险分类 fallback：Playbook 命中未明确风险维度时，按规则名/条款类型推断 risk_category。
"""

from typing import Optional

from app.compliance.agents.base import AgentBase, get_structured_llm
from app.compliance.agents.prompts.reviewer_prompt import build_clause_review_prompt
from app.compliance.playbook.engine import match_rules_for_clauses
from app.compliance.schemas.review import RiskItem

# Playbook 命中 → 风险维度的启发映射（按规则名关键词）
_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("试用期", "legality"),
    ("违约", "reasonableness"),
    ("红线", "legality"),
    ("社保", "legality"),
    ("竞业", "legality"),
    ("保密", "clarity"),
    ("加班", "equality"),
    ("补偿", "completeness"),
    ("争议解决", "clarity"),
]


def _category_for_hit(hit: dict) -> str:
    """根据 Playbook 命中的规则名/描述推断 risk_category（5 类之一）。"""
    haystack = f"{hit.get('name') or ''} {hit.get('standard_position') or ''}".lower()
    for kw, cat in _CATEGORY_KEYWORDS:
        if kw in haystack:
            return cat
    if hit.get("red_line"):
        return "legality"
    return "reasonableness"


def _hit_to_risk(hit: dict) -> dict:
    """把一个 Playbook 命中转成风险项 dict（供 API 入库 / 前端展示）。"""
    category = _category_for_hit(hit)
    return {
        "clause_number": hit.get("clause_number", ""),
        "risk_level": hit.get("risk_level", "medium"),
        "risk_category": category,
        "description": _build_description(hit, category),
        "suggestion": hit.get("suggested_clause"),
        "suggestion_reason": hit.get("standard_position"),
        "legal_references": (
            [{"ref_type": "playbook", "ref_name": hit.get("legal_basis_ref") or "",
              "ref_article": None, "ref_content": hit.get("standard_position") or ""}]
            if hit.get("legal_basis_ref")
            else []
        ),
        "ai_confidence": hit.get("confidence", 0.9),
    }


def _build_description(hit: dict, category: str) -> str:
    """组装风险描述（含条款号与规则理由，前端可直接展示）。"""
    base = hit.get("name") or "条款风险"
    reason = hit.get("standard_position") or ""
    return (
        f"条款 {hit.get('clause_number') or ''}：{base}"
        f"（{category}）。规则说明：{reason}"
    )


class ReviewerAgent(AgentBase):
    name = "reviewer"

    def review_clause(
        self,
        clause: dict,
        rules: Optional[list[dict]] = None,
        regulation_hits: Optional[list[dict]] = None,
    ) -> list[RiskItem]:
        """审查单条条款，返回风险项列表（无风险返回 []）。

        Args:
            clause: {"clause_number", "content", ...}。
            rules: 活跃 Playbook 规则（service 层按合同类型过滤后喂入）。
            regulation_hits: 法规引用候选（开放 ai 模式用）。
        """
        clause_number = clause.get("clause_number", "")
        text = clause.get("content", "")

        # test 模式：确定性 mock —— 用 Playbook 命中转风险项
        if self.test_mode:
            raw_hits = match_rules_for_clauses([clause], rules or [], llm=None)
            self.log(f"test review clause {clause_number}: {len(raw_hits)} hits")
            risks = []
            for h in raw_hits:
                raw = _hit_to_risk(h)
                risks.append(RiskItem(**raw))
            return risks

        # 非 test：结构化 LLM 输出 + 融合 Playbook 命中线索
        structured = get_structured_llm(RiskItem)
        if structured is None:
            # 结构化失败降级：仍用 Playbook 命中（确定性路径）
            raw_hits = match_rules_for_clauses([clause], rules or [], llm=None)
            return [RiskItem(**_hit_to_risk(h)) for h in raw_hits]

        try:
            prompt = build_clause_review_prompt(
                clause_number, text,
                playbook_hints=rules, regulation_hits=regulation_hits,
            )
            items = structured.invoke(prompt)
            # 兼容单条 RiskItem 或 list
            if isinstance(items, RiskItem):
                return [items]
            return list(items)
        except Exception as e:  # noqa: BLE001
            self.log(f"LLM review failed for clause {clause_number}: {e}")
            return []

    def review_all(
        self,
        clauses: list[dict],
        rules: Optional[list[dict]] = None,
        regulation_hits: Optional[dict] = None,
    ) -> list[RiskItem]:
        """逐条审查全部条款，汇总所有风险。regulation_hits: {clause_number: hits}。"""
        all_risks: list[RiskItem] = []
        for clause in clauses:
            hits = (regulation_hits or {}).get(clause.get("clause_number"))
            risks = self.review_clause(clause, rules, hits)
            all_risks.extend(risks)
        self.log(f"review_all: {len(clauses)} clauses -> {len(all_risks)} risks")
        return all_risks