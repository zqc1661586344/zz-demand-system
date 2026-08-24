"""Document API routes — upload, list, delete, reprocess."""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.document import DocumentResponse, DocumentUploadResponse, ReprocessResponse
from app.services.document_service import (
    create_document,
    delete_document,
    get_all_documents,
    get_document_by_id,
    update_document_status,
)
from app.rag.pipeline import process_document

from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    visibility: str = "private",
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证文件类型 — 先检查 MIME 类型，若无法确定则根据扩展名判断
    mime_to_ext = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    }
    ext_to_mime = {v: k for k, v in mime_to_ext.items()}

    # 优先用客户端上报的 Content-Type；命中即采用
    mime_type = file.content_type or ""
    logger.info("file name: %s, file content_type: %s", file.filename, mime_type)
    if mime_type not in mime_to_ext:
        # 浏览器对 .md/.txt 等常上报 application/octet-stream，改为按文件名后缀反推真实 MIME（用 Path.suffix，确定性更强，不依赖 mimetypes 系统数据库在 Windows/macOS/Linux 上的差异）。
        suffix = Path(file.filename or "").suffix.lower()
        if suffix in ext_to_mime:
            mime_type = ext_to_mime[suffix]
        logger.info("file real content_type: %s", mime_type)

    if mime_type not in mime_to_ext:
        logger.error("unsupported file type: %s", mime_type)
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.filename or mime_type}",
        )

    ext = mime_to_ext[mime_type]

    # 上传文档存盘
    # TODO：考虑用异步任务处理文件上传和解析，避免阻塞主线程
    os.makedirs(settings.upload_path, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = settings.upload_path / stored_name
    logger.info("file_path: %s", file_path)

    # TODO: 检查文件大小，限制上传文件大小
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # 文档信息存入数据库
    doc = create_document(
        db=db,
        filename=stored_name,
        original_filename=file.filename or stored_name,
        file_size=len(content),
        mime_type=mime_type,
        uploaded_by=current_user.id,
        visibility=visibility,
    )
    logger.info("document created in db, doc name: %s, doc id: %s", file.filename, doc.id)

    # 触发异步处理管道，处理文档内容（向量化、BM25等）
    background_tasks.add_task(process_document, doc.id)

    return DocumentUploadResponse(id=doc.id, filename=stored_name, status="pending")


@router.get("", response_model=list[DocumentResponse])
def list_documents(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return get_all_documents(db, skip=skip, limit=limit)


@router.get("/{doc_id}", response_model=DocumentResponse)
def get_document(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{doc_id}")
def delete_document_route(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.uploaded_by != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Permission denied")
    if not delete_document(db, doc_id):
        raise HTTPException(status_code=500, detail="Failed to delete document")
    return {"message": "Document deleted successfully"}


@router.post("/{doc_id}/reprocess", response_model=ReprocessResponse)
def reprocess_document(
    doc_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.uploaded_by != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Permission denied")
    doc = update_document_status(db, doc_id, "pending", error_message=None)
    background_tasks.add_task(process_document, doc_id)
    return ReprocessResponse(id=doc_id, status="pending")
