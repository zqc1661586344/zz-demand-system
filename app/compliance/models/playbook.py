"""Compliance playbook ORM model — enterprise review rules (keyword/semantic/LLM)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text

from app.database import Base


class CompliancePlaybook(Base):
    """审查规则：三层匹配（关键词/正则 → 语义 → LLM 判定）。

    典型规则：试用期约定、违约金上限、保密期限、竞业限制等。
    """

    __tablename__ = "compliance_playbooks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    contract_type = Column(String(50), nullable=False, index=True)  # labor_contract/nda/...
    clause_type = Column(String(50), nullable=True)
    risk_level = Column(String(10), nullable=False)  # high/medium/low
    # keyword（确定性匹配）/ semantic（向量语义）/ hybrid（语义后 LLM 判定）
    match_type = Column(String(20), nullable=False, default="keyword")
    match_pattern = Column(Text, nullable=True)  # 关键词/正则表达式
    match_threshold = Column(Float, default=0.8)
    legal_basis_ref = Column(Text, nullable=True)  # 法规依据（引用线索）
    standard_position = Column(Text, nullable=True)  # 企业标准立场
    red_line = Column(Boolean, default=False)  # 红线条款（必须修改）
    negotiable = Column(Boolean, default=True)
    suggested_clause = Column(Text, nullable=True)  # 建议条款措辞
    priority = Column(Integer, default=100)
    is_active = Column(Boolean, default=True)
    version = Column(Integer, default=1)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )