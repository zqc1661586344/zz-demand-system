"""Regulation Service — 法规知识库业务层。

职责：法规 CRUD + 条款管理 + 摄入入口 + 混合检索封装。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.compliance.knowledge import ingestion as knowledge_ingestion
from app.compliance.knowledge import retrieval as knowledge_retrieval
from app.compliance.models.regulation import (
    ComplianceRegulation,
    ComplianceRegulationArticle,
)
from app.logging_config import get_logger

logger = get_logger(__name__)


class RegulationService:
    """法规业务层。"""

    # ============== 查询 ==============

    @staticmethod
    def list_regulations(
        db: Session,
        regulation_type: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
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
            enriched.append(
                {
                    "id": r.id,
                    "title": r.title,
                    "regulation_type": r.regulation_type,
                    "status": r.status,
                    "source": r.source,
                    "publish_date": r.publish_date.isoformat() if r.publish_date else None,
                    "effective_date": r.effective_date.isoformat() if r.effective_date else None,
                    "article_count": count,
                    "created_at": r.created_at.isoformat(),
                }
            )
        return enriched, total

    @staticmethod
    def get_regulation(db: Session, regulation_id: str) -> Optional[dict]:
        r = db.get(ComplianceRegulation, regulation_id)
        if not r:
            return None
        articles = (
            db.query(ComplianceRegulationArticle)
            .filter(ComplianceRegulationArticle.regulation_id == regulation_id)
            .order_by(ComplianceRegulationArticle.sort_order)
            .all()
        )
        return {
            "regulation": {
                "id": r.id,
                "title": r.title,
                "regulation_type": r.regulation_type,
                "status": r.status,
                "source": r.source,
                "publish_date": r.publish_date.isoformat() if r.publish_date else None,
                "effective_date": r.effective_date.isoformat() if r.effective_date else None,
                "created_at": r.created_at.isoformat(),
            },
            "articles": [
                {
                    "id": a.id,
                    "article_number": a.article_number,
                    "chapter": a.chapter,
                    "section": a.section,
                    "content": a.content,
                    "sort_order": a.sort_order,
                }
                for a in articles
            ],
        }

    # ============== 摄入 ==============

    @staticmethod
    def create_regulation(
        db: Session,
        title: str,
        regulation_type: str = "law",
        file_path: Optional[str] = None,
        publish_date: Optional[str] = None,
        effective_date: Optional[str] = None,
        source: Optional[str] = None,
        articles: Optional[list[dict]] = None,
    ) -> dict:
        existing = (
            db.query(ComplianceRegulation).filter(ComplianceRegulation.title == title).first()
        )
        if existing:
            raise ValueError(f"regulation already exists (id={existing.id})")

        reg_id = str(uuid.uuid4())
        r = ComplianceRegulation(
            id=reg_id,
            title=title,
            regulation_type=regulation_type,
            publish_date=_parse_date(publish_date),
            effective_date=_parse_date(effective_date),
            status="active",
            source=source,
            file_path=file_path or None,
            version=1,
            created_at=datetime.now(timezone.utc),
        )
        db.add(r)

        if articles:
            _bulk_articles(db, reg_id, articles)
            db.commit()
            logger.info("Regulation ingested (inline): %s (%d articles)", title, len(articles))
        elif file_path:
            db.commit()
            db.refresh(r)
            try:
                knowledge_ingestion.ingest_from_file(file_path, title, regulation_type, db=db)
                db.commit()
                logger.info("Regulation ingested (file): %s", title)
            except Exception as exc:
                logger.error("Regulation ingest failed: %s", exc)
                raise RuntimeError(f"ingest failed: {exc}") from exc
        else:
            db.commit()
            logger.info("Regulation registered (no articles): %s", title)

        db.refresh(r)
        count = (
            db.query(ComplianceRegulationArticle)
            .filter(ComplianceRegulationArticle.regulation_id == reg_id)
            .count()
        )
        return {
            "id": reg_id,
            "title": title,
            "article_count": count,
            "status": r.status,
        }

    @staticmethod
    def delete_regulation(db: Session, regulation_id: str) -> bool:
        r = db.get(ComplianceRegulation, regulation_id)
        if not r:
            return False
        db.query(ComplianceRegulationArticle).filter(
            ComplianceRegulationArticle.regulation_id == regulation_id
        ).delete()
        db.delete(r)
        db.commit()
        logger.info("Regulation deleted: %s", r.title)
        return True

    @staticmethod
    def seed_from_directory(db: Session, seed_dir: Path) -> dict:
        """从 JSON 目录批量加载法规种子。"""
        if not seed_dir.exists():
            raise FileNotFoundError(f"seed dir not found: {seed_dir}")

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
            _bulk_articles(db, reg_id, data.get("articles", []))
            loaded += 1

        db.commit()
        logger.info("Seed regulations: loaded=%d skipped=%d", loaded, skipped)
        return {"loaded": loaded, "skipped": skipped}

    # ============== 检索 ==============

    @staticmethod
    def search(
        db: Session,
        query: str,
        top_k: int = 5,
        regulation_type: Optional[str] = None,
    ) -> list[dict]:
        """混合检索 —— 语义向量 + 关键词。"""
        try:
            return knowledge_retrieval.search(
                query=query,
                top_k=top_k,
                regulation_type=regulation_type,
                db=db,
            )
        except Exception as exc:
            logger.error("Regulation search failed: %s", exc)
            return []


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _bulk_articles(db: Session, reg_id: str, articles: list[dict]):
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
