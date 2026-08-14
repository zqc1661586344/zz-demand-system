"""Document service — upload tracking, status management."""

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models.document import Document


def create_document(
    db: Session,
    filename: str,
    original_filename: str,
    file_size: int,
    mime_type: str,
    uploaded_by: str,
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
    # Remove file from disk
    if doc.file_path and os.path.exists(doc.file_path):
        os.remove(doc.file_path)
    db.delete(doc)
    db.commit()
    return True


def update_document_status(db: Session, doc_id: str, status: str, chunk_count: int | None = None, error_message: str | None = None) -> Document | None:
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        return None
    doc.status = status
    if chunk_count is not None:
        doc.chunk_count = chunk_count
    if error_message is not None:
        doc.error_message = error_message
    doc.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(doc)
    return doc