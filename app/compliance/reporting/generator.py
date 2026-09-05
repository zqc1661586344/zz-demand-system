"""报告生成器 — 组装 HTML 报告并落盘。

MVP：生成单文件 HTML（内嵌样式、自包含，前端可直接 iframe/下载）。
后续版本追加 Word 导出（python-docx），本模块预留 generate_word 接口。

设计原则：generator 只负责「报告数据 dict → 文件 bytes → 落盘」，
不做业务逻辑。业务逻辑（风险计数、摘要、排序）已由 ReporterAgent 产出。
"""

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


_LEVEL_BADGE = {
    "high": ('<span class="badge high">高风险</span>', "#dc2626", "#fef2f2"),
    "medium": ('<span class="badge medium">中风险</span>', "#f59e0b", "#fffbeb"),
    "low": ('<span class="badge low">低风险</span>', "#6b7280", "#f9fafb"),
}


def _fmt_level(level: str) -> str:
    badge, _, _ = _LEVEL_BADGE.get(level, _LEVEL_BADGE["low"])
    return badge


def _color_for(level: str) -> tuple[str, str]:
    _, fg, bg = _LEVEL_BADGE.get(level, _LEVEL_BADGE["low"])
    return fg, bg


def render_html(report_data: dict) -> str:
    """把 ReporterAgent 产出的 report_data 渲染成 HTML 字符串。"""
    summary = report_data.get("summary", "")
    doc_info = report_data.get("doc_info", {})
    key_info = report_data.get("key_info", {})
    risk_counts = report_data.get("risk_counts", {"high": 0, "medium": 0, "low": 0, "total": 0})
    risks = report_data.get("risks", [])
    clauses = report_data.get("clauses", [])
    highlights = report_data.get("highlights", [])
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ---- 执行摘要卡片 ----
    high_fg, high_bg = _color_for("high")
    med_fg, med_bg = _color_for("medium")
    low_fg, low_bg = _color_for("low")

    cards_html = f"""
      <div class="metric" style="border-left:4px solid {high_fg}; background:{high_bg};">
        <div class="metric-label">高风险</div>
        <div class="metric-value" style="color:{high_fg};">{risk_counts["high"]}</div>
      </div>
      <div class="metric" style="border-left:4px solid {med_fg}; background:{med_bg};">
        <div class="metric-label">中风险</div>
        <div class="metric-value" style="color:{med_fg};">{risk_counts["medium"]}</div>
      </div>
      <div class="metric" style="border-left:4px solid {low_fg}; background:{low_bg};">
        <div class="metric-label">低风险</div>
        <div class="metric-value" style="color:{low_fg};">{risk_counts["low"]}</div>
      </div>
      <div class="metric total">
        <div class="metric-label">风险总数</div>
        <div class="metric-value">{risk_counts["total"]}</div>
      </div>
    """

    # ---- 关键信息 ----
    if key_info:
        ki_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in key_info.items() if v)
        ki_html = f"""
          <div class="section">
            <h2>合同关键信息</h2>
            <table class="ki-table"><tbody>{ki_rows}</tbody></table>
          </div>
        """
    else:
        ki_html = ""

    # ---- 风险详情 ----
    if risks:
        risk_rows = []
        for r in risks:
            level = r.get("risk_level", "low")
            badge = _fmt_level(level)
            desc = r.get("description", "")
            suggestion = r.get("suggestion") or "（无）"
            reason = r.get("suggestion_reason") or ""
            clause_num = r.get("clause_number") or "-"
            category = r.get("risk_category", "")
            refs_html = ""
            refs = r.get("legal_references") or []
            if refs:
                ref_items = []
                for rf in refs:
                    verified = rf.get("verified")
                    mark = "✓ 已校验" if verified else "⚠ 需人工核实"
                    ref_items.append(
                        f"<li><b>{rf.get('ref_name', '')}</b> {rf.get('ref_article', '')} "
                        f"<small class='ref-mark'>{mark}</small><br>"
                        f"<blockquote>{rf.get('ref_content', '')}</blockquote></li>"
                    )
                refs_html = f"<ul class='refs'>{''.join(ref_items)}</ul>"

            risk_rows.append(f"""
              <div class="risk-item level-{level}">
                <div class="risk-head">
                  {badge}
                  <span class="clause-ref">条款 {clause_num}</span>
                  <span class="category-tag">{category}</span>
                </div>
                <div class="risk-desc">{desc}</div>
                <div class="risk-suggestion">
                  <b>修改建议：</b>{suggestion}
                </div>
                {f"<div class='risk-reason'><b>理由：</b>{reason}</div>" if reason else ""}
                {refs_html}
              </div>
            """)
        risks_html = f"""
          <div class="section">
            <h2>风险详情（{len(risks)} 项）</h2>
            {"".join(risk_rows)}
          </div>
        """
    else:
        risks_html = """
          <div class="section">
            <h2>风险详情</h2>
            <p class="empty">未检出风险项。</p>
          </div>
        """

    # ---- 高风险快速定位 ----
    if highlights:
        hl_items = "".join(
            f"<li><b>{h.get('clause_number', '-')}</b> — {h.get('description', '')}</li>"
            for h in highlights
        )
        highlights_html = f"""
          <div class="section highlights">
            <h2>⚠ 高风险快速定位</h2>
            <ul>{hl_items}</ul>
          </div>
        """
    else:
        highlights_html = ""

    # ---- 条款目录 ----
    if clauses:
        clause_rows = "".join(
            f"<tr><td>{c.get('clause_number', '')}</td>"
            f"<td>{c.get('title', '') or (c.get('content', '')[:60] + '...')}</td>"
            f"<td>{c.get('clause_type', '')}</td></tr>"
            for c in clauses
        )
        clauses_html = f"""
          <div class="section">
            <h2>合同条款（{len(clauses)} 条）</h2>
            <table class="clauses-table"><thead>
              <tr><th>条款号</th><th>标题/摘要</th><th>类型</th></tr>
            </thead><tbody>{clause_rows}</tbody></table>
          </div>
        """
    else:
        clauses_html = ""

    title = doc_info.get("original_filename") or "合同审查报告"
    doc_type = doc_info.get("doc_type", "other")

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title} — 合规审查报告</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; padding: 24px; background: #f8fafc; color: #1e293b; line-height: 1.6; }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  header {{ background: linear-gradient(135deg, #1e40af, #2563eb); color: white;
           padding: 32px; border-radius: 12px; margin-bottom: 24px; }}
  header h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
  header .meta {{ opacity: 0.85; font-size: 13px; }}
  .summary {{ background: white; padding: 20px 24px; border-radius: 8px;
              border-left: 4px solid #2563eb; margin-bottom: 24px; font-size: 15px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }}
  .metric {{ background: white; border-radius: 8px; padding: 16px 20px; }}
  .metric.total {{ border-left: 4px solid #1e293b; background: #f8fafc; }}
  .metric-label {{ font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }}
  .metric-value {{ font-size: 32px; font-weight: 700; margin-top: 4px; }}
  .section {{ background: white; border-radius: 8px; padding: 24px; margin-bottom: 24px; }}
  .section h2 {{ margin-top: 0; font-size: 18px; padding-bottom: 12px;
                 border-bottom: 1px solid #e2e8f0; }}
  .highlights {{ border: 2px solid #fef2f2; }}
  .highlights ul {{ margin: 0; padding-left: 20px; }}
  .badge {{ display: inline-block; padding: 2px 10px; border-radius: 12px;
           font-size: 12px; font-weight: 600; margin-right: 8px; }}
  .badge.high {{ background: #fef2f2; color: #dc2626; }}
  .badge.medium {{ background: #fffbeb; color: #d97706; }}
  .badge.low {{ background: #f9fafb; color: #6b7280; }}
  .risk-item {{ border-left: 3px solid #e2e8f0; padding: 12px 16px; margin-bottom: 16px;
                background: #fafbfc; border-radius: 0 6px 6px 0; }}
  .risk-item.level-high {{ border-left-color: #dc2626; }}
  .risk-item.level-medium {{ border-left-color: #f59e0b; }}
  .risk-head {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
  .clause-ref {{ font-weight: 600; color: #334155; }}
  .category-tag {{ background: #e0e7ff; color: #4338ca; padding: 1px 8px;
                   border-radius: 10px; font-size: 11px; }}
  .risk-desc {{ color: #334155; margin-bottom: 6px; }}
  .risk-suggestion {{ color: #166534; font-size: 14px; }}
  .risk-reason {{ color: #64748b; font-size: 13px; margin-top: 4px; }}
  .refs {{ padding-left: 18px; margin: 8px 0 0 0; font-size: 13px; color: #475569; }}
  .refs blockquote {{ margin: 4px 0; padding: 8px 12px; background: #f1f5f9;
                       border-radius: 4px; font-size: 12px; }}
  .ref-mark {{ color: #059669; }}
  .ref-mark.neg {{ color: #d97706; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
  th {{ background: #f8fafc; font-weight: 600; color: #475569; }}
  .ki-table td:first-child {{ width: 180px; color: #64748b; font-weight: 500; }}
  .clauses-table td {{ font-size: 13px; }}
  .empty {{ color: #94a3b8; font-style: italic; }}
  footer {{ text-align: center; color: #94a3b8; font-size: 12px; margin-top: 40px; }}
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>{title}</h1>
    <div class="meta">合同类型：{doc_type} · 生成时间：{now}</div>
  </header>

  <div class="summary">{summary}</div>

  <div class="metrics">{cards_html}</div>

  {highlights_html}
  {ki_html}
  {risks_html}
  {clauses_html}

  <footer>
    本报告由企业合规审查系统自动生成 · {now}
  </footer>
</div>
</body>
</html>"""


def _ensure_dir(path_str: str) -> Path:
    p = Path(path_str)
    p.mkdir(parents=True, exist_ok=True)
    return p


def generate_reports_for_review(
    review_id: str,
    report_data: dict,
    *,
    compliance_doc_id: str | None = None,
) -> dict:
    """生成审查报告并落盘（HTML + Word + PDF 三路并行，单格式失败不中断）。"""
    from app.compliance.reporting.exporters.pdf_exporter import export_pdf
    from app.compliance.reporting.exporters.word_exporter import export_word

    report_dir = _ensure_dir(settings.compliance_report_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    html_path = report_dir / f"review-{review_id}-{timestamp}.html"
    word_path = report_dir / f"review-{review_id}-{timestamp}.docx"
    pdf_path = report_dir / f"review-{review_id}-{timestamp}.pdf"

    html_content = render_html(report_data)
    html_path.write_text(html_content, encoding="utf-8")
    logger.info("report html generated: %s (%d bytes)", html_path, html_path.stat().st_size)

    word_abs = export_word(report_data, str(report_dir)) or None
    pdf_abs = export_pdf(html_content, str(pdf_path))

    return {"html": str(html_path), "word": word_abs, "pdf": pdf_abs}
