"""PDF 报告导出（app/compliance/reporting/exporters/pdf_exporter.py）。

方案：weasyprint 接收 HTML 字符串直接转 PDF，无需中间文件。
设计文档 2.3 / P1：PDF 导出用 weasyprint（HTML→PDF），样式可控。
"""

from __future__ import annotations

from pathlib import Path

from app.logging_config import get_logger

logger = get_logger(__name__)


def export_pdf(html_content: str, output_path: str | Path) -> str | None:
    """把 HTML 字符串转成 PDF 文件。

    Args:
        html_content: 完整的 HTML 字符串（含内联 CSS）。
        output_path: 输出 PDF 路径（.pdf 后缀）。

    Returns:
        绝对路径 str；失败返回 None（weasyprint 是 P1 依赖，没装时降级返回 None）。
    """
    try:
        from weasyprint import HTML  # type: ignore
    except ImportError:
        logger.warning("weasyprint not installed, PDF export skipped (pip install weasyprint)")
        return None

    try:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        HTML(string=html_content).write_pdf(str(out))
        size = out.stat().st_size
        logger.info("pdf exported: %s (%d bytes)", out, size)
        return str(out.resolve())
    except Exception as e:  # noqa: BLE001
        logger.exception("pdf export failed: %s", e)
        return None
