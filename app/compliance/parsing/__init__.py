"""解析模块包（app/compliance/parsing/）—— 条款拆分与统一解析入口。

复用现有 `app/rag/pipeline.load_document` 读全文，`clause_splitter` 按「第X条」
切条款，`parser` 编排加载→拆分→类型判定，产出审查流水线的 parse 节点输入。

用法：
    from app.compliance.parsing import build_parsing_result, classify_doc_type
    result = build_parsing_result(document_id, file_path, mime_type)
"""

from app.compliance.parsing.clause_splitter import (
    split_clauses_from_text,
)
from app.compliance.parsing.parser import (
    build_parsing_result,
    classify_doc_type,
    dump_result,
    load_text,
    parse_document,
)

__all__ = [
    "split_clauses_from_text",
    "load_text",
    "classify_doc_type",
    "build_parsing_result",
    "parse_document",
    "dump_result",
]