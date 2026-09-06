"""Compliance Reviews API — 审查任务 CRUD + 启动 + 人工审核 + 报告下载."""

import json

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.compliance.services.review_service import ReviewService
from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.logging_config import get_logger
from app.middleware.rate_limit import get_limiter
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.compliance.models.report import ComplianceHumanAction, ComplianceReport
from app.compliance.models.review import ComplianceReview
from app.compliance.schemas.review import (
    HumanReviewRequest,
    ReviewCreateRequest,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api/compliance/reviews", tags=["compliance-reviews"])
limiter = get_limiter()


def _assert_review_access(db: Session, review_id: str, user: User) -> ComplianceReview:
    review = db.query(ComplianceReview).filter(ComplianceReview.id == review_id).first()
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    if not user.is_superuser and review.created_by != user.id:
        raise HTTPException(status_code=403, detail="forbidden: not your review")
    return review


def _assert_doc_access_or_404(biz_doc, user_id: str) -> None:
    from app.models.document import Document as BizDocument

    if biz_doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    if biz_doc.status != "indexed":
        raise HTTPException(
            status_code=400, detail=f"document status='{biz_doc.status}', need 'indexed'"
        )
    is_shared = getattr(biz_doc, "visibility", None) == "shared"
    is_owner = getattr(biz_doc, "uploaded_by", None) == user_id
    if user_id and not is_owner and not is_shared:
        raise HTTPException(status_code=403, detail="forbidden: not your document and not shared")


@router.post("", response_model_exclude_none=True)
@limiter.limit("10/minute")
def create_review(
    req: ReviewCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,  # noqa: ARG001
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

    if settings.use_celery_task and settings.celery_broker_url:
        from app.compliance.tasks import run_compliance_review

        try:
            run_compliance_review.delay(payload)
            logger.info(
                "review %s queued via celery for doc %s", response.review_id, req.document_id
            )
        except Exception as exc:  # noqa: BLE001 — broker 断连等投递失败降级
            logger.warning("celery queue failed (%s) — fall back background_tasks", exc)
            background_tasks.add_task(service.run_review, payload)
            logger.info(
                "review %s queued via background_tasks (celery fallback)", response.review_id
            )
    else:
        background_tasks.add_task(service.run_review, payload)
        logger.info(
            "review %s queued via background_tasks for doc %s", response.review_id, req.document_id
        )
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
    _assert_review_access(db, review_id, current_user)
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
    current_user: User = Depends(require_roles("admin", "legal")),
    request: Request = None,  # noqa: ARG001
):
    _assert_review_access(db, review_id, current_user)
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
    current_user: User = Depends(require_roles("admin", "legal")),
    request: Request = None,  # noqa: ARG001
):
    _assert_review_access(db, review_id, current_user)
    if not req.risk_ids:
        raise HTTPException(status_code=400, detail="risk_ids is required")
    if len(req.risk_ids) > 100:
        raise HTTPException(status_code=400, detail="too many risk_ids (max 100)")
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


@router.post("/{review_id}/resume")
@limiter.limit("10/minute")
def resume_review(
    review_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "legal")),
    request: Request = None,  # noqa: ARG001
):
    """人工确认后续跑 generate_report（HITL resume）。

    仅当 review.status == pending_human 且至少存在一条人工决策记录时可用。
    """
    review = _assert_review_access(db, review_id, current_user)
    if review.status != "pending_human":
        raise HTTPException(
            status_code=400,
            detail=f"review status is '{review.status}', expected 'pending_human' to resume",
        )

    action_count = (
        db.query(ComplianceHumanAction).filter(ComplianceHumanAction.review_id == review_id).count()
    )
    if action_count == 0:
        from app.compliance.models.review import ComplianceRisk

        untouched_high = (
            db.query(ComplianceRisk)
            .filter(
                ComplianceRisk.review_id == review_id,
                ComplianceRisk.risk_level == "high",
                ComplianceRisk.human_decision == "na",
            )
            .count()
        )
        has_high = (review.high_risk_count or 0) > 0
        if has_high and untouched_high > 0:
            raise HTTPException(
                status_code=409,
                detail="no human review decisions recorded — please review high risks before resuming",
            )
    from app.compliance.harness.runtime import get_harness

    if settings.use_celery_task and settings.celery_broker_url:
        from app.compliance.tasks import resume_compliance_review

        try:
            resume_compliance_review.delay(review_id)
            logger.info("review %s resumed via celery by user %s", review_id, current_user.id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("celery resume queue failed (%s) — fall back background_tasks", exc)
            harness = get_harness()
            background_tasks.add_task(harness.resume_review, review_id)
    else:
        harness = get_harness()
        background_tasks.add_task(harness.resume_review, review_id)
        logger.info("review %s resumed via background_tasks by user %s", review_id, current_user.id)
    return {"ok": True, "review_id": review_id, "status": "resuming"}


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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,  # noqa: ARG001
):
    review = _assert_review_access(db, review_id, current_user)

    ext = _FORMAT_EXT.get(format)
    if ext is None:
        raise HTTPException(status_code=400, detail=f"unsupported format: {format}")

    report = (
        db.query(ComplianceReport)
        .filter(ComplianceReport.review_id == review.id, ComplianceReport.format == format)
        .order_by(ComplianceReport.generated_at.desc())
        .first()
    )

    report_dir = Path(settings.compliance_report_dir).resolve()
    if report is not None and report.file_path:
        candidate = Path(report.file_path).resolve()
        try:
            candidate.relative_to(report_dir)
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="report path out of jail") from exc
        if candidate.is_file():
            _final_name = candidate.name
            if format == "html":
                _final_name = f"compliance-report-{review.id}.html"
            return FileResponse(
                path=str(candidate),
                media_type=_MIME_MAP.get(ext, "application/octet-stream"),
                filename=_final_name,
                headers={
                    "X-Content-Type-Options": "nosniff",
                    "Content-Disposition": f'attachment; filename="{_final_name}"',
                },
            )

    raise HTTPException(
        status_code=404,
        detail=f"no {format} report found for review {review.id}",
    )
