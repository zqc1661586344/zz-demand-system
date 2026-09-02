"""ComplianceHarness — 审查工作流运行时（app/compliance/harness/runtime.py）。

封装（设计文档 §5.5.3）：LangGraph 图、checkpointer（PostgresSaver / InMemorySaver 三级
回退，见 checkpointer.py）、图节点路由（parse/supervise/extract/review/reflect/compare/
human_review/generate_report）、条件边（should_compare/should_retry）、状态落库
（审查阶段写 compliance_reviews.status 与风险计数）。

执行方式（MVP）：POST /reviews 创建任务后由 FastAPI BackgroundTasks 调用
`start_review(...)`（同步，在线程池中跑）；`graph.stream(..., stream_mode="updates")`
逐节点输出 → 每节点把阶段/计数写库 → 前端轮询 GET /reviews/{id}。SSE 端点用
「DB 轮询生成器」推 `data: {json}` 事件（stream.py 格式化）。

图节点调用 skills（parse/playbook/rag/risk/report）与 agents（supervisor/extractor/
reviewer/reporter），全部确定性路径 test 模式可跑通（mock）。引用校验由 rag_skill
（citation_verifier）完成——法规空库时降级 verified=False + 「需人工核实」。

注意：runtime 顶层不 import review_graph（避免循环依赖），图构建在 __init__ 内延迟导入。
"""

from functools import lru_cache
from typing import Optional

from app.compliance.agents.extractor import ExtractorAgent
from app.compliance.agents.reporter import ReporterAgent
from app.compliance.agents.supervisor import SupervisorAgent
from app.compliance.harness.checkpointer import build_checkpointer
from app.compliance.harness.hitl import HitlManager
from app.compliance.models.review import ComplianceReview
from app.compliance.skills.parse_skill import ParseSkill
from app.compliance.skills.playbook_skill import PlaybookSkill
from app.compliance.skills.rag_skill import RagSkill
from app.compliance.skills.report_skill import ReportSkill
from app.compliance.skills.risk_skill import RiskSkill
from app.compliance.workflows.state import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_GENERATING,
    STATUS_PARSING,
    STATUS_PENDING_HUMAN,
    STATUS_PLANNING,
    STATUS_REFLECTING,
    STATUS_REVIEWING,
)
from app.config import settings
from app.database import SessionLocal
from app.logging_config import get_logger

logger = get_logger(__name__)


def _thread_config(review_id: str) -> dict:
    """LangGraph thread config：thread_id=review-<id>（checkpointer 断点恢复键）。"""
    return {"configurable": {"thread_id": f"review-{review_id}"}}


