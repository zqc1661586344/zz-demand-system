"""法规知识库 API — 法规摄入、向量检索、CRUD。

与 app/compliance/models/regulation.py + schemas/regulation.py 对齐。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.logging_config import get_logger
from app.dependencies import get_current_user
from app.models.user import User
from app.compliance.knowledge import ingestion as knowledge_ingestion
from app.compliance.knowledge import retrieval as knowledge_retrieval
from app.compliance.models.regulation import (
    ComplianceRegulation,
    ComplianceRegulationArticle,
)
from app.compliance.schemas.regulation import (
    RegulationArticleResponse,
    RegulationIngestRequest,
    RegulationResponse,
    RegulationSearchRequest,
    RegulationSearchResponse,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/compliance/knowledge", tags=["compliance-knowledge"])


# ─── 法规库 CRUD ────────────────────────────────────────────────────────────────


@router.get("/regulations", response_model=dict)
def list_regulations(
    regulation_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(ComplianceRegulation)
    if regulation_type:
        q = q.filter(ComplianceRegulation.regulation_type == regulation_type)
    if status:
        q = q.filter(ComplianceRegulation.status == status)

    total = q.count()
    items = q.order_by(ComplianceRegulation.created_at.desc()).offset(offset).limit(limit).all()

    enriched = []
    for r in items:
        count = (
            db.query(ComplianceRegulationArticle)
            .filter(ComplianceRegulationArticle.regulation_id == r.id)
            .count()
        )
        d = RegulationResponse.model_validate(r).model_dump()
        d["article_count"] = count
        enriched.append(d)

    return {"items": enriched, "total": total, "limit": limit, "offset": offset}


@router.get("/regulations/{regulation_id}", response_model=dict)
def get_regulation(
    regulation_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    r = db.get(ComplianceRegulation, regulation_id)
    if not r:
        raise HTTPException(404, "regulation not found")
    articles = (
        db.query(ComplianceRegulationArticle)
        .filter(ComplianceRegulationArticle.regulation_id == regulation_id)
        .order_by(ComplianceRegulationArticle.sort_order)
        .all()
    )
    return {
        "regulation": RegulationResponse.model_validate(r).model_dump(),
        "articles": [RegulationArticleResponse.model_validate(a).model_dump() for a in articles],
    }


@router.post("/regulations", response_model=RegulationResponse)
def ingest_regulation(
    req: RegulationIngestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """摄入一部法规 —— 可选：原文文件路径或直接传 articles 数组。"""

    existing = (
        db.query(ComplianceRegulation).filter(ComplianceRegulation.title == req.title).first()
    )
    if existing:
        raise HTTPException(409, f"regulation already exists (id={existing.id})")

    reg_id = str(uuid.uuid4())

    r = ComplianceRegulation(
        id=reg_id,
        title=req.title,
        regulation_type=req.regulation_type,
        publish_date=_parse_date(req.publish_date),
        effective_date=_parse_date(req.effective_date),
        expire_date=_parse_date(req.expire_date),
        status="active",
        source=req.source,
        file_path=req.file_path or None,
        version=1,
        created_at=datetime.now(timezone.utc),
    )
    db.add(r)

    # articles 直接提供 —— 跳过文件解析
    if req.articles:
        _ingest_articles_json(db, reg_id, req.articles)
        db.commit()
        logger.info(
            "Regulation ingested (inline articles): %s (%d articles)", req.title, len(req.articles)
        )
    elif req.file_path:
        db.commit()
        db.refresh(r)
        try:
            knowledge_ingestion.ingest_from_file(
                req.file_path, req.title, req.regulation_type, db=db
            )
            db.commit()
            logger.info("Regulation ingested (from file): %s", req.title)
        except Exception as exc:
            logger.error("Regulation ingest failed: %s", exc)
            raise HTTPException(500, f"ingest failed: {exc}") from exc
    else:
        db.commit()
        logger.info("Regulation registered (no articles, to be filled): %s", req.title)

    db.refresh(r)
    count = (
        db.query(ComplianceRegulationArticle)
        .filter(ComplianceRegulationArticle.regulation_id == reg_id)
        .count()
    )
    resp = RegulationResponse.model_validate(r).model_dump()
    resp["article_count"] = count
    return resp


@router.delete("/regulations/{regulation_id}")
def delete_regulation(
    regulation_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    r = db.get(ComplianceRegulation, regulation_id)
    if not r:
        raise HTTPException(404, "regulation not found")
    db.query(ComplianceRegulationArticle).filter(
        ComplianceRegulationArticle.regulation_id == regulation_id
    ).delete()
    db.delete(r)
    db.commit()
    logger.info("Regulation deleted: %s", r.title)
    return {"ok": True, "deleted": regulation_id}


# ─── 法规向量检索 ────────────────────────────────────────────────────────────────


@router.post("/search", response_model=RegulationSearchResponse)
def search_regulations(
    req: RegulationSearchRequest,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """混合检索 —— 语义向量 + 关键词 RRF 融合。"""
    try:
        hits = knowledge_retrieval.search(
            query=req.query,
            top_k=req.top_k,
            regulation_type=req.regulation_type,
            db=db,
        )
    except Exception as exc:
        logger.error("Regulation search failed: %s", exc)
        raise HTTPException(500, f"search failed: {exc}") from exc

    return RegulationSearchResponse(hits=hits)


# ─── 批量摄入种子 ────────────────────────────────────────────────────────────────


@router.post("/seed")
def seed_regulations(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """从 app/compliance/knowledge/seed_data/labor_contract/ 批量加载种子法规。"""
    seed_dir = Path(__file__).resolve().parent.parent / "knowledge" / "seed_data" / "labor_contract"
    if not seed_dir.exists():
        raise HTTPException(404, f"seed dir not found: {seed_dir}")

    loaded = 0
    skipped = 0
    for f in sorted(seed_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Seed JSON parse error %s: %s", f.name, exc)
            continue

        existing = (
            db.query(ComplianceRegulation)
            .filter(ComplianceRegulation.title == data.get("title"))
            .first()
        )
        if existing:
            skipped += 1
            continue

        reg_id = str(uuid.uuid4())
        r = ComplianceRegulation(
            id=reg_id,
            title=data.get("title", f.stem),
            regulation_type=data.get("regulation_type", "law"),
            publish_date=_parse_date(data.get("publish_date")),
            effective_date=_parse_date(data.get("effective_date")),
            source=data.get("source"),
            status="active",
            version=1,
            created_at=datetime.now(timezone.utc),
        )
        db.add(r)
        _ingest_articles_json(db, reg_id, data.get("articles", []))
        loaded += 1

    db.commit()
    logger.info("Seed regulations: loaded=%d skipped=%d", loaded, skipped)
    return {"ok": True, "loaded": loaded, "skipped": skipped}


# ─── 内部辅助 ────────────────────────────────────────────────────────────────────


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _ingest_articles_json(db: Session, reg_id: str, articles: list[dict]):
    for idx, art in enumerate(articles):
        a = ComplianceRegulationArticle(
            id=str(uuid.uuid4()),
            regulation_id=reg_id,
            article_number=str(art.get("article_number") or f"第{idx + 1}条"),
            chapter=art.get("chapter"),
            section=art.get("section"),
            content=str(art.get("content") or ""),
            sort_order=idx + 1,
        )
        db.add(a)
