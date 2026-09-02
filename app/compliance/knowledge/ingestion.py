"""法规摄入流程 — regulation + articles 入库 + 向量化。

两种输入源（设计文档 §5.7.2）：
  1. **JSON 结构化**（空库阶段主路径）：调用方直接给 title + articles 数组
     [{article_number, chapter, section, content}]。每条款写入
     compliance_regulation_articles 表 + 向量化进 collection=compliance_regulations。
  2. **原文文件**（PDF/DOCX/TXT）：复用 app/rag/pipeline.load_document() 加载全文，
     走条款拆分器（parsing/clause_splitter，Step 6）切成条款再入库。MVP 阶段
     file_path 为空时按纯 JSON 摄入（见 RegulationIngestRequest 默认）。

摄入流程：建 ComplianceRegulation → 逐条建 ComplianceRegulationArticle →
         add_regulations_to_store(向量文档) → 提交。幂等：同 title 重复摄入会
         先删除旧法规（含向量）再重建，避免累积。
"""

import uuid
from pathlib import Path

from langchain_core.documents import Document
from sqlalchemy.orm import Session

from app.compliance.knowledge.vector_store import (
    add_regulations_to_store,
    delete_regulations_from_store,
)
from app.compliance.models.regulation import ComplianceRegulation, ComplianceRegulationArticle
from app.config import settings
from app.database import SessionLocal
from app.logging_config import get_logger

logger = get_logger(__name__)


def _parse_date(value: str | None):
    """把 'YYYY-MM-DD' 或 None 转成 date（无法解析返回 None，不抛错）。"""
    if not value:
        return None
    from datetime import date

    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        logger.warning("skip invalid date: %r", value)
        return None


def _build_article_doc(
    regulation: ComplianceRegulation,
    article: ComplianceRegulationArticle,
) -> Document:
    """把一个法规条款转成带 metadata 的向量 Document。"""
    return Document(
        page_content=article.content,
        metadata={
            "article_id": str(article.id),
            "regulation_id": str(regulation.id),
            "article_number": article.article_number,
            "chapter": article.chapter or "",
            "section": article.section or "",
            "regulation_type": regulation.regulation_type,
            "title": regulation.title,
            "status": regulation.status,
        },
    )


def ingest_regulation(
    title: str,
    regulation_type: str,
    articles: list[dict],
    *,
    publish_date: str | None = None,
    effective_date: str | None = None,
    expire_date: str | None = None,
    source: str | None = None,
    file_path: str | None = None,
    db: Session | None = None,
) -> dict:
    """摄入一部法规（JSON 结构化）。

    Args:
        title: 法规名称（必填）。
        regulation_type: law / admin_regulation / judicial_interpretation / local_rule。
        articles: 条款数组，每项 {article_number, chapter?, section?, content}。
        publish_date/effective_date/expire_date/source/file_path: 法规元数据（可选）。
        db: 可选 Session；不传则内部开独立会话（供后台任务）。

    Returns:
        {"regulation_id": str, "article_count": int}；异常时记录日志并抛出。
    """
    own_db = db is None
    session: Session = db or SessionLocal()
    try:
        # 幂等：同名法规已存在则先清理（含向量 + 条款表行），避免累积/重复召回
        existing = (
            session.query(ComplianceRegulation)
            .filter(ComplianceRegulation.title == title)
            .first()
        )
        if existing:
            delete_regulations_from_store(str(existing.id))
            session.query(ComplianceRegulationArticle).filter(
                ComplianceRegulationArticle.regulation_id == str(existing.id)
            ).delete()
            session.delete(existing)
            session.commit()
            logger.info("re-ingest regulation (replaced old): %s", title)

        reg = ComplianceRegulation(
            id=str(uuid.uuid4()),
            title=title,
            regulation_type=regulation_type,
            publish_date=_parse_date(publish_date),
            effective_date=_parse_date(effective_date),
            expire_date=_parse_date(expire_date),
            source=source,
        )
        if file_path:
            reg.file_path = file_path
        session.add(reg)
        # 逐条建条款
        article_rows: list[ComplianceRegulationArticle] = []
        vec_docs: list[Document] = []
        for idx, a in enumerate(articles):
            row = ComplianceRegulationArticle(
                id=str(uuid.uuid4()),
                regulation_id=str(reg.id),
                article_number=(a.get("article_number") or f"第{idx + 1}条"),
                chapter=a.get("chapter"),
                section=a.get("section"),
                content=a.get("content") or "",
                sort_order=idx,
            )
            article_rows.append(row)
            session.add(row)
            if row.content:
                vec_docs.append(_build_article_doc(reg, row))
        session.commit()
        # 向量化（单独提交后写向量，异常不影响已落库的元数据）
        if vec_docs:
            add_regulations_to_store(vec_docs)
        logger.info(
            "regulation ingested: %s (%s, %d articles)", title, regulation_type, len(article_rows)
        )
        return {"regulation_id": str(reg.id), "article_count": len(article_rows)}
    finally:
        if own_db:
            session.close()


def ingest_from_file(
    file_path: str,
    title: str,
    regulation_type: str,
    db: Session | None = None,
) -> dict:
    """从法规原文文件摄入（PDF/DOCX/TXT）—— 复用 load_document + 条款拆分。

    MVP 占位：条款拆分器尚未就绪时，把全文切成按「第X条」正则粗切的条款；
    若 parsing.clause_splitter 可用则用之。（当前实现依赖 clause_splitter，
    为空 JSON articles 时走 ingest_regulation 的纯结构化路径。）
    """
    # 延迟 import，避免循环依赖（ingestion → parsing → rag.pipeline）
    from app.compliance.parsing.clause_splitter import (
        split_clauses_from_text,
    )

    from app.rag.pipeline import load_document

    p = Path(file_path)
    content_type = _guess_mime(p)
    raw_docs = load_document(str(p), content_type)
    full_text = "\n\n".join(d.page_content for d in raw_docs)
    clauses = split_clauses_from_text(full_text)
    articles = [
        {
            "article_number": c["clause_number"] or f"第{i + 1}条",
            "content": c["content"],
        }
        for i, c in enumerate(clauses)
    ]
    return ingest_regulation(
        title=title,
        regulation_type=regulation_type,
        articles=articles,
        file_path=str(p),
        db=db,
    )


def _guess_mime(path: Path) -> str:
    """按文件后缀推断 MIME（与 app/api/documents.py 的映射一致）。"""
    suffix = path.suffix.lower()
    mapping = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".csv": "text/csv",
        ".html": "text/html",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".toml": "application/toml",
    }
    return mapping.get(suffix, "text/plain")


def get_regular_articles_for_verification(
    regulation_id: str, db: Session | None = None
) -> list[str]:
    """取某部法规的全部条款原文（供引用校验的候选池）。空库返回 []。"""
    own_db = db is None
    session: Session = db or SessionLocal()
    try:
        rows = (
            session.query(ComplianceRegulationArticle)
            .filter(ComplianceRegulationArticle.regulation_id == regulation_id)
            .order_by(ComplianceRegulationArticle.sort_order)
            .all()
        )
        return [r.content for r in rows]
    finally:
        if own_db:
            session.close()