"""Compliance schemas — Pydantic models for review API and LLM structured output."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DocType(str, Enum):
    LABOR_CONTRACT = "labor_contract"
    NDA = "nda"
    PROCUREMENT = "procurement"
    SERVICE_AGREEMENT = "service_agreement"
    OTHER = "other"


class RiskLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RiskCategory(str, Enum):
    LEGALITY = "legality"
    EQUALITY = "equality"
    CLARITY = "clarity"
    COMPLETENESS = "completeness"
    REASONABLENESS = "reasonableness"


class ClauseType(str, Enum):
    PARTIES = "parties"
    TERM = "term"
    PAYMENT = "payment"
    DELIVERY = "delivery"
    PENALTY = "penalty"
    IP = "ip"
    CONFIDENTIALITY = "confidentiality"
    DISPUTE = "dispute"
    TERMINATION = "termination"
    FORCE_MAJEURE = "force_majeure"
    OTHER = "other"


# ---------------- 条款与关键信息（LLM 结构化输出） ----------------

class Clause(BaseModel):
    clause_number: str = Field(description="条款号，如'第3条第2款'")
    clause_type: ClauseType
    title: Optional[str] = None
    content: str
    page_number: Optional[int] = None


class KeyInfo(BaseModel):
    party_a: Optional[str] = None
    party_b: Optional[str] = None
    sign_date: Optional[str] = None
    effective_date: Optional[str] = None
    term: Optional[str] = None
    amount: Optional[str] = None
    payment_method: Optional[str] = None
    penalty_cap: Optional[str] = None
    ip_ownership: Optional[str] = None
    confidentiality_period: Optional[str] = None
    dispute_resolution: Optional[str] = None


# ---------------- 风险识别（LLM 结构化输出） ----------------

class LegalReference(BaseModel):
    ref_type: str = Field(description="regulation/judicial_case")
    ref_name: str
    ref_article: Optional[str] = None
    ref_content: str


class RiskItem(BaseModel):
    clause_number: str
    risk_level: RiskLevel
    risk_category: RiskCategory
    description: str
    suggestion: Optional[str] = None
    suggestion_reason: Optional[str] = None
    legal_references: list[LegalReference] = Field(default_factory=list)
    ai_confidence: float = 1.0


class ReviewResult(BaseModel):
    doc_type: DocType
    key_info: KeyInfo
    clauses: list[Clause]
    risks: list[RiskItem]
    summary: str


# ---------------- API 请求 / 响应 ----------------

class ReviewCreateRequest(BaseModel):
    document_id: str
    template_id: Optional[str] = None
    contract_type_override: Optional[DocType] = None


class ReviewCreateResponse(BaseModel):
    review_id: str
    compliance_doc_id: str
    thread_id: str
    status: str


class RiskItemResponse(BaseModel):
    id: str
    clause_number: Optional[str] = None
    clause_content: Optional[str] = None
    risk_level: str
    risk_category: str
    description: str
    suggestion: Optional[str] = None
    suggestion_reason: Optional[str] = None
    legal_references: list[LegalReference] = Field(default_factory=list)
    ai_confidence: float = 1.0
    human_confirmed: bool = False
    human_decision: Optional[str] = None
    model_config = {"from_attributes": True}


class ReviewDetailResponse(BaseModel):
    review_id: str
    compliance_doc_id: str
    document_id: str
    status: str
    doc_type: Optional[str] = None
    key_info: Optional[dict] = None
    high_risk_count: int = 0
    medium_risk_count: int = 0
    low_risk_count: int = 0
    risks: list[RiskItemResponse] = Field(default_factory=list)
    created_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class HumanReviewRequest(BaseModel):
    action: str = Field(
        description="confirm/modify_level/edit_suggestion/mark_false/batch_confirm/resume"
    )
    risk_ids: list[str] = Field(default_factory=list)
    new_risk_level: Optional[str] = None
    new_suggestion: Optional[str] = None
    note: Optional[str] = None