class ComplianceHarness:
    """审查工作流运行时：图构建(checkpointer) + 节点路由 + 状态落库。"""

    def __init__(self):
        self.checkpointer = build_checkpointer()
        # 延迟导入避免循环依赖（review_graph 需要 import 本类作类型）
        from app.compliance.workflows.review_graph import build_review_graph

        self.graph = build_review_graph(self).compile(checkpointer=self.checkpointer)
        self.supervisor = SupervisorAgent()
        self.extractor = ExtractorAgent()
        self.reporter = ReporterAgent()
        self.hitl = HitlManager()
        # 能力层（skills）
        self._parse_skill = ParseSkill()
        self._playbook_skill = PlaybookSkill()
        self._rag_skill = RagSkill()
        self._risk_skill = RiskSkill()
        self._report_skill = ReportSkill()

    # ===================== 状态落库 =====================

    def _persist_status(self, review_id: str, status: str, **extra) -> None:
        """把审查阶段/计数写入 compliance_reviews（后台线程自开会话，不跨线程持 db）。

        落库失败仅记日志不阻断流水线（审查结果通过后续节点继续推进）。
        """
        db = SessionLocal()
        try:
            row = db.query(ComplianceReview).filter(ComplianceReview.id == review_id).first()
            if row is None:
                logger.warning("persist_status: review %s not found", review_id)
                return
            row.status = status
            for k, v in extra.items():
                if hasattr(row, k) and v is not None:
                    setattr(row, k, v)
            db.commit()
            logger.info("review %s status=%s", review_id, status)
        except Exception as e:  # noqa: BLE001
            logger.warning("persist_status failed for review %s: %s", review_id, e)
        finally:
            db.close()

    # 阶段进度映射（前端进度条 0~100 用，与 state.PHASE_ORDER 对应）
    _PHASE_PROGRESS = {
        STATUS_PARSING: 10,
        STATUS_PLANNING: 25,
        STATUS_REVIEWING: 50,
        STATUS_REFLECTING: 70,
        STATUS_PENDING_HUMAN: 80,
        STATUS_GENERATING: 90,
        STATUS_COMPLETED: 100,
    }

    # ===================== 图节点（调用 skills/agents） =====================

    def parse_document(self, state: dict) -> dict:
        """节点 parse：加载并拆分条款，判定合同类型，产出 key_info 骨架。"""
        ctx = {
            "document_id": state.get("document_id"),
            "file_path": state.get("file_path"),
            "mime_type": state.get("mime_type"),
        }
        result = self._parse_skill.execute(ctx)
        self._persist_status(state["review_id"], STATUS_PARSING)
        if not result.get("ok"):
            self._persist_status(state["review_id"], STATUS_FAILED, error_message=result.get("error"))
            return {**state, "status": STATUS_FAILED, "error": result.get("error")}
        data = result["data"]
        return {
            **state,
            "raw_text": data["raw_text"],
            "doc_type": data["doc_type"],
            "clauses": data["clauses"],
            "key_info": data["key_info"],
            "status": STATUS_PARSING,
        }

    def supervise(self, state: dict) -> dict:
        """节点 supervise：文档分类复核 + 审查计划列表。"""
        plan = self.supervisor.plan_review(
            parsing_result={"doc_type": state.get("doc_type")},
            contract_type_override=state.get("contract_type_override"),
        )
        self._persist_status(state["review_id"], STATUS_PLANNING)
        return {**state, "review_plan": plan["plan"], "status": STATUS_PLANNING}

    def extract_clauses(self, state: dict) -> dict:
        """节点 extract：条款类型分类 + 关键信息提取。"""
        result = self.extractor.extract(
            {"clauses": state.get("clauses") or [], "raw_text": state.get("raw_text") or ""}
        )
        self._persist_status(state["review_id"], STATUS_PLANNING)
        return {
            **state,
            "clauses": result.get("clauses") or state.get("clauses") or [],
            "key_info": result.get("key_info") or state.get("key_info") or {},
        }

    def review_clauses(self, state: dict) -> dict:
        """节点 review：Playbook 命中 → 风险识别（5 类 × 3 级）。"""
        self._persist_status(state["review_id"], STATUS_REVIEWING)
        clauses = state.get("clauses") or []
        # 1) Playbook 命中（确定性线索，test 模式主路径）
        pb = self._playbook_skill.execute(
            {"clauses": clauses, "rules": state.get("rules") or []}
        )
        # 2) 风险识别（test 模式用 Playbook 命中转风险；openai 融合 LLM）
        rr = self._risk_skill.execute(
            {"clauses": clauses, "rules": state.get("rules") or []}
        )
        risks = (rr.get("data") or {}).get("risks") if rr.get("ok") else []
        counts = {
            "high_risk_count": sum(1 for r in risks if r.get("risk_level") == "high"),
            "medium_risk_count": sum(1 for r in risks if r.get("risk_level") == "medium"),
            "low_risk_count": sum(1 for r in risks if r.get("risk_level") == "low"),
        }
        self._persist_status(state["review_id"], STATUS_REVIEWING, **counts)
        return {**state, "risks": risks, "status": STATUS_REVIEWING}

    def reflect(self, state: dict) -> dict:
        """节点 reflect：自反思质量评估。

        MVP 质量分（确定性）：有风险且完成全流程 → 高分；重审越多置信度越低。
        should_retry 据此决定是否回 review 重审（最多 compliance_max_retry 次）。
        """
        self._persist_status(state["review_id"], STATUS_REFLECTING)
        retry_count = int(state.get("retry_count") or 0)
        risks = state.get("risks") or []
        quality = 1.0 if risks else 0.3
        if retry_count > 0:
            quality = max(0.3, quality - 0.15 * retry_count)
        next_retry = retry_count + 1
        self._persist_status(state["review_id"], STATUS_REFLECTING, retry_count=next_retry)
        return {
            **state,
            "retry_count": next_retry,
            "quality_score": round(quality, 2),
            "status": STATUS_REFLECTING,
        }

    def compare_template(self, state: dict) -> dict:
        """节点 compare：模板比对（P1 预留）；MVP 无 template_id 时不启用，原样返回。"""
        return state

    def human_review(self, state: dict) -> dict:
        """节点 human_review：HITL 记录（MVP 简化，不真正 interrupt）。

        MVP：把高风险条款号标记为 pending_human 并在 compliance_human_actions 留痕
        （记录「等待人工确认」）；无论是否有 high 风险都继续 generate_report，不阻塞。
        真正 interrupt/resume 留 P1（hitl.py 已备 build_resume_command）。
        """
        self._persist_status(state["review_id"], STATUS_PENDING_HUMAN)
        pending = [
            r.get("clause_number")
            for r in (state.get("risks") or [])
            if r.get("risk_level") == "high"
        ]
        return {**state, "pending_human_review": pending, "status": STATUS_PENDING_HUMAN}

    def generate_report(self, state: dict) -> dict:
        """节点 generate_report：组装报告数据 → 生成 Word/HTML → 落库。

        reporting 模块（reporting/generator.py，Step 10）延迟导入；生成失败置 failed。
        """
        self._persist_status(state["review_id"], STATUS_GENERATING)
        report_ctx = {
            "doc_info": {
                "document_id": state.get("document_id"),
                "original_filename": state.get("original_filename") or "",
                "doc_type": state.get("doc_type"),
            },
            "key_info": state.get("key_info") or {},
            "clauses": state.get("clauses") or [],
            "risks": state.get("risks") or [],
        }
        rep = self._report_skill.execute(report_ctx)
        if not rep.get("ok"):
            self._persist_status(state["review_id"], STATUS_FAILED, error_message=rep.get("error"))
            return {**state, "status": STATUS_FAILED, "error": rep.get("error")}
        try:
            from datetime import datetime, timezone

            from app.compliance.reporting.generator import generate_reports_for_review

            review_id = state["review_id"]
            paths = generate_reports_for_review(
                review_id, rep["data"]["report_data"], compliance_doc_id=state.get("compliance_doc_id")
            )
            self._persist_status(
                state["review_id"],
                STATUS_COMPLETED,
                completed_at=datetime.now(timezone.utc),
            )
            logger.info("review %s completed, reports: %s", review_id, paths)
            return {
                **state,
                "report_path": (paths or {}).get("html"),
                "status": STATUS_COMPLETED,
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("report generation failed: %s", e)
            self._persist_status(state["review_id"], STATUS_FAILED, error_message=str(e))
            return {**state, "status": STATUS_FAILED, "error": str(e)}

    # ===================== 条件边 =====================

    def should_compare(self, state: dict) -> str:
        """条件边：有 template_id → compare；否则 skip。MVP 无模板比对，恒 skip。"""
        return "compare" if state.get("template_id") else "skip"

    def should_retry(self, state: dict) -> str:
        """条件边：质量不达标且未超重试上限 → retry；HITL 启用且有高风险 → human；
        否则 → skip_human（直接生成报告）。"""
        quality = float(state.get("quality_score") or 0.0)
        retry = int(state.get("retry_count") or 0)
        max_retry = int(settings.compliance_max_retry)
        if quality < settings.compliance_quality_threshold and retry <= max_retry:
            return "retry"
        if settings.compliance_hitl_enabled and any(
            r.get("risk_level") == "high" for r in (state.get("risks") or [])
        ):
            return "human"
        return "skip_human"

    # ===================== 对外执行入口 =====================

    def start_review(
        self,
        review_id: str,
        document_id: str,
        compliance_doc_id: str,
        file_path: str,
        mime_type: str,
        user_id: Optional[str] = None,
        rules: Optional[list] = None,
        original_filename: str | None = None,
    ) -> dict:
        """启动一次审查（同步，FastAPI BackgroundTasks 线程内调用）。

        Args:
            review_id: compliance_reviews.id（thread_id=review-<id>）。
            document_id: 业务表 documents.id。
            compliance_doc_id: compliance_documents.id。
            file_path / mime_type: 文档路径与类型（parse skill 用）。
            user_id: 发起人（落 created_by 由 service 写入，这里仅随 state 传入）。
            rules: 活跃 Playbook 规则（service 按合同类型过滤后喂入）。
            original_filename: 展示用原名（报告 doc_info）。

        Returns:
            {"thread_id": ..., "status": 最终状态, "error": 可选项}
        """
        initial: dict = {
            "review_id": review_id,
            "document_id": document_id,
            "compliance_doc_id": compliance_doc_id,
            "file_path": file_path,
            "mime_type": mime_type,
            "user_id": user_id,
            "rules": rules or [],
            "original_filename": original_filename or "",
            "status": STATUS_PARSING,
            "retry_count": 0,
        }
        config = _thread_config(review_id)
        try:
            final = None
            for event in self.graph.stream(initial, config, stream_mode="updates"):
                # updates 模式：每个节点返回 {节点名: state_update}；合并取最后一次状态
                for update in event.values():
                    if isinstance(update, dict) and update.get("status"):
                        final = update
            status = (final or {}).get("status", STATUS_FAILED)
            if status in (STATUS_COMPLETED, STATUS_FAILED):
                self._persist_status(review_id, status)
            return {
                "thread_id": config["configurable"]["thread_id"],
                "status": status,
                "error": (final or {}).get("error"),
            }
        except Exception as e:  # noqa: BLE001
            logger.exception("review run failed: %s", e)
            self._persist_status(review_id, STATUS_FAILED, error_message=str(e))
            return {
                "thread_id": config["configurable"]["thread_id"],
                "status": "failed",
                "error": str(e),
            }


@lru_cache
def get_harness() -> ComplianceHarness:
    """Harness 单例（lru_cache；main.py lifespan 预热用，可选）。"""
    return ComplianceHarness()