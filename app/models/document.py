"""Document ORM models."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Document(Base):
    """
    文档模型类，用于存储文档的基本信息和状态
    继承自Base类，使用SQLAlchemy ORM映射到数据库表
    """

    __tablename__ = "documents"  # 指定数据库表名为"documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)
    mime_type = Column(String(100), nullable=True)
    status = Column(
        String(50), default="pending", index=True
    )  # pending, processing, indexed, failed
    chunk_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    uploaded_by = Column(String(36), ForeignKey("users.id"), nullable=False)
    visibility = Column(String(20), default="private")  # "private" | "shared"
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    uploader = relationship("User", back_populates="documents")


class DocumentChunk(Base):
    """
    文档块模型类，用于存储文档被分割后的各个块的信息。
    继承自Base类，使用SQLAlchemy ORM进行数据库映射。
    """

    __tablename__ = "document_chunks"  # 指定数据库表名为"document_chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    # jieba 分词后的空格串（中文按词切分，英文按空白切分）。供 PG tsvector 稀疏检索使用
    # （to_tsvector('simple', search_text) 上的 GIN 表达式索引）。为 NULL 表示该 chunk 尚未
    # 建立搜索文本；SQLite 开发环境无 PG GIN 索引，稀疏检索会回退内存 BM25。
    search_text = Column("search_text", Text, nullable=True)
    page_number = Column(Integer, nullable=True)
    meta_json = Column("meta", Text, nullable=True)  # JSON string
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationships
    document = relationship("Document", backref="chunks")
