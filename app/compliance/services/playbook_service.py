"""Playbook Service — 审查规则业务层。

职责：规则 CRUD + 按合同类型/风险等级拉活跃规则 + 版本管理 + 启停切换。
API 路由层只调本 service，不直接写 DB。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.compliance.models.playbook import CompliancePlaybook
from app.logging_config import get_logger

logger = get_logger(__name__)


class PlaybookService:
    """审查规则业务层。"""

    # ============== 查询 ==============

    @staticmethod
    def list_rules(
        db: Session,
        contract_type: Optional[str] = None,
        is_active: Optional[bool] = None,
        risk_level: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[CompliancePlaybook], int]:
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
        return items, total

    @staticmethod
    def get(db: Session, playbook_id: str) -> Optional[CompliancePlaybook]:
        return db.get(CompliancePlaybook, playbook_id)

    @staticmethod
    def load_active_rules(db: Session, contract_type: str) -> list[CompliancePlaybook]:
        """按合同类型拉所有活跃规则（审查主链路用）。"""
        return (
            db.query(CompliancePlaybook)
            .filter(
                CompliancePlaybook.contract_type == contract_type,
                CompliancePlaybook.is_active.is_(True),
            )
            .order_by(CompliancePlaybook.priority)
            .all()
        )

    # ============== 写入 ==============

    @staticmethod
    def create(db: Session, data: dict, created_by: Optional[str] = None) -> CompliancePlaybook:
        existing = (
            db.query(CompliancePlaybook)
            .filter(
                CompliancePlaybook.contract_type == data["contract_type"],
                CompliancePlaybook.name == data["name"],
                CompliancePlaybook.priority == data.get("priority", 100),
            )
            .first()
        )
        if existing:
            raise ValueError(f"playbook already exists (id={existing.id})")

        p = CompliancePlaybook(
            id=str(uuid.uuid4()),
            name=data["name"],
            description=data.get("description"),
            contract_type=data["contract_type"],
            clause_type=data.get("clause_type"),
            risk_level=data["risk_level"],
            match_type=data.get("match_type", "keyword"),
            match_pattern=data.get("match_pattern"),
            match_threshold=data.get("match_threshold", 0.8),
            legal_basis_ref=data.get("legal_basis_ref"),
            standard_position=data.get("standard_position"),
            red_line=data.get("red_line", False),
            negotiable=data.get("negotiable", True),
            suggested_clause=data.get("suggested_clause"),
            priority=data.get("priority", 100),
            is_active=True,
            version=1,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        logger.info("Playbook created: %s [%s]", p.name, p.contract_type)
        return p

    @staticmethod
    def update(db: Session, playbook_id: str, patch: dict) -> Optional[CompliancePlaybook]:
        p = db.get(CompliancePlaybook, playbook_id)
        if not p:
            return None
        for k, v in patch.items():
            setattr(p, k, v)
        p.updated_at = datetime.now(timezone.utc)
        p.version += 1
        db.commit()
        db.refresh(p)
        logger.info("Playbook updated: %s (v%d)", p.name, p.version)
        return p

    @staticmethod
    def delete(db: Session, playbook_id: str) -> bool:
        p = db.get(CompliancePlaybook, playbook_id)
        if not p:
            return False
        db.delete(p)
        db.commit()
        logger.info("Playbook deleted: %s", p.name)
        return True

    @staticmethod
    def toggle(db: Session, playbook_id: str) -> Optional[CompliancePlaybook]:
        p = db.get(CompliancePlaybook, playbook_id)
        if not p:
            return None
        p.is_active = not p.is_active
        p.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(p)
        logger.info("Playbook toggled: %s active=%s", p.name, p.is_active)
        return p
