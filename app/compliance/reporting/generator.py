"""报告生成器 — 组装 HTML 报告并落盘.

使用 Jinja2 ``select_autoescape`` 保证所有用户/LLM 输出自动 HTML 转义，
杜绝存储型 XSS。

生成器只负责「报告数据 dict → 文件 bytes → 落盘」，不做业务逻辑。
"""

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, select_autoescape

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_ENV = Environment(autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


_LEVEL_META = {
    "high": {"label": "高风险", "fg": "#dc2626", "bg": "#fef2f2", "css": "high"},
    "medium": {"label": "中风险", "fg": "#d97706", "bg": "#fffbeb", "css": "medium"},
    "low": {"label": "低风险", "fg": "#6b7280", "bg": "#f9fafb", "css": "low"},
}


def _level_meta(level: str) -> dict:
    return _LEVEL_META.get(level, _LEVEL_META["low"])


_REPORT_TEMPLATE_STR = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{{ title }} — 合规审查报告</title>
<meta name="referrer" content="no-referrer">
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:;">
<style>
  * { box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; padding: 24px; background: #f8fafc; color: #1e293b; line-height: 1.6; }
  .container { max-width: 960px; margin: 0 auto; }
  header { background: linear-gradient(135deg, #1e40af, #2563eb); color: white;
           padding: 32px; border-radius: 12px; margin-bottom: 24px; }
  header h1 { margin: 0 0 8px 0; font-size: 24px; }
  header .meta { opacity: 0.85; font-size: 13px; }
  .summary { background: white; padding: 20px 24px; border-radius: 8px;
              border-left: 4px solid #2563eb; margin-bottom: 24px; font-size: 15px; white-space: pre-wrap; }
  .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
  .metric { background: white; border-radius: 8px; padding: 16px 20px; }
  .metric.total { border-left: 4px solid #1e293b; background: #f8fafc; }
  .metric-label { font-size: 12px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
  .metric-value { font-size: 32px; font-weight: 700; margin-top: 4px; }
  .section { background: white; border-radius: 8px; padding: 24px; margin-bottom: 24px; }
  .section h2 { margin-top: 0; font-size: 18px; padding-bottom: 12px;
                 border-bottom: 1px solid #e2e8f0; }
  .highlights { border: 2px solid #fef2f2; }
  .highlights ul { margin: 0; padding-left: 20px; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 12px;
           font-size: 12px; font-weight: 600; margin-right: 8px; }
  .badge.high { background: #fef2f2; color: #dc2626; }
  .badge.medium { background: #fffbeb; color: #d97706; }
  .badge.low { background: #f9fafb; color: #6b7280; }
  .risk-item { border-left: 3px solid #e2e8f0; padding: 12px 16px; margin-bottom: 16px;
                background: #fafbfc; border-radius: 0 6px 6px 0; }
  .risk-item.level-high { border-left-color: #dc2626; }
  .risk-item.level-medium { border-left-color: #f59e0b; }
  .risk-head { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap; }
  .clause-ref { font-weight: 600; color: #334155; }
  .category-tag { background: #e0e7ff; color: #4338ca; padding: 1px 8px;
                   border-radius: 10px; font-size: 11px; }
  .risk-desc { color: #334155; margin-bottom: 6px; white-space: pre-wrap; }
  .risk-suggestion { color: #166534; font-size: 14px; white-space: pre-wrap; }
  .risk-reason { color: #64748b; font-size: 13px; margin-top: 4px; white-space: pre-wrap; }
  .refs { padding-left: 18px; margin: 8px 0 0 0; font-size: 13px; color: #475569; }
  .refs blockquote { margin: 4px 0; padding: 8px 12px; background: #f1f5f9;
                       border-radius: 4px; font-size: 12px; white-space: pre-wrap; }
  .ref-mark { color: #059669; }
  .ref-mark.neg { color: #d97706; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }
  th { background: #f8fafc; font-weight: 600; color: #475569; }
  .ki-table td:first-child { width: 180px; color: #64748b; font-weight: 500; }
  .clauses-table td { font-size: 13px; }
  .empty { color: #94a3b8; font-style: italic; }
  footer { text-align: center; color: #94a3b8; font-size: 12px; margin-top: 40px; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>{{ title }}</h1>
    <div class="meta">合同类型：{{ doc_type }} · 生成时间：{{ now }}</div>
  </header>

  <div class="summary">{{ summary }}</div>

  <div class="metrics">
    <div class="metric" style="border-left:4px solid {{ level_meta('high').fg }}; background:{{ level_meta('high').bg }};">
      <div class="metric-label">高风险</div>
      <div class="metric-value" style="color:{{ level_meta('high').fg }};">{{ risk_counts.high }}</div>
    </div>
    <div class="metric" style="border-left:4px solid {{ level_meta('medium').fg }}; background:{{ level_meta('medium').bg }};">
      <div class="metric-label">中风险</div>
      <div class="metric-value" style="color:{{ level_meta('medium').fg }};">{{ risk_counts.medium }}</div>
    </div>
    <div class="metric" style="border-left:4px solid {{ level_meta('low').fg }}; background:{{ level_meta('low').bg }};">
      <div class="metric-label">低风险</div>
      <div class="metric-value" style="color:{{ level_meta('low').fg }};">{{ risk_counts.low }}</div>
    </div>
    <div class="metric total">
      <div class="metric-label">风险总数</div>
      <div class="metric-value">{{ risk_counts.total }}</div>
    </div>
  </div>

  {% if highlights %}
  <div class="section highlights">
    <h2>⚠ 高风险快速定位</h2>
    <ul>
      {% for h in highlights %}
      <li><b>{{ h.clause_number or '-' }}</b> — {{ h.description }}</li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}

  {% if key_info %}
  <div class="section">
    <h2>合同关键信息</h2>
    <table class="ki-table">
      <tbody>
        {% for k, v in key_info.items() %}
          {% if v %}
          <tr><td>{{ k }}</td><td>{{ v }}</td></tr>
          {% endif %}
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  <div class="section">
    <h2>风险详情（{{ risks|length }} 项）</h2>
    {% if not risks %}
    <p class="empty">未检出风险项。</p>
    {% endif %}
    {% for r in risks %}
      {% set lvl = level_meta(r.risk_level or 'low') %}
      <div class="risk-item level-{{ lvl.css }}">
        <div class="risk-head">
          <span class="badge {{ lvl.css }}">{{ lvl.label }}</span>
          <span class="clause-ref">条款 {{ r.clause_number or '-' }}</span>
          {% if r.risk_category %}<span class="category-tag">{{ r.risk_category }}</span>{% endif %}
        </div>
        <div class="risk-desc">{{ r.description }}</div>
        <div class="risk-suggestion"><b>修改建议：</b>{{ r.suggestion or '（无）' }}</div>
        {% if r.suggestion_reason %}
        <div class="risk-reason"><b>理由：</b>{{ r.suggestion_reason }}</div>
        {% endif %}
        {% set refs = r.legal_references or [] %}
        {% if refs %}
        <ul class="refs">
          {% for rf in refs %}
            {% set mark = '✓ 已校验' if rf.verified else '⚠ 需人工核实' %}
          <li>
            <b>{{ rf.ref_name }}</b> {% if rf.ref_article %}{{ rf.ref_article }}{% endif %}
            <small class="ref-mark">{{ mark }}</small>
            <blockquote>{{ rf.ref_content }}</blockquote>
          </li>
          {% endfor %}
        </ul>
        {% endif %}
      </div>
    {% endfor %}
  </div>

  {% if clauses %}
  <div class="section">
    <h2>合同条款（{{ clauses|length }} 条）</h2>
    <table class="clauses-table">
      <thead>
        <tr><th>条款号</th><th>标题/摘要</th><th>类型</th></tr>
      </thead>
      <tbody>
        {% for c in clauses %}
        <tr>
          <td>{{ c.clause_number or '' }}</td>
          <td>{{ c.title or ((c.content or '')[:60] ~ ('...' if (c.content or '')|length > 60 else '')) }}</td>
          <td>{{ c.clause_type or '' }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  <footer>
    本报告由企业合规审查系统自动生成 · {{ now }}
  </footer>
</div>
</body>
</html>
"""


_TEMPLATE = _ENV.from_string(_REPORT_TEMPLATE_STR)


def render_html(report_data: dict) -> str:
    """把 ReporterAgent 产出的 report_data 渲染成安全转义的 HTML 字符串。"""
    doc_info = report_data.get("doc_info") or {}
    title = doc_info.get("original_filename") or "合同审查报告"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    risk_counts = report_data.get("risk_counts") or {"high": 0, "medium": 0, "low": 0, "total": 0}
    for k in ("high", "medium", "low", "total"):
        risk_counts.setdefault(k, 0)

    return _TEMPLATE.render(
        title=title,
        doc_type=doc_info.get("doc_type", "other"),
        now=now,
        summary=report_data.get("summary", ""),
        risk_counts=risk_counts,
        key_info=report_data.get("key_info") or {},
        risks=report_data.get("risks") or [],
        clauses=report_data.get("clauses") or [],
        highlights=report_data.get("highlights") or [],
        level_meta=_level_meta,
    )


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
    """生成审查报告并落盘（HTML + Word + PDF 三路并行，单格式失败不中断）."""
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
