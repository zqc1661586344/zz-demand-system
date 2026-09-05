"""Reporter Agent — 审查报告数据组装。

职责（设计文档 F04）：把审查结果组装为 reporting/generator 能直接消费的结构化数据：
  - 合同基本信息（doc_type / key_info / page_count）
  - 执行摘要（summary）
  - 风险总览（分级计数 + 百分比）
  - 逐条风险详情（clause_number / risk_level / description / suggestion / references）
  - Playbook 规则命中汇总

test 模式下 summary 由规则模板拼接；openai/ollama 可由 LLM 生成自然语言摘要。
"""

from typing import Optional

from app.compliance.agents.base import AgentBase


def _count_risks(risks: list[dict]) -> dict:
    levels = {"high": 0, "medium": 0, "low": 0}
    for r in risks:
        lv = r.get("risk_level", "low")
        if lv in levels:
            levels[lv] += 1
    levels["total"] = sum(levels.values())
    return levels


def _build_summary(
    doc_type: str, risk_counts: dict, llm=None, quality_score: float | None = None
) -> str:
    """生成执行摘要文本。test 模式规则模板；openai 模式可升级 LLM 生成。"""
    total = risk_counts["total"]
    high = risk_counts["high"]
    medium = risk_counts["medium"]
    low = risk_counts["low"]

    quality_tag = ""
    if quality_score is not None:
        if quality_score >= 0.8:
            quality_tag = "审查质量良好。"
        elif quality_score >= 0.5:
            quality_tag = "审查质量中等，建议重点关注高风险条款。"
        else:
            quality_tag = "审查质量偏低，建议人工复核。"

    if high > 0:
        verdict = "存在**高风险**条款，建议修改后签署。"
    elif medium > 0:
        verdict = "条款基本合规，有中低风险可谈判调整。"
    elif total > 0:
        verdict = "未发现重大风险，部分条款建议关注。"
    else:
        verdict = "未检出风险项。"

    return (
        f"合同类型：{doc_type}。"
        f"共检出 {total} 项风险（高 {high} / 中 {medium} / 低 {low}）。"
        f"{verdict} {quality_tag}"
    )


class ReporterAgent(AgentBase):
    name = "reporter"

    def build_report_data(
        self,
        *,
        doc_info: dict,
        key_info: dict,
        clauses: list[dict],
        risks: list[dict],
        quality_score: Optional[float] = None,
    ) -> dict:
        """组装完整报告数据（供 reporting/generator 渲染 Word/HTML）。

        返回结构：
        {
          "summary": str,
          "doc_info": {...},
          "key_info": {...},
          "risk_counts": {"high": n, "medium": n, "low": n, "total": n},
          "risks": [...],  # 按 risk_level 排序
          "clauses": [...],
          "highlights": [...],  # high 风险快速定位
          "quality_score": float | None,
        }
        """
        risk_counts = _count_risks(risks)
        summary = _build_summary(
            doc_info.get("doc_type", "other"),
            risk_counts,
            quality_score=quality_score,
        )

        level_order = {"high": 0, "medium": 1, "low": 2}
        sorted_risks = sorted(
            risks,
            key=lambda r: (
                level_order.get(r.get("risk_level", "low"), 3),
                r.get("clause_number", ""),
            ),
        )

        highlights = [
            {
                "clause_number": r.get("clause_number"),
                "description": r.get("description"),
                "suggestion": r.get("suggestion"),
            }
            for r in sorted_risks
            if r.get("risk_level") == "high"
        ]

        self.log(
            f"report assembled: doc_type={doc_info.get('doc_type')} "
            f"risks={risk_counts['total']} clauses={len(clauses)}"
        )

        return {
            "summary": summary,
            "doc_info": doc_info,
            "key_info": key_info or {},
            "risk_counts": risk_counts,
            "risks": sorted_risks,
            "clauses": clauses,
            "highlights": highlights,
            "quality_score": quality_score,
        }
