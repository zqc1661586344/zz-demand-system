"""Compliance report ORM models — generated report files and human review actions."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, Text

from app.database import Base


class ComplianceReport(Base):
    """审查报告：同一审查可导出多格式（word/html），记录文件路径与大小。"""

    __tablename__ = "compliance_reports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    review_id = Column(
        String(36),
        ForeignKey("compliance_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    format = Column(String(10), nullable=False)  # word / html
    file_path = Column(String(1000), nullable=False)
    file_size = Column(BigInteger, nullable=True)
    generated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ComplianceHumanAction(Base):
    """人工审核操作留痕：确认/改级/编辑建议/标记误报/批量确认，记录操作前后内容。"""

    __tablename__ = "compliance_human_actions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    review_id = Column(
        String(36),
        ForeignKey("compliance_reviews.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    risk_id = Column(String(36), ForeignKey("compliance_risks.id"), nullable=True)
    # confirm/modify_level/edit_suggestion/mark_false/add_note/batch_confirm/resume
    action_type = Column(String(30), nullable=False)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    operator_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))