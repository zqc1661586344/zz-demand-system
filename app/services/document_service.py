"""Document service — upload tracking, status management."""

import os
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.models.document import Document
from app.rag.vector_store import delete_documents_from_store


def create_document(
    db: Session,
    filename: str,
    original_filename: str,
    file_size: int,
    mime_type: str,
    uploaded_by: str,
    visibility: str = "private",
) -> Document:
    doc = Document(
        id=str(uuid.uuid4()),
        filename=filename,
        original_filename=original_filename,
        file_path=str(settings.upload_path / filename),
        file_size=file_size,
        mime_type=mime_type,
        status="pending",
        uploaded_by=uploaded_by,
        visibility=visibility,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def get_all_documents(db: Session, skip: int = 0, limit: int = 100) -> list[Document]:
    return db.query(Document).offset(skip).limit(limit).all()


def get_document_by_id(db: Session, doc_id: str) -> Document | None:
    return db.query(Document).filter(Document.id == doc_id).first()


def delete_document(db: Session, doc_id: str) -> bool:
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        return False

    # 记录所有者 ID 和可见性（删除前保留，后面刷新 BM25 要用）
    owner_id = str(doc.uploaded_by)
    visibility = doc.visibility

    # Remove from vector store first (Chroma — by document_id metadata)
    delete_documents_from_store(doc_id)

    # 删除 DocumentChunk 表中的记录（使用传入的 db 会话）
    from app.models.document import DocumentChunk

    db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
    db.commit()  # 先提交，确保 refresh_bm25_for_user 内的独立 DB 会话能看到删除结果

    # 重建该用户的 BM25 索引
    from app.rag.retrievers import invalidate_other_users_bm25, refresh_bm25_for_user

    refresh_bm25_for_user(owner_id)
    if visibility == "shared":
        # 共享文档变更 → 其他用户的 BM25 缓存失效（下次查询懒加载重建）
        invalidate_other_users_bm25(except_user_id=owner_id)

    # Remove file from disk
    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)
    db.delete(doc)
    db.commit()
    return True


def update_document_status(
    db: Session,
    doc_id: str,
    status: str,
    chunk_count: int | None = None,
    error_message: str | None = None,
) -> Document | None:
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        return None
    doc.status = status
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    if error_message is not None:
        doc.error_message = error_message
    db.commit()
    db.refresh(doc)
    return doc
