"""Playbook（审查规则）CRUD API — admin 运营接口。

与 app/compliance/models/playbook.py + schemas/playbook.py 对齐。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_roles
from app.logging_config import get_logger
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.compliance.models.playbook import CompliancePlaybook
from app.compliance.schemas.playbook import (
    PlaybookCreateRequest,
    PlaybookResponse,
    PlaybookUpdateRequest,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/compliance/playbooks", tags=["compliance-playbooks"])


@router.get("", response_model=PaginatedResponse[PlaybookResponse])
def list_playbooks(
    contract_type: Optional[str] = Query(None, description="按合同类型过滤"),
    is_active: Optional[bool] = Query(None),
    risk_level: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(CompliancePlaybook)
    if contract_type:
        q = q.filter(CompliancePlaybook.contract_type == contract_type)
    if is_active is not None:
        q = q.filter(CompliancePlaybook.is_active == is_active)
    if risk_level:
        q = q.filter(CompliancePlaybook.risk_level == risk_level)

    total = q.count()
    items = (
        q.order_by(CompliancePlaybook.contract_type, CompliancePlaybook.priority)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return PaginatedResponse(
        items=[PlaybookResponse.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{playbook_id}", response_model=PlaybookResponse)
def get_playbook(
    playbook_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    p = db.get(CompliancePlaybook, playbook_id)
    if not p:
        raise HTTPException(404, "playbook not found")
    return PlaybookResponse.model_validate(p)


@router.post("", response_model=PlaybookResponse)
def create_playbook(
    req: PlaybookCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    existing = (
        db.query(CompliancePlaybook)
        .filter(
            CompliancePlaybook.contract_type == req.contract_type,
            CompliancePlaybook.name == req.name,
            CompliancePlaybook.priority == req.priority,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, f"playbook already exists (id={existing.id})")

    p = CompliancePlaybook(
        id=str(uuid.uuid4()),
        name=req.name,
        description=req.description,
        contract_type=req.contract_type,
        clause_type=req.clause_type,
        risk_level=req.risk_level,
        match_type=req.match_type,
        match_pattern=req.match_pattern,
        match_threshold=req.match_threshold,
        legal_basis_ref=req.legal_basis_ref,
        standard_position=req.standard_position,
        red_line=req.red_line,
        negotiable=req.negotiable,
        suggested_clause=req.suggested_clause,
        priority=req.priority,
        is_active=True,
        version=1,
        created_by=current_user.id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    logger.info("Playbook created: %s [%s]", p.name, p.contract_type)
    return PlaybookResponse.model_validate(p)


@router.put("/{playbook_id}", response_model=PlaybookResponse)
def update_playbook(
    playbook_id: str,
    req: PlaybookUpdateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    p = db.get(CompliancePlaybook, playbook_id)
    if not p:
        raise HTTPException(404, "playbook not found")

    patch = req.model_dump(exclude_unset=True)
    for k, v in patch.items():
        setattr(p, k, v)
    p.updated_at = datetime.now(timezone.utc)
    p.version += 1

    db.commit()
    db.refresh(p)
    logger.info("Playbook updated: %s (v%d)", p.name, p.version)
    return PlaybookResponse.model_validate(p)


@router.delete("/{playbook_id}")
def delete_playbook(
    playbook_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    p = db.get(CompliancePlaybook, playbook_id)
    if not p:
        raise HTTPException(404, "playbook not found")
    db.delete(p)
    db.commit()
    logger.info("Playbook deleted: %s", p.name)
    return {"ok": True, "deleted": playbook_id}


@router.post("/{playbook_id}/toggle")
def toggle_playbook(
    playbook_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("admin")),
):
    """快速启停规则（软开关，比 DELETE 安全）。"""
    p = db.get(CompliancePlaybook, playbook_id)
    if not p:
        raise HTTPException(404, "playbook not found")
    p.is_active = not p.is_active
    p.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(p)
    return {"ok": True, "id": p.id, "is_active": p.is_active}
