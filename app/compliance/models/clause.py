"""Compliance clause ORM models — clause (审查最小单元) and key info extraction."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class ComplianceClause(Base):
    """合同条款：审查的最小单元，按「第X条」正则 + LLM 校正拆分得到。

    clause_type 如：parties / term / payment / delivery / penalty / ip /
    confidentiality / dispute / termination / force_majeure / other
    """

    __tablename__ = "compliance_clauses"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    compliance_doc_id = Column(
        String(36),
        ForeignKey("compliance_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clause_number = Column(String(50), nullable=True)  # 如「第3条第2款」，原文编号
    clause_type = Column(String(50), nullable=True)
    title = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ComplianceKeyInfo(Base):
    """合同关键信息：合同主体、金额、期限、付款条件、违约责任、知识产权、
    保密期限、争议解决等字段级提取（LLM 结构化输出）。"""

    __tablename__ = "compliance_key_info"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    compliance_doc_id = Column(
        String(36),
        ForeignKey("compliance_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    field_key = Column(String(50), nullable=False)  # party_a / party_b / amount / term / ...
    field_value = Column(Text, nullable=True)
    confidence = Column(Float, nullable=True)
    clause_id = Column(String(36), ForeignKey("compliance_clauses.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))