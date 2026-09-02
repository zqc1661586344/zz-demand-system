"""Compliance Reviews API — 审查任务 CRUD + 启动 + 人工审核。"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.compliance.services.review_service import ReviewService
from app.database import get_db
from app.dependencies import get_current_user
from app.logging_config import get_logger
from app.middleware.rate_limit import get_limiter
from app.models.user import User
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
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    request: Request = None,  # noqa: ARG001
):
    service = ReviewService()
    rows = service.list_reviews(db=db, user_id=current_user.id, limit=limit, offset=offset)
    return {"items": rows, "total": len(rows), "limit": limit, "offset": offset}


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
