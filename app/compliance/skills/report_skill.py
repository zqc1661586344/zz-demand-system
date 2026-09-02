"""报告生成 Skill（app/compliance/skills/report_skill.py）——报告数据组装。

职责（设计文档 F04）：把审查结果组装为报告所需结构化数据（执行摘要、风险总览、
逐条风险详情、合同基本信息），供 reporting/generator 渲染 Word/HTML。
实现复用 ReporterAgent.build_report_data（reporter.py），本文件仅做 ctx 适配。

ctx 输入键（由 review_graph 的 generate_report 节点注入）：
    doc_info:  {document_id, original_filename, doc_type, page_count, ...}
    key_info:  合同关键信息（KeyInfo 兼容 dict）
    clauses:   条款列表 [{clause_number, content, ...}]
    risks:     风险项列表 [{risk_level, risk_category, description, suggestion, ...}]

返回：{"ok": True, "data": {"report_data": {...}}}；失败 {"ok": False}。
"""

from app.compliance.agents.reporter import ReporterAgent
from app.compliance.skills.base import SkillBase


class ReportSkill(SkillBase):
    name = "report"

    def execute(self, ctx: dict) -> dict:
        doc_info = ctx.get("doc_info") or {}
        key_info = ctx.get("key_info") or {}
        clauses = ctx.get("clauses") or []
        risks = ctx.get("risks") or []

        try:
            reporter = ReporterAgent()
            report_data = reporter.build_report_data(
                doc_info=doc_info,
                key_info=key_info,
                clauses=clauses,
                risks=risks,
            )
            self.log(
                f"report data assembled: {len(risks)} risks "
                f"(high={report_data['risk_counts']['high']})"
            )
            return {"ok": True, "data": {"report_data": report_data}}
        except Exception as e:  # noqa: BLE001
            self.log(f"report data failed: {e}")
            return self.err(f"report skill 失败：{e}")