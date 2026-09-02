"""Compliance regulation ORM models — law knowledge base (regulation + articles)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Date, Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class ComplianceRegulation(Base):
    """法规库：法律法规/司法解释/部门规章的元数据。向量由独立 PGVector collection
    `compliance_regulations` 承载（langchain_pg_embedding 表），本表存结构化元数据。"""

    __tablename__ = "compliance_regulations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String(500), nullable=False)  # 法规名称
    # law / admin_regulation / judicial_interpretation / local_rule
    regulation_type = Column(String(30), nullable=False)
    publish_date = Column(Date, nullable=True)
    effective_date = Column(Date, nullable=True)
    expire_date = Column(Date, nullable=True)
    status = Column(String(20), default="active")  # active/expired/amended
    source = Column(String(100), nullable=True)
    # 原始文件路径（摄入时复制到 compliance_regulation_dir）
    file_path = Column(String(500), nullable=True)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ComplianceRegulationArticle(Base):
    """法规条款：向量化存储的最小单元（metadata 由 langchain-postgres 关联），
    本表存结构化元数据（条款号、章节、原文），与向量通过 article_number + regulation_id 关联。"""

    __tablename__ = "compliance_regulation_articles"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    regulation_id = Column(
        String(36),
        ForeignKey("compliance_regulations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    article_number = Column(String(50), nullable=False)  # 如「第十条」
    chapter = Column(String(200), nullable=True)
    section = Column(String(200), nullable=True)
    content = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))