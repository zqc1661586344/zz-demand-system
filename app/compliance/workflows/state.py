"""审查流水线 State（app/compliance/workflows/state.py）——LangGraph ReviewState。"""

"""审查流水线 State — LangGraph ReviewState（设计文档 §5.5.1）。

字段按阶段组织：
  - 输入：document_id / compliance_doc_id / file_path / mime_type / template_id /
          contract_type_override / user_id
  - 文档解析：raw_text / doc_type / clauses / key_info
  - 审查计划：review_plan
  - 审查结果：risks / review_summary（risks 与 summary 在 LangGraph 中被追加合并）
  - 模板比对：template_diff（预留）
  - 自反思：retry_count / quality_score
  - 人机协同：pending_human_review / human_decisions（MVP 预留 interrupt）
  - 输出：report_id / report_path / status / error

注意：LangGraph 默认按「同键覆盖」合并 state；risks/review_plan 等列表若要累加，
子类/节点应返回带自定义 reducer 的 Annotated 类型，或在节点内合并后写回。
MVP 采用简单写法：risks 用 list（节点内整体写回），避免 reducers 复杂化。
"""

from typing import Optional, TypedDict


# 审查任务阶段常量（写入 compliance_reviews.status）
STATUS_PARSING = "parsing"
STATUS_PLANNING = "planning"
STATUS_REVIEWING = "reviewing"
STATUS_REFLECTING = "reflecting"
STATUS_COMPARING = "comparing"
STATUS_PENDING_HUMAN = "pending_human"
STATUS_GENERATING = "generating"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# 阶段常量映射（前端进度展示用：阶段顺序）
PHASE_ORDER = [
    STATUS_PARSING,
    STATUS_PLANNING,
    STATUS_REVIEWING,
    STATUS_REFLECTING,
    STATUS_COMPARING,
    STATUS_PENDING_HUMAN,
    STATUS_GENERATING,
    STATUS_COMPLETED,
]


class ReviewState(TypedDict, total=False):
    """审查流水线状态。

    total=False：允许节点只更新部分字段；LangGraph 会将新增键合并进 state。
    """

    # ---- 输入 ----
    review_id: str  # compliance_reviews.id（审查任务主键，节点内部落库用）
    document_id: str  # 业务表 documents.id
    compliance_doc_id: str  # compliance_documents.id（本次审查关联）
    file_path: str  # 磁盘文件路径
    mime_type: str  # 文件 MIME 类型
    original_filename: Optional[str]  # 展示用原名（报告 doc_info）
    rules: list  # Playbook 活跃规则（service 已按合同类型过滤）
    template_id: Optional[str]
    contract_type_override: Optional[str]
    user_id: Optional[str]

    # ---- 文档解析 ----
    raw_text: str
    doc_type: str
    clauses: list  # list[dict]：{clause_number, title, content, page_number, ...}
    key_info: dict  # 合同关键信息（字段级）

    # ---- 审查计划 ----
    review_plan: list  # list[str]

    # ---- 审查结果 ----
    risks: list  # list[dict]：{clause_number, risk_level, risk_category,
    #             description, suggestion, legal_references, ...}
    review_summary: str

    # ---- 模板比对（预留）----
    template_diff: Optional[list]

    # ---- 自反思 ----
    retry_count: int
    quality_score: float

    # ---- 人机协同（MVP 预留 interrupt）----
    pending_human_review: list  # list[str] 高风险条款号（HITL 启用时暂停点）
    human_decisions: Optional[dict]

    # ---- 输出 ----
    report_id: Optional[str]
    report_path: Optional[str]
    status: str
    error: Optional[str]
