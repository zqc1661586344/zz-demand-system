"""Compliance document ORM model — review-specific metadata for an uploaded document."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String

from app.database import Base


class ComplianceDocument(Base):
    """合规审查文档：扩展业务表 documents，记录审查相关元数据。

    document_id 引用现有 documents 表（上传走现有 /api/documents/upload），
    审查前校验该文档 status == "indexed"。
    状态机：uploaded / parsing / parsed / reviewing / pending_human / generating / completed / failed
    """

    __tablename__ = "compliance_documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 合同类型：labor_contract / nda / procurement / service_agreement / other
    doc_type = Column(String(50), nullable=True)
    doc_type_confidence = Column(Float, nullable=True)
    page_count = Column(Integer, nullable=True)
    status = Column(String(20), default="uploaded", index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )