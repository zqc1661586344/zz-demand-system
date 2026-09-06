"""Review service — 审查任务编排层。

职责（设计文档 §5.5.4）：
  - 创建 ComplianceReview + ComplianceDocument 记录（幂等：同一 document_id + 合同类型不重复建）
  - 校验业务表 documents 存在且 status == "indexed"
  - 按合同类型从 Playbook 拉活跃规则喂入 Harness
  - 启动 Harness.start_review（BackgroundTasks 线程）
  - 查询 / 更新审查详情 + 风险列表 + 人工审核操作留痕

API 路由层只处理 FastAPI 请求体 → 调本 service → 用 ReviewDetailResponse 返回。
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.compliance.harness.runtime import ComplianceHarness, get_harness
from app.compliance.models.clause import ComplianceClause, ComplianceKeyInfo
from app.compliance.models.document import ComplianceDocument
from app.compliance.models.playbook import CompliancePlaybook
from app.compliance.models.report import ComplianceHumanAction
from app.compliance.models.review import (
    ComplianceRisk,
    ComplianceRiskReference,
    ComplianceReview,
)
from app.compliance.schemas.review import ReviewDetailResponse, ReviewCreateResponse
from app.logging_config import get_logger

logger = get_logger(__name__)


def _is_doc_accessible(biz_doc, user_id: str) -> bool:
    """判定 user_id 是否有权限审查 biz_doc（私有文档 owner / 共享文档任何人）。"""
    if getattr(biz_doc, "uploaded_by", None) == user_id:
        return True
    if getattr(biz_doc, "visibility", None) == "shared":
        return True
    return False


class ReviewService:
    """审查业务编排。"""

    def __init__(self, harness: Optional[ComplianceHarness] = None):
        self.harness = harness or get_harness()

    # ============== 创建审查任务 ==============

    def create_review(
        self,
        *,
        db: Session,
        document_id: str,
        original_filename: str,
        user_id: Optional[str] = None,
        doc_type: Optional[str] = None,
        template_id: Optional[str] = None,
    ) -> tuple[ReviewCreateResponse, dict]:
        """创建审查任务并加载 Playbook 规则。

        Returns:
            (response, start_payload) — start_payload 给 BackgroundTasks 调用 harness。
        """
        # 1) 校验业务文档存在 + 归属权限
        from app.models.document import Document as BizDocument

        biz_doc = db.query(BizDocument).filter(BizDocument.id == document_id).first()
        if biz_doc is None:
            raise ValueError(f"document {document_id} not found")
        if biz_doc.status != "indexed":
            raise ValueError(f"document {document_id} status='{biz_doc.status}', need 'indexed'")

        if user_id and not _is_doc_accessible(biz_doc, user_id):
            raise ValueError("forbidden: not your document and not shared")

        file_path = biz_doc.file_path
        mime_type = biz_doc.mime_type or "application/octet-stream"
        if not file_path:
            raise ValueError(f"document {document_id} has no file_path")

        # 2) 创建 compliance_document（幂等：已有就复用）
        comp_doc = (
            db.query(ComplianceDocument)
            .filter(ComplianceDocument.document_id == document_id)
            .first()
        )
        if comp_doc is None:
            comp_doc = ComplianceDocument(
                id=str(uuid.uuid4()),
                document_id=document_id,
                doc_type=doc_type,
                status="uploaded",
            )
            db.add(comp_doc)
            db.flush()

        # 3) 创建 review 任务
        review_id = str(uuid.uuid4())
        thread_id = f"review-{review_id}"
        review = ComplianceReview(
            id=review_id,
            compliance_doc_id=comp_doc.id,
            thread_id=thread_id,
            status="pending",
            started_at=datetime.now(timezone.utc),
            template_id=template_id,
            created_by=user_id,
        )
        db.add(review)

        # 4) 按合同类型拉活跃 Playbook 规则
        contract_type = doc_type or "labor_contract"
        rules = self._load_active_rules(db, contract_type)
        db.commit()

        logger.info(
            "review created: %s (doc=%s, comp_doc=%s, rules=%d)",
            review_id,
            document_id,
            comp_doc.id,
            len(rules),
        )

        response = ReviewCreateResponse(
            review_id=review_id,
            compliance_doc_id=comp_doc.id,
            thread_id=thread_id,
            status="pending",
        )

        start_payload = {
            "review_id": review_id,
            "document_id": document_id,
            "compliance_doc_id": comp_doc.id,
            "file_path": file_path,
            "mime_type": mime_type,
            "user_id": user_id,
            "rules": rules,
            "original_filename": original_filename,
        }
        return response, start_payload

    def _load_active_rules(self, db: Session, contract_type: str) -> list[dict]:
        rows = (
            db.query(CompliancePlaybook)
            .filter(
                CompliancePlaybook.is_active.is_(True),
                CompliancePlaybook.contract_type == contract_type,
            )
            .order_by(CompliancePlaybook.priority.asc())
            .all()
        )
        if not rows:
            logger.warning(
                "no active Playbook rules for contract_type=%s — review will proceed in LLM-only mode",
                contract_type,
            )

        return [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "contract_type": r.contract_type,
                "clause_type": r.clause_type,
                "risk_level": r.risk_level,
                "match_type": r.match_type,
                "match_pattern": r.match_pattern,
                "match_threshold": r.match_threshold,
                "legal_basis_ref": r.legal_basis_ref,
                "standard_position": r.standard_position,
                "suggested_clause": r.suggested_clause,
                "red_line": r.red_line,
                "priority": r.priority,
            }
            for r in rows
        ]

    def run_review(self, payload: dict) -> dict:
        """BackgroundTasks 入口：直接调用 harness.start_review。"""
        return self.harness.start_review(**payload)

    # ============== 查询 ==============

    def list_reviews(
        self,
        *,
        db: Session,
        user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        q = db.query(ComplianceReview).order_by(ComplianceReview.created_at.desc())
        if user_id:
            q = q.filter(ComplianceReview.created_by == user_id)
        rows = q.limit(limit).offset(offset).all()
        return [
            {
                "review_id": r.id,
                "compliance_doc_id": r.compliance_doc_id,
                "status": r.status,
                "high_risk_count": r.high_risk_count,
                "medium_risk_count": r.medium_risk_count,
                "low_risk_count": r.low_risk_count,
                "created_at": r.created_at,
                "completed_at": r.completed_at,
                "error_message": r.error_message,
            }
            for r in rows
        ]

    def get_review(self, *, db: Session, review_id: str) -> Optional[ReviewDetailResponse]:
        """查询完整审查详情（含风险 + 引用 + 条款号 + 关键信息）。"""
        review = db.query(ComplianceReview).filter(ComplianceReview.id == review_id).first()
        if review is None:
            return None

        comp_doc = (
            db.query(ComplianceDocument)
            .filter(ComplianceDocument.id == review.compliance_doc_id)
            .first()
        )

        clause_rows = (
            db.query(ComplianceClause)
            .filter(ComplianceClause.compliance_doc_id == review.compliance_doc_id)
            .order_by(ComplianceClause.sort_order.asc())
            .all()
        )
        clause_map: dict[str, ComplianceClause] = {c.id: c for c in clause_rows}

        ki_rows = (
            db.query(ComplianceKeyInfo)
            .filter(ComplianceKeyInfo.compliance_doc_id == review.compliance_doc_id)
            .all()
        )
        key_info: dict[str, str] = {ki.field_key: ki.field_value or "" for ki in ki_rows}

        risks = (
            db.query(ComplianceRisk)
            .filter(ComplianceRisk.review_id == review_id)
            .order_by(ComplianceRisk.sort_order.asc())
            .all()
        )
        risk_ids = [r.id for r in risks]

        ref_map: dict[str, list] = {}
        if risk_ids:
            refs = (
                db.query(ComplianceRiskReference)
                .filter(ComplianceRiskReference.risk_id.in_(risk_ids))
                .all()
            )
            for rf in refs:
                ref_map.setdefault(rf.risk_id, []).append(rf)

        risk_responses = []
        for r in risks:
            clause = clause_map.get(r.clause_id) if r.clause_id else None
            refs = ref_map.get(r.id, [])
            risk_responses.append(
                {
                    "id": r.id,
                    "clause_number": clause.clause_number if clause else None,
                    "clause_content": (clause.content[:200] + "...")
                    if clause and clause.content
                    else None,
                    "risk_level": r.risk_level,
                    "risk_category": r.risk_category,
                    "description": r.description,
                    "suggestion": r.suggestion,
                    "suggestion_reason": r.suggestion_reason,
                    "legal_references": [
                        {
                            "ref_type": rf.ref_type,
                            "ref_name": rf.ref_name,
                            "ref_article": rf.ref_article,
                            "ref_content": rf.ref_content,
                            "verified": rf.verified,
                        }
                        for rf in refs
                    ],
                    "ai_confidence": r.ai_confidence,
                    "human_confirmed": r.human_confirmed,
                    "human_decision": r.human_decision,
                }
            )

        return ReviewDetailResponse(
            review_id=review.id,
            compliance_doc_id=review.compliance_doc_id,
            document_id=comp_doc.document_id if comp_doc else "",
            status=review.status,
            doc_type=comp_doc.doc_type if comp_doc else None,
            key_info=key_info,
            high_risk_count=review.high_risk_count or 0,
            medium_risk_count=review.medium_risk_count or 0,
            low_risk_count=review.low_risk_count or 0,
            risks=risk_responses,
            created_at=review.created_at,
            completed_at=review.completed_at,
            error_message=review.error_message,
        )

    def delete_review(self, *, db: Session, review_id: str) -> bool:
        review = db.query(ComplianceReview).filter(ComplianceReview.id == review_id).first()
        if review is None:
            return False
        db.delete(review)
        db.commit()
        return True

    # ============== 人工审核 ==============

    VALID_HUMAN_ACTIONS = frozenset({"confirm", "modify_level", "edit_suggestion", "mark_false"})

    def human_action(
        self,
        *,
        db: Session,
        review_id: str,
        action: str,
        risk_ids: list[str],
        operator_id: Optional[str] = None,
        new_risk_level: Optional[str] = None,
        new_suggestion: Optional[str] = None,
        note: Optional[str] = None,
    ) -> dict:
        """执行人工审核操作并留痕。"""
        if action not in self.VALID_HUMAN_ACTIONS:
            raise ValueError(
                f"unknown action '{action}', must be one of {sorted(self.VALID_HUMAN_ACTIONS)}"
            )
        now = datetime.now(timezone.utc)
        results = []
        for risk_id in risk_ids:
            risk = (
                db.query(ComplianceRisk)
                .filter(
                    ComplianceRisk.id == risk_id,
                    ComplianceRisk.review_id == review_id,
                )
                .first()
            )
            if risk is None:
                results.append({"risk_id": risk_id, "ok": False, "error": "not found"})
                continue
            old_val = {"level": risk.risk_level, "suggestion": risk.suggestion}
            if action == "confirm":
                risk.human_confirmed = True
                risk.human_decision = "confirmed"
            elif action == "modify_level":
                if new_risk_level:
                    risk.risk_level = new_risk_level
                risk.human_confirmed = True
                risk.human_decision = "modified"
            elif action == "edit_suggestion":
                if new_suggestion:
                    risk.suggestion = new_suggestion
                risk.human_confirmed = True
                risk.human_decision = "modified"
            elif action == "mark_false":
                risk.human_decision = "rejected"

            risk.human_reviewed_at = now
            risk.human_reviewed_by = operator_id

            action_log = ComplianceHumanAction(
                id=str(uuid.uuid4()),
                review_id=review_id,
                risk_id=risk_id,
                action_type=action,
                old_value=str(old_val),
                new_value=str({"level": risk.risk_level, "suggestion": risk.suggestion}),
                operator_id=operator_id,
            )
            db.add(action_log)
            results.append({"risk_id": risk_id, "ok": True})

        db.commit()
        return {"action": action, "results": results}
