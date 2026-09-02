"""统一解析入口 — 加载合同全文 → 拆条款 → 判定类型。

设计文档 F01：复用现有 `app/rag/pipeline.load_document` 加载 PDF/DOCX/TXT 等格式，
再由 `clause_splitter` 做条款级拆分；合同类型（labor_contract/nda/procurement/
service_agreement/other）在 MVP 用规则关键词判定（test 模式确定性、无 LLM 依赖），
openai 模式可换 LLM 判定（预留）。关键信息(key_info)提取由审查流水线的 extractor
节点（agents/extractor.py，Step 8）负责，本模块只产出解析结果骨架。

产出 dict：
    {
      "raw_text": 全文,
      "doc_type": 类型,
      "doc_confidence": 置信度,
      "clauses": [{clause_number, title, content, page_number}, ...],
      "key_info": {},   # 由 extractor 后续填充
    }
"""

import json
from typing import Optional

from langchain_core.documents import Document

from app.logging_config import get_logger

logger = get_logger(__name__)


# 合同类型关键词规则（MVP 用；openai 模式可升级为 LLM 判定）。
# 每类给一组触发词，命中最高分获胜；均未命中 → other。
_DOC_TYPE_RULES: list[tuple[str, float, list[str]]] = [
    ("labor_contract", 0.9, ["劳动合同", "劳务合同", "劳动关系", "工资", "社会保险"]),
    ("nda", 0.9, ["保密协议", "竞业限制", "商业秘密", "confidentiality", "非公开"]),
    ("procurement", 0.9, ["采购合同", "供货", "乙方交付货物", "订单", "采购"]),
    ("service_agreement", 0.8, ["服务合同", "咨询服务", "服务费", "技术支持"]),
]


def load_text(file_path: str, mime_type: str) -> str:
    """加载合同全文并拼接（复用现有 load_document）。返回单段文本。"""
    # 延迟 import，避免 parsing → rag.pipeline → (循环) 的潜在交叉
    from app.rag.pipeline import load_document

    docs: list[Document] = load_document(file_path, mime_type)
    return "\n\n".join(d.page_content for d in docs)


def classify_doc_type(text: str, llm=None) -> tuple[str, float]:
    """判定合同类型。规则命中取最高分；未命中小分判 other。

    llm 为预留参数：openai 模式可改为 LLM 判定，MVP 用规则保证 test 模式确定性。
    """
    text_for_scan = (text or "").lower()
    best: tuple[str, float] = ("other", 0.5)
    for doc_type, score, keywords in _DOC_TYPE_RULES:
        hits = sum(1 for kw in keywords if kw.lower() in text_for_scan)
        if hits and hits / len(keywords) >= 0.2:
            # 命中比例越高置信度越高（保底 0.6，上限 0.98）
            conf = round(min(0.98, 0.6 + 0.2 * hits), 2)
            if conf > best[1]:
                best = (doc_type, conf)
    logger.info("classify_doc_type -> %s (%.2f)", best[0], best[1])
    return best


def build_parsing_result(
    document_id: str,
    file_path: str,
    mime_type: str,
    llm=None,
    page_map: Optional[dict[int, int]] = None,
) -> dict:
    """完整解析：读全文 → 拆条款 → 判类型。返回解析结果 dict。

    Args:
        document_id: 业务表 documents.id。
        file_path / mime_type: 文件路径与 MIME（传给 load_document）。
        llm: 预留（openai 模式类型判定/条款校正）。
        page_map: 可选，条款→页码映射（MVP 由 Document.metadata.page 收集，见 parse_with_pages）。
    """
    raw_text = load_text(file_path, mime_type)
    doc_type, confidence = classify_doc_type(raw_text, llm)

    # 拆条款（页号后补）
    from app.compliance.parsing.clause_splitter import split_clauses_from_text

    clauses_raw = split_clauses_from_text(raw_text, llm)
    clauses = [
        {
            "clause_number": c.get("clause_number", ""),
            "title": c.get("title"),
            "content": c.get("content", ""),
            "page_number": page_map.get(i, None) if page_map else None,
        }
        for i, c in enumerate(clauses_raw)
    ]

    return {
        "document_id": document_id,
        "file_path": file_path,
        "mime_type": mime_type,
        "raw_text": raw_text,
        "doc_type": doc_type,
        "doc_confidence": confidence,
        "clauses": clauses,
        "key_info": {},
    }


def collect_page_map(docs: list[Document]) -> Optional[dict]:
    """从 load_document 的 Document 列表收集页面信息（仅 PDF 有 page 元数据）。

    返回 {clause起始行index: 页码} 不直接适用；MVP 采用简化：记录各页文本行号。
    实际页号映射在 ingestion/chain 层按需补，此处保留钩子。返回 None 表示未分页。
    """
    page_map: dict[int, int] = {}
    offset = 0
    for d in docs:
        page = d.metadata.get("page")
        if page is not None:
            lines = d.page_content.count("\n")
            page_map[offset] = page
            offset += lines + 1
    return page_map or None


def parse_document(
    document_id: str,
    file_path: str,
    mime_type: str,
    llm=None,
) -> dict:
    """便捷入口：加载含页号的信息并调用 build_parsing_result。"""
    from app.rag.pipeline import load_document

    raw_docs = load_document(file_path, mime_type)
    page_map = collect_page_map(raw_docs)
    result = build_parsing_result(document_id, file_path, mime_type, llm, page_map)
    # build_parsing_result 再次 load_document 会重复读文件；MVP 可接受（文本量小）。
    return result


def dump_result(result: dict) -> str:
    """把解析结果转 JSON 字符串（供审查任务记录/调试）。"""
    return json.dumps(result, ensure_ascii=False, default=str)