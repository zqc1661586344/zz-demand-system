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
from pathlib import Path
from typing import Optional

from app.compliance.agents.extractor import ExtractorAgent
from app.compliance.agents.reporter import ReporterAgent
from app.compliance.agents.supervisor import SupervisorAgent
from app.compliance.harness.checkpointer import build_checkpointer
from app.compliance.harness.hitl import HitlManager
from app.compliance.models.clause import ComplianceClause, ComplianceKeyInfo
from app.compliance.models.playbook import CompliancePlaybook
from app.compliance.models.report import ComplianceReport
from app.compliance.models.review import (
    ComplianceRisk,
    ComplianceRiskReference,
    ComplianceReview,
)
from app.compliance.skills.parse_skill import ParseSkill
from app.compliance.skills.playbook_skill import PlaybookSkill
from app.compliance.skills.rag_skill import RagSkill
from app.compliance.skills.report_skill import ReportSkill
from app.compliance.skills.risk_skill import RiskSkill
from app.compliance.workflows.state import (
    STATUS_COMPARING,
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

    def _persist_results(
        self,
        *,
        review_id: str,
        compliance_doc_id: str,
        clauses: list[dict],
        key_info: dict,
        risks: list[dict],
        report_paths: dict,
        risk_counts: dict,
    ) -> None:
        """审查完成后事务化写入 clauses → key_info → risks → references → reports.

        同一 review 重跑时先清旧数据（CASCADE ondelete 会连带清除 risks/references）。
        任意写入失败整体回滚并置 review 为 failed，保证不会出现"半落库"。
        """
        import uuid as _uuid
        from datetime import datetime, timezone as _tz

        db = SessionLocal()
        try:
            review = db.query(ComplianceReview).filter(ComplianceReview.id == review_id).first()
            if review is None:
                logger.warning("persist_results: review %s not found", review_id)
                return

            db.query(ComplianceReport).filter(ComplianceReport.review_id == review_id).delete(
                synchronize_session=False
            )

            existing_clause_ids = {
                r[0]
                for r in db.query(ComplianceClause.id)
                .filter(ComplianceClause.compliance_doc_id == compliance_doc_id)
                .all()
            }

            clause_id_by_index: dict[int, str] = {}
            for idx, c in enumerate(clauses or []):
                clause_id = str(_uuid.uuid4())
                c_obj = ComplianceClause(
                    id=clause_id,
                    compliance_doc_id=compliance_doc_id,
                    clause_number=c.get("clause_number") or f"第{idx + 1}条",
                    clause_type=c.get("clause_type"),
                    title=c.get("title") or (c.get("content") or "")[:80],
                    content=c.get("content") or "",
                    page_number=c.get("page_number"),
                    sort_order=idx,
                )
                db.add(c_obj)
                clause_id_by_index[idx] = clause_id
                existing_clause_ids.discard(clause_id)

            for old_id in existing_clause_ids:
                db.query(ComplianceClause).filter(ComplianceClause.id == old_id).delete()

            db.query(ComplianceKeyInfo).filter(
                ComplianceKeyInfo.compliance_doc_id == compliance_doc_id
            ).delete(synchronize_session=False)
            for k, v in (key_info or {}).items():
                if v is None or v == "":
                    continue
                db.add(
                    ComplianceKeyInfo(
                        id=str(_uuid.uuid4()),
                        compliance_doc_id=compliance_doc_id,
                        field_key=k,
                        field_value=str(v),
                        confidence=None,
                        clause_id=None,
                    )
                )

            db.flush()

            db.query(ComplianceRisk).filter(ComplianceRisk.review_id == review_id).delete(
                synchronize_session=False
            )

            _rule_id_cache: dict[str, str | None] = {}

            for idx, r in enumerate(risks or []):
                rule_hint = r.get("playbook_rule_id")
                playbook_id: str | None = None
                if rule_hint:
                    playbook_id = rule_hint
                elif r.get("rule_id"):
                    playbook_id = r["rule_id"]
                elif r.get("rule_name"):
                    cache_key = r["rule_name"]
                    if cache_key not in _rule_id_cache:
                        _pb_row = (
                            db.query(CompliancePlaybook.id)
                            .filter(CompliancePlaybook.name == r["rule_name"])
                            .first()
                        )
                        _rule_id_cache[cache_key] = _pb_row[0] if _pb_row else None
                    playbook_id = _rule_id_cache[cache_key]

                clause_idx = r.get("clause_index")
                clause_id = (
                    clause_id_by_index.get(int(clause_idx)) if clause_idx is not None else None
                )

                risk_obj = ComplianceRisk(
                    id=str(_uuid.uuid4()),
                    review_id=review_id,
                    clause_id=clause_id,
                    risk_level=r.get("risk_level", "low"),
                    risk_category=r.get("risk_category", "other"),
                    description=r.get("description") or "",
                    suggestion=r.get("suggestion"),
                    suggestion_reason=r.get("suggestion_reason"),
                    playbook_rule_id=playbook_id,
                    ai_confidence=float(r.get("ai_confidence") or 1.0),
                    sort_order=idx,
                )
                db.add(risk_obj)
                db.flush()

                for rf in r.get("legal_references") or []:
                    db.add(
                        ComplianceRiskReference(
                            id=str(_uuid.uuid4()),
                            risk_id=risk_obj.id,
                            ref_type=rf.get("ref_type") or "regulation",
                            ref_name=rf.get("ref_name") or "",
                            ref_article=rf.get("ref_article"),
                            ref_content=rf.get("ref_content") or "",
                            ref_source_url=rf.get("ref_source_url"),
                            verified=bool(rf.get("verified")),
                        )
                    )

            now = datetime.now(_tz.utc)
            for fmt in ("html", "word", "pdf"):
                p = report_paths.get(fmt) if report_paths else None
                if p:
                    path_obj = Path(p)
                    size = path_obj.stat().st_size if path_obj.is_file() else None
                    db.add(
                        ComplianceReport(
                            id=str(_uuid.uuid4()),
                            review_id=review_id,
                            format=fmt,
                            file_path=p,
                            file_size=size,
                            generated_at=now,
                        )
                    )

            review.high_risk_count = int(risk_counts.get("high", 0))
            review.medium_risk_count = int(risk_counts.get("medium", 0))
            review.low_risk_count = int(risk_counts.get("low", 0))

            db.commit()
            logger.info(
                "persist_results: review=%s clauses=%d risks=%d",
                review_id,
                len(clauses or []),
                len(risks or []),
            )
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.exception("persist_results FAILED for review %s: %s", review_id, e)
            try:
                review = db.query(ComplianceReview).filter(ComplianceReview.id == review_id).first()
                if review:
                    review.status = STATUS_FAILED
                    review.error_message = f"persist_results: {e}"
                    db.commit()
            except Exception as e2:  # noqa: BLE001
                logger.warning("failed to mark review %s as failed: %s", review_id, e2)
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

    @staticmethod
    def _guard_failed(state: dict) -> dict | None:
        if state.get("status") == STATUS_FAILED:
            return state
        return None

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
            self._persist_status(
                state["review_id"], STATUS_FAILED, error_message=result.get("error")
            )
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
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
        plan = self.supervisor.plan_review(
            parsing_result={"doc_type": state.get("doc_type")},
            contract_type_override=state.get("contract_type_override"),
        )
        self._persist_status(state["review_id"], STATUS_PLANNING)
        return {**state, "review_plan": plan["plan"], "status": STATUS_PLANNING}

    def extract_clauses(self, state: dict) -> dict:
        """节点 extract：条款类型分类 + 关键信息提取。"""
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
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
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
        self._persist_status(state["review_id"], STATUS_REVIEWING)
        clauses = state.get("clauses") or []
        rr = self._risk_skill.execute({"clauses": clauses, "rules": state.get("rules") or []})
        risks = (rr.get("data") or {}).get("risks") if rr.get("ok") else []
        counts = {
            "high_risk_count": sum(1 for r in risks if r.get("risk_level") == "high"),
            "medium_risk_count": sum(1 for r in risks if r.get("risk_level") == "medium"),
            "low_risk_count": sum(1 for r in risks if r.get("risk_level") == "low"),
        }
        self._persist_status(state["review_id"], STATUS_REVIEWING, **counts)
        return {**state, "risks": risks, "status": STATUS_REVIEWING}

    def reflect(self, state: dict) -> dict:
        """多维度自反思：覆盖率 + 置信度 + 重审衰减。"""
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
        self._persist_status(state["review_id"], STATUS_REFLECTING)
        retry_count = int(state.get("retry_count") or 0)
        risks = state.get("risks") or []
        clauses = state.get("clauses") or []

        if clauses and not risks:
            coverage_score = 0.4
        elif not clauses:
            coverage_score = 0.2
        else:
            coverage_score = 1.0

        if risks:
            confs = [float(r.get("ai_confidence") or 1.0) for r in risks]
            avg_conf = sum(confs) / len(confs)
        else:
            avg_conf = 0.5

        decay = max(0.0, 0.15 * retry_count)
        quality = max(0.1, (coverage_score * 0.5 + avg_conf * 0.5) - decay)
        next_retry = retry_count + 1

        self._persist_status(state["review_id"], STATUS_REFLECTING, retry_count=next_retry)
        logger.info(
            "reflect: q=%.2f cov=%.2f conf=%.2f retry=%d r=%d c=%d",
            quality,
            coverage_score,
            avg_conf,
            retry_count,
            len(risks),
            len(clauses),
        )
        return {
            **state,
            "retry_count": next_retry,
            "quality_score": round(quality, 2),
            "coverage_score": round(coverage_score, 2),
            "avg_confidence": round(avg_conf, 2),
            "status": STATUS_REFLECTING,
        }

    def compare_template(self, state: dict) -> dict:
        """企业模板比对：复用 Playbook standard_position 做偏离检测 + 建议补全 + 红线升级。"""
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
        risks = list(state.get("risks") or [])
        rules = state.get("rules") or []
        deviations = 0

        for i, risk in enumerate(risks):
            cn = risk.get("clause_number") or ""
            best_rule = None
            best_score = 0.0
            for rule in rules:
                rp = (rule.get("match_pattern") or "").lower()
                if rp and (rp in cn.lower() or cn.lower() in rp):
                    score = float(rule.get("priority") or 100)
                    if score > best_score:
                        best_score = score
                        best_rule = rule
            if not best_rule:
                continue

            enriched = dict(risk)
            std_pos = best_rule.get("standard_position")
            sugg = best_rule.get("suggested_clause")
            red_line = best_rule.get("red_line", False)

            if std_pos:
                enriched["template_deviation"] = True
                enriched["template_standard"] = std_pos
                deviations += 1

            if sugg and not enriched.get("suggestion"):
                enriched["suggestion"] = sugg
            elif sugg and enriched.get("suggestion") and sugg not in enriched.get("suggestion", ""):
                enriched["suggestion"] = f"{enriched['suggestion']}（企业标准：{sugg}）"

            if red_line and enriched.get("risk_level") != "high":
                enriched["risk_level"] = "high"
                enriched["red_line_flag"] = True

            risks[i] = enriched

        counts = {
            "high_risk_count": sum(1 for r in risks if r.get("risk_level") == "high"),
            "medium_risk_count": sum(1 for r in risks if r.get("risk_level") == "medium"),
            "low_risk_count": sum(1 for r in risks if r.get("risk_level") == "low"),
        }
        self._persist_status(
            state["review_id"],
            STATUS_COMPARING,
            template_deviations=deviations,
            **counts,
        )
        logger.info("compare_template: %d deviations", deviations)
        return {**state, "risks": risks, "template_deviations": deviations, **counts}

    def human_review(self, state: dict) -> dict:
        """节点 human_review：HITL 记录（MVP 简化，不真正 interrupt）。

        MVP：把高风险条款号标记为 pending_human 并在 compliance_human_actions 留痕（记录「等待人工确认」）；无论是否有 high 风险都继续 generate_report，不阻塞。
        真正 interrupt/resume 留 P1（hitl.py 已备 build_resume_command）。
        """
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
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
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
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
            report_data = rep["data"]["report_data"]
            paths = generate_reports_for_review(
                review_id,
                report_data,
                compliance_doc_id=state.get("compliance_doc_id"),
            )

            risk_counts = report_data.get("risk_counts") or {}
            for k in ("high", "medium", "low"):
                risk_counts.setdefault(k, 0)

            self._persist_results(
                review_id=review_id,
                compliance_doc_id=state.get("compliance_doc_id"),
                clauses=report_data.get("clauses") or state.get("clauses") or [],
                key_info=report_data.get("key_info") or state.get("key_info") or {},
                risks=report_data.get("risks") or state.get("risks") or [],
                report_paths=paths,
                risk_counts=risk_counts,
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
        """有 template_id 或 rules 含 standard_position/suggested_clause → compare。"""
        rules = state.get("rules") or []
        if state.get("template_id") or any(
            r.get("standard_position") or r.get("suggested_clause") for r in rules
        ):
            return "compare"
        return "skip"

    def should_retry(self, state: dict) -> str:
        """质量不达标或置信度过低 → retry；HITL 且有 high → human；否则 → skip_human。"""
        quality = float(state.get("quality_score") or 0.0)
        avg_conf = float(state.get("avg_confidence") or 0.5)
        retry = int(state.get("retry_count") or 0)
        max_retry = int(settings.compliance_max_retry)
        low_conf = (
            avg_conf < 0.6 and (state.get("clauses") or []) and not (state.get("risks") or [])
        )
        if (quality < settings.compliance_quality_threshold or low_conf) and retry <= max_retry:
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
