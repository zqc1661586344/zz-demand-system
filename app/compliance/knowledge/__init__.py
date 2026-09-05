"""法规知识库包（app/compliance/knowledge/）。

对外暴露知识库能力：
- vector_store:   独立 PGVector collection=compliance_regulations（读写检索）
- ingestion:      法规摄入（JSON 结构化 / 原文文件 → 条款拆分 → 向量化）
- retrieval:      法规语义检索服务（组装结构化命中）
- citation_verifier: 引用强制校验（防幻觉，逐字匹配）

用法：
    from app.compliance.knowledge import ingest_regulation, search_regulations_lite
    result = ingest_regulation(title=..., regulation_type=..., articles=[...])
    hits = search_regulations_lite("试用期不得超过六个月")
"""

from app.compliance.knowledge.vector_store import (
    add_regulations_to_store,
    delete_regulations_from_store,
    get_regulation_vector_store,
)
from app.compliance.knowledge.ingestion import (
    ingest_from_file,
    ingest_regulation,
)
from app.compliance.knowledge.retrieval import (
    count_regulations,
    search as search_regulations_lite,
)
from app.compliance.knowledge.citation_verifier import (
    normalize_text,
    text_similarity,
    verify_citation,
    verify_references,
)

__all__ = [
    "get_regulation_vector_store",
    "add_regulations_to_store",
    "delete_regulations_from_store",
    "ingest_regulation",
    "ingest_from_file",
    "search_regulations_lite",
    "count_regulations",
    "normalize_text",
    "text_similarity",
    "verify_citation",
    "verify_references",
]