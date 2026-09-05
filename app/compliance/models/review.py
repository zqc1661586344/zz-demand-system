"""Compliance review ORM models — review task, risk items, and risk references."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)

from app.database import Base


class ComplianceReview(Base):
    """审查任务：对应一次 LangGraph 审查流水线运行，记录 thread_id 用于持久化/断点恢复。

    status: pending / running / pending_human / completed / failed
    (运行时由 Harness 写具体审查阶段：parsing/planning/reviewing/reflecting/generating)
    """

    __tablename__ = "compliance_reviews"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    compliance_doc_id = Column(
        String(36),
        ForeignKey("compliance_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id = Column(String(100), nullable=False, index=True)  # LangGraph thread_id
    status = Column(String(20), default="pending", index=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    high_risk_count = Column(Integer, default=0)
    medium_risk_count = Column(Integer, default=0)
    low_risk_count = Column(Integer, default=0)
    retry_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    template_id = Column(String(36), nullable=True)  # 可选，模板比对用（P1）
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ComplianceRisk(Base):
    """风险项：审查核心产出，5 类风险 × 3 级，附带修改建议与人工审核状态。"""

    __tablename__ = "compliance_risks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    review_id = Column(
        String(36),
        ForeignKey("compliance_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clause_id = Column(String(36), ForeignKey("compliance_clauses.id"), nullable=True)
    risk_level = Column(String(10), nullable=False, index=True)  # high/medium/low
    # legality/equality/clarity/completeness/reasonableness
    risk_category = Column(String(30), nullable=False)
    description = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=True)
    suggestion_reason = Column(Text, nullable=True)
    playbook_rule_id = Column(String(36), nullable=True)
    ai_confidence = Column(Float, default=1.0)
    human_confirmed = Column(Boolean, default=False)
    human_decision = Column(String(20), default="na")  # na/confirmed/modified/rejected
    human_note = Column(Text, nullable=True)
    human_reviewed_at = Column(DateTime, nullable=True)
    human_reviewed_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ComplianceRiskReference(Base):
    """风险项的法规/判例/Playbook 引用。verified 标记是否通过原文逐字校验。"""

    __tablename__ = "compliance_risk_references"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    risk_id = Column(
        String(36),
        ForeignKey("compliance_risks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ref_type = Column(String(20), nullable=False)  # regulation/judicial_case/playbook
    ref_name = Column(String(500), nullable=False)  # 法规名称
    ref_article = Column(String(100), nullable=True)  # 条款号，如「第十条」
    ref_content = Column(Text, nullable=False)  # 原文摘录
    ref_source_url = Column(String(1000), nullable=True)
    verified = Column(Boolean, default=False)  # 通过原文逐字校验？
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))