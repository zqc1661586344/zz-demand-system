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

from app.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])


# 允许的文件扩展名（默认与 pipeline.py 支持的格式一致，通过 ALLOWED_EXTENSIONS 环境变量覆盖）
ALLOWED_EXTENSIONS = set(settings.allowed_extensions)


@router.post("/upload", response_model=DocumentUploadResponse, status_code=201)
@get_limiter().limit(settings.rate_limit_upload)
async def upload_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    visibility: str = "private",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证文件类型 — 先检查 MIME 类型，若无法确定则根据扩展名判断（与 pipeline.load_document 支持格式保持一致）
    mime_to_ext = {
        "application/pdf": ".pdf",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "text/csv": ".csv",
        "text/html": ".html",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/toml": ".toml",
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

    # 上传文档存盘 —— 按块流式落盘，避免单个大文件整体读入内存
    os.makedirs(settings.upload_path, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    file_path = settings.upload_path / stored_name
    logger.info(f"file_path: {file_path}")

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    written = 0
    try:
        with open(file_path, "wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB 块
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {settings.max_upload_size_mb}MB 大小限制",
                    )
                f.write(chunk)
    except HTTPException:
        # 超限后清理已写部分，避免残留半截文件
        if file_path.exists():
            file_path.unlink()
        raise

    # 文档信息存入数据库
    doc = create_document(
        db=db,
        filename=stored_name,
        original_filename=file.filename or stored_name,
        file_size=written,
        mime_type=mime_type,
        uploaded_by=current_user.id,
        visibility=visibility,
    )
    logger.info("document created in db, doc name: %s, doc id: %s", file.filename, doc.id)

    # 通过 Celery 异步处理文档（生产环境）
    if settings.celery_broker_url and settings.use_celery_task:
        from app.rag.tasks import process_document_task

        logger.info("process document task use celery")
        process_document_task.delay(doc.id)
    else:
        # 无 Celery 时回退 BackgroundTasks（开发模式）—— 使用 FastAPI 注入的实例
        from app.rag.pipeline import process_document

        logger.info("process document task use background_tasks")
        background_tasks.add_task(process_document, doc.id)

    return DocumentUploadResponse(id=doc.id, filename=stored_name, status="pending")


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
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        logger.error("document not found: %s", doc_id)
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.uploaded_by != current_user.id and not current_user.is_superuser:
        logger.error("permission denied: %s", doc_id)
        raise HTTPException(status_code=403, detail="Permission denied")

    return doc


@router.delete("/{doc_id}")
def delete_document_route(
    doc_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        logger.error("document not found: %s", doc_id)
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.uploaded_by != current_user.id and not current_user.is_superuser:
        logger.error("permission denied: %s", doc_id)
        raise HTTPException(status_code=403, detail="Permission denied")

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
    doc = get_document_by_id(db, doc_id)
    if doc is None:
        logger.error("document not found: %s", doc_id)
        raise HTTPException(status_code=404, detail="Document not found")

    if doc.uploaded_by != current_user.id and not current_user.is_superuser:
        logger.error("permission denied: %s", doc_id)
        raise HTTPException(status_code=403, detail="Permission denied")

    doc = update_document_status(db, doc_id, "pending", error_message=None)
    if settings.celery_broker_url and settings.use_celery_task:
        from app.rag.tasks import process_document_task

        logger.info("reprocess document task use celery")
        process_document_task.delay(doc_id)
    else:
        from app.rag.pipeline import process_document

        logger.info("reprocess document task use background_tasks")

        background_tasks.add_task(process_document, doc_id)
    return ReprocessResponse(id=doc_id, status="pending")
