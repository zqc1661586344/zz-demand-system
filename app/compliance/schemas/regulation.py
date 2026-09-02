"""Compliance regulation schemas — Pydantic models for regulation knowledge base API."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class RegulationIngestRequest(BaseModel):
    """摄入法规：title 必填；file_path 为空时按 JSON 占位结构摄入（空库/后补法条阶段用）。

    articles 可选：直接提供条款数组（[{article_number, chapter, content}]），
    免去再从原文文件拆分。
    """

    title: str
    regulation_type: str = "law"  # law / admin_regulation / judicial_interpretation / local_rule
    file_path: str = ""  # 已上传的法规原文文件路径（可选）
    publish_date: Optional[str] = None
    effective_date: Optional[str] = None
    expire_date: Optional[str] = None
    source: Optional[str] = None
    articles: list[dict] | None = None  # [{article_number, chapter, section, content}]


class RegulationArticleResponse(BaseModel):
    id: str
    article_number: str
    chapter: Optional[str] = None
    section: Optional[str] = None
    content: str
    sort_order: Optional[int] = None


class RegulationResponse(BaseModel):
    id: str
    title: str
    regulation_type: str
    status: str
    source: Optional[str] = None
    publish_date: Optional[date] = None
    effective_date: Optional[date] = None
    expire_date: Optional[date] = None
    article_count: int = 0
    created_at: datetime
    model_config = {"from_attributes": True}


class RegulationSearchRequest(BaseModel):
    query: str
    top_k: int = 5
    regulation_type: Optional[str] = None
    contract_type: Optional[str] = None  # 预留：按合同类型过滤


class RegulationSearchHit(BaseModel):
    article_id: str
    regulation_id: str
    regulation_title: str
    article_number: str
    content: str
    score: float


class RegulationSearchResponse(BaseModel):
    hits: list[RegulationSearchHit]