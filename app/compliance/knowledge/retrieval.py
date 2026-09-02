"""法规检索服务 — 向量语义检索 + 结构化元数据组装。

调用 `vector_store.search_regulations()` 拿到 (Document, score) 命中，
再从向量 metadata（摄入时写入）与 compliance_regulation_articles 表补全结构化字段，
组装成检索命中列表（供 /knowledge/regulations/search API 和 rag_skill 使用）。

空库/检索失败降级返回空列表，不抛异常（审查流程不受法规库缺失影响）。
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.compliance.knowledge.vector_store import search_regulations
from app.compliance.models.regulation import ComplianceRegulation, ComplianceRegulationArticle
from app.database import SessionLocal
from app.logging_config import get_logger

logger = get_logger(__name__)


def _hit_from_metadata(doc, score: float) -> dict:
    """从向量 Document 的 metadata 组装基本命中结构。"""
    meta = doc.metadata or {}
    return {
        "article_id": meta.get("article_id", ""),
        "regulation_id": meta.get("regulation_id", ""),
        "regulation_title": meta.get("title", meta.get("regulation_title", "")),
        "regulation_type": meta.get("regulation_type", ""),
        "article_number": meta.get("article_number", ""),
        "chapter": meta.get("chapter"),
        "content": doc.page_content,
        "score": round(float(score), 4),
    }


def search(
    query: str,
    top_k: int = 5,
    regulation_type: Optional[str] = None,
    contract_type: Optional[str] = None,
    db: Optional[Session] = None,
) -> list[dict]:
    """法规语义检索：返回命中列表（空库/异常返回 []）。

    Args:
        query: 检索文本（风险描述/条款内容）。
        top_k: 返回条数上限（默认取 settings.compliance_rag_top_k 由调用方控制）。
        regulation_type: 过滤法规类型（law/judicial_interpretation/...）。
        contract_type: 预留参数——合同类型过滤后续可映射到法规分类（MVP 不启用）。
        db: 可选 Session；不传则内部开独立会话（供后台任务用）。
    """
    try:
        hits = search_regulations(
            query,
            k=top_k,
            regulation_type=regulation_type,
        )
    except Exception as e:  # noqa: BLE001 — 检索失败降级空列表，不阻断审查
        logger.warning("regulation search failed for query=%r: %s", query[:50], e)
        return []

    result = [_hit_from_metadata(doc, score) for doc, score in hits]
    if not result:
        logger.info("regulation search: no hits for query=%r (empty KB?)", query[:50])
        return result

    # 补全结构化信息：向量 metadata 缺 title 时回表查询
    need_db = any(not h["regulation_title"] or not h["article_id"] for h in result)
    if need_db:
        _enrich_from_db(result, db)
    return result


def _enrich_from_db(result: list[dict], db: Optional[Session]) -> None:
    """向量 metadata 不全时，回查 compliance_regulation_articles 表补全。"""
    try:
        own_db = db is None
        session: Session = db or SessionLocal()
        try:
            for hit in result:
                if hit["article_id"]:
                    continue
                article = (
                    session.query(ComplianceRegulationArticle)
                    .filter(
                        ComplianceRegulationArticle.regulation_id == hit["regulation_id"],
                        ComplianceRegulationArticle.article_number == hit["article_number"],
                    )
                    .first()
                )
                if article:
                    hit["article_id"] = article.id
                    if not hit["regulation_title"]:
                        reg = (
                            session.query(ComplianceRegulation)
                            .filter(ComplianceRegulation.id == hit["regulation_id"])
                            .first()
                        )
                        hit["regulation_title"] = reg.title if reg else ""
        finally:
            if own_db:
                session.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("enrich regulation hits from DB failed: %s", e)


def count_regulations(db: Session) -> int:
    """法规总数（空库阶段也能用，用于审计/展示）。"""
    return db.query(ComplianceRegulation).count()