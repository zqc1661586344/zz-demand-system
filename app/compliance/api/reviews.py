"""Compliance Reviews API — 审查任务 CRUD + 启动 + 人工审核 + 报告下载。"""

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.compliance.services.review_service import ReviewService
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.logging_config import get_logger
from app.middleware.rate_limit import get_limiter
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.compliance.models.review import ComplianceReview
from app.compliance.schemas.review import (
    HumanReviewRequest,
    ReviewCreateRequest,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/compliance/reviews", tags=["compliance-reviews"])
limiter = get_limiter()


@router.post("", response_model_exclude_none=True)
@limiter.limit("10/minute")
def create_review(
    req: ReviewCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,  # noqa: ARG001 — 供 slowapi rate-limit 取 client
):
    service = ReviewService()
    original_filename = getattr(req, "original_filename", None) or f"doc-{req.document_id}"
    try:
        response, payload = service.create_review(
            db=db,
            document_id=req.document_id,
            original_filename=original_filename,
            user_id=current_user.id,
            doc_type=req.contract_type_override.value if req.contract_type_override else None,
            template_id=req.template_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    background_tasks.add_task(service.run_review, payload)
    logger.info("review %s queued for doc %s", response.review_id, req.document_id)
    return response


@router.get("")
@limiter.limit("60/minute")
def list_reviews(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,  # noqa: ARG001
):
    q = db.query(ComplianceReview)
    if not current_user.is_superuser:
        q = q.filter(ComplianceReview.created_by == current_user.id)
    total = q.count()
    service = ReviewService()
    items = service.list_reviews(db=db, user_id=current_user.id, limit=limit, offset=offset)
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{review_id}", response_model_exclude_none=True)
@limiter.limit("60/minute")
def get_review(
    review_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,  # noqa: ARG001
):
    service = ReviewService()
    detail = service.get_review(db=db, review_id=review_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="review not found")
    return detail


@router.delete("/{review_id}")
@limiter.limit("10/minute")
def delete_review(
    review_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,  # noqa: ARG001
):
    service = ReviewService()
    ok = service.delete_review(db=db, review_id=review_id)
    if not ok:
        raise HTTPException(status_code=404, detail="review not found")
    return {"ok": True, "review_id": review_id}


@router.post("/{review_id}/human-review")
@limiter.limit("30/minute")
def human_review(
    review_id: str,
    req: HumanReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,  # noqa: ARG001
):
    service = ReviewService()
    try:
        result = service.human_action(
            db=db,
            review_id=review_id,
            action=req.action,
            risk_ids=req.risk_ids,
            operator_id=current_user.id,
            new_risk_level=req.new_risk_level,
            new_suggestion=req.new_suggestion,
            note=req.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return result


_FORMAT_EXT = {"html": ".html", "word": ".docx", "pdf": ".pdf"}
_MIME_MAP = {
    ".html": "text/html; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}


@router.get("/{review_id}/report/{format}")
@limiter.limit("30/minute")
def download_report(
    review_id: str,
    format: str = "html",
    db: Session = Depends(get_db),  # noqa: ARG001 — 鉴权用
    current_user: User = Depends(get_current_user),  # noqa: ARG001
    request: Request = None,  # noqa: ARG001
):
    """下载审查报告（P1）：扫描 report_dir 返回最新匹配文件。

    Args:
        review_id: 审查任务 id。
        format: html | word | pdf。
    """
    ext = _FORMAT_EXT.get(format)
    if ext is None:
        raise HTTPException(status_code=400, detail=f"unsupported format: {format}")

    report_dir = Path(settings.compliance_report_dir)
    if not report_dir.is_dir():
        raise HTTPException(status_code=404, detail="report directory not ready")

    prefix = f"review-{review_id}-"
    candidates = sorted(
        report_dir.glob(f"{prefix}*{ext}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"no {format} report found for review {review_id}",
        )

    latest = candidates[0]
    return FileResponse(
        path=str(latest),
        media_type=_MIME_MAP.get(ext, "application/octet-stream"),
        filename=latest.name,
    )
