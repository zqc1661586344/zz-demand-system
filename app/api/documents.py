"""Document API routes — upload, list, delete, reprocess."""

import os
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    UploadFile,
    File,
)
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.middleware.rate_limit import get_limiter
from app.models.document import Document
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.document import DocumentResponse, DocumentUploadResponse, ReprocessResponse
from app.services.document_service import (
    create_document,
    delete_document,
    get_document_by_id,
    update_document_status,
)

from app.services.rag_service import enqueue_process

from app.rag.loaders import MIME_TO_EXT

from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

# EXT_TO_MIME 为模块级常量，模块加载时构建一次后复用

EXT_TO_MIME: dict[str, str] = {v: k for k, v in MIME_TO_EXT.items()}


def _get_owned_doc(doc_id: str, db: Session, current_user: User) -> Document:
    """取文档 + 鉴权。文档不存在抛 404，非 owner 且非 superuser 抛 403。"""
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.uploaded_by != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Permission denied")
    return doc


def _resolve_mime_type(file: UploadFile) -> tuple[str, str]:
    """从 UploadFile 解析最终 (mime_type, ext)。

    优先用客户端上报的 Content-Type；若无法命中则回退到文件名后缀。
    全部不匹配时抛 HTTPException(400)。
    """
    mime_type = file.content_type or ""
    if mime_type not in MIME_TO_EXT:
        # 浏览器对 .md/.txt 等常上报 application/octet-stream，改为按文件名后缀反推真实 MIME
        suffix = Path(file.filename or "").suffix.lower()
        if suffix in EXT_TO_MIME:
            mime_type = EXT_TO_MIME[suffix]

    if mime_type not in MIME_TO_EXT:
        logger.error("unsupported file type: %s", mime_type)
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.filename or mime_type}",
        )

    ext = MIME_TO_EXT[mime_type]
    return mime_type, ext


async def _write_uploaded_file(
    file: UploadFile,
    ext: str,
    max_bytes: int,
) -> tuple[Path, int]:
    """按 1MB 块流式写盘，超限时自行清理半截文件并抛 HTTPException(413)。

    Returns:
        (file_path, written) — 文件完整落盘后的路径和实际字节数
    """
    os.makedirs(settings.upload_path, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = settings.upload_path / stored_name
    written = 0

    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB 块
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    logger.warning(
                        "file too large: %s (%d bytes > %d)", file.filename, written, max_bytes
                    )
                    raise HTTPException(
                        status_code=413,
                        detail=f"The file exceeds the size limit of {settings.max_upload_size_mb}MB",
                    )
                f.write(chunk)
    except HTTPException:
        file_path.unlink(missing_ok=True)
        raise

    return file_path, written


@router.post("/upload", response_model=DocumentUploadResponse, status_code=201)
@get_limiter().limit(settings.rate_limit_upload)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    visibility: str = Query("private", pattern="^(private|shared)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1. 校验文件类型
    mime_type, ext = _resolve_mime_type(file)
    logger.debug("uploading %s as %s (ext=%s)", file.filename, mime_type, ext)

    # 2. 流式写盘（超限自动清理半截文件并抛 413）
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    file_path, written = await _write_uploaded_file(file, ext, max_bytes)

    # 3. 写入 DB —— 失败则回滚已落盘文件，避免孤立磁盘垃圾
    try:
        doc = create_document(
            db=db,
            filename=file_path.name,
            original_filename=file.filename or file_path.name,
            file_size=written,
            mime_type=mime_type,
            uploaded_by=current_user.id,
            visibility=visibility,
        )
    except Exception:
        logger.exception("create document failed: %s", file.filename)
        file_path.unlink(missing_ok=True)
        raise

    # 4. 入队后台处理 —— 失败只记日志，用户可通过 reprocess 重试
    try:
        enqueue_process(doc.id, background_tasks=background_tasks)
    except Exception:
        logger.exception("failed to enqueue process for doc %s", doc.id)

    logger.info("document uploaded: %s -> %s (%d bytes)", file.filename, doc.id, written)
    return DocumentUploadResponse(id=doc.id, filename=file_path.name, status="pending")


@router.get("", response_model=PaginatedResponse[DocumentResponse])
def list_documents(
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Document)
    if not current_user.is_superuser:
        q = q.filter(Document.uploaded_by == current_user.id)
    total = q.count()
    items = q.offset(offset).limit(limit).all()
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{doc_id}", response_model=DocumentResponse)
def get_document(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_doc(doc_id, db, current_user)


@router.delete("/{doc_id}")
def delete_document_route(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_doc(doc_id, db, current_user)
    if not delete_document(db, doc_id):
        logger.error("failed to delete document: %s", doc_id)
        raise HTTPException(status_code=500, detail="Failed to delete document")
    return {"message": "Document deleted successfully"}


@router.post("/{doc_id}/reprocess", response_model=ReprocessResponse)
def reprocess_document(
    doc_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_doc(doc_id, db, current_user)
    logger.info("reprocessing document: %s", doc_id)

    update_document_status(db, doc_id, "pending", error_message=None)

    try:
        enqueue_process(doc_id, background_tasks=background_tasks)
    except Exception:
        logger.exception("failed to enqueue process for reprocess doc %s", doc_id)

    return ReprocessResponse(id=doc_id, status="pending")
