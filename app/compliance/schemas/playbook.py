"""Compliance playbook schemas — Pydantic models for review rule API.

规则实体存 compliance_playbooks 表；请求/响应模型用于 admin 管理接口
（POST/PUT /playbooks）。字段与 ORM 模型 app/compliance/models/playbook.py 对齐。
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PlaybookCreateRequest(BaseModel):
    """创建审查规则（admin）—— 三层匹配：keyword/semantic/hybrid。"""

    name: str = ...  # 规则名称
    description: Optional[str] = None
    contract_type: str = "labor_contract"  # labor_contract / nda / procurement / service_agreement
    clause_type: Optional[str] = None  # 适用条款类型（parties/term/payment/... 留空则不限定）
    risk_level: str = "medium"  # high / medium / low
    match_type: str = "keyword"  # keyword(确定匹配) / semantic(向量语义) / hybrid(语义后 LLM 判定)
    match_pattern: Optional[str] = None  # 关键词或正则表达式
    match_threshold: float = 0.8  # semantic/hybrid 的语义相似度阈值
    legal_basis_ref: Optional[str] = None  # 法规依据（引用线索，如「劳动合同法第十九条」）
    standard_position: Optional[str] = None  # 企业标准立场
    red_line: bool = False  # 红线条款（必须修改）
    negotiable: bool = True  # 是否可谈判
    suggested_clause: Optional[str] = None  # 建议措辞
    priority: int = 100


class PlaybookUpdateRequest(BaseModel):
    """更新审查规则（admin）—— 仅传需要修改的字段，其余保持原值。"""

    name: Optional[str] = None
    description: Optional[str] = None
    contract_type: Optional[str] = None
    clause_type: Optional[str] = None
    risk_level: Optional[str] = None
    match_type: Optional[str] = None
    match_pattern: Optional[str] = None
    match_threshold: Optional[float] = None
    legal_basis_ref: Optional[str] = None
    standard_position: Optional[str] = None
    red_line: Optional[bool] = None
    negotiable: Optional[bool] = None
    suggested_clause: Optional[str] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class PlaybookResponse(BaseModel):
    """规则响应模型（GET /playbooks 列表 / 单项）。"""

    id: str
    name: str
    description: Optional[str] = None
    contract_type: str
    clause_type: Optional[str] = None
    risk_level: str
    match_type: str
    match_pattern: Optional[str] = None
    match_threshold: float = 0.8
    legal_basis_ref: Optional[str] = None
    standard_position: Optional[str] = None
    red_line: bool = False
    negotiable: bool = True
    suggested_clause: Optional[str] = None
    priority: int = 100
    is_active: bool = True
    version: int = 1
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}