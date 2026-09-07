"""ComplianceHarness — 审查工作流运行时（app/compliance/harness/runtime.py）。

封装（设计文档 §5.5.3）：LangGraph 图、checkpointer（PostgresSaver / InMemorySaver 三级
回退，见 checkpointer.py）、图节点路由（parse/supervise/extract/review/reflect/compare/
human_review/generate_report）、条件边（should_compare/should_retry）、状态落库
（审查阶段写 compliance_reviews.status 与风险计数）。

执行方式：POST /reviews 创建任务后由 FastAPI BackgroundTasks 或 Celery worker 调用
`start_review(...)`；图节点按 LangGraph StateGraph 顺序执行，每节点把阶段/计数写库；
前端轮询 GET /reviews/{id} 获取进度。含高风险时 human_review 节点先落库再进入 END，
等待人工审核后通过 POST /reviews/{id}/resume 触发 generate_report 生成正式报告。

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
from app.compliance.models.clause import ComplianceClause, ComplianceKeyInfo
from app.compliance.models.playbook import CompliancePlaybook
from app.compliance.models.report import ComplianceReport
from app.compliance.models.review import (
    ComplianceRisk,
    ComplianceRiskReference,
    ComplianceReview,
)
from app.compliance.skills.parse_skill import ParseSkill
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


def compute_reflect_quality(
    clauses: list,
    risks: list,
    retry_count: int = 0,
    avg_conf_override: float | None = None,
    rules: list | None = None,
    llm_error_count: int = 0,
) -> tuple[float, float, float, list[str]]:
    """纯函数：计算 reflect 阶段的 quality 评分 + degraded 原因。

    关键修正：零 Playbook 规则场景 → coverage 打折、quality 上限封顶；
    LLM 逐条款失败计数 → 失败率 ≥30% 时大幅降 quality + degraded_reasons。

    返回 (quality, coverage_score, avg_confidence, degraded_reasons)。
    """
    degraded: list[str] = []
    rules_list = rules or []
    has_rules = bool(rules_list)

    if not clauses:
        coverage_score = 0.2
        degraded.append("no_clauses_extracted")
    else:
        coverage_score = 1.0

    # LLM 失败率降 coverage（>=30% 时严重惩罚）
    if clauses and llm_error_count > 0:
        fail_ratio = llm_error_count / len(clauses)
        degraded.append(f"llm_failed_{llm_error_count}/{len(clauses)}")
        if fail_ratio >= 0.5:
            coverage_score *= 0.3
        elif fail_ratio >= 0.3:
            coverage_score *= 0.6
        elif fail_ratio >= 0.1:
            coverage_score *= 0.85

    if not has_rules:
        coverage_score *= 0.5
        degraded.append("no_playbook_rules")

    has_verified_ref = any(
        any(ref.get("verified") for ref in (r.get("legal_references") or [])) for r in risks
    )
    if clauses and not risks:
        coverage_score *= 0.9
        if not has_rules:
            degraded.append("llm_only_no_rules_no_risks")

    if avg_conf_override is not None:
        avg_conf = avg_conf_override
    elif risks:
        confs = [float(r.get("ai_confidence") or 1.0) for r in risks]
        avg_conf = sum(confs) / len(confs)
    elif clauses:
        avg_conf = 0.9 if has_rules else 0.7
    else:
        avg_conf = 0.5

    decay = max(0.0, 0.15 * retry_count)
    quality = max(0.1, (coverage_score * 0.5 + avg_conf * 0.5) - decay)

    if not has_rules:
        quality = min(quality, 0.5)

    if not degraded and not has_verified_ref:
        degraded.append("no_verified_references")

    return quality, coverage_score, avg_conf, degraded


class ComplianceHarness:
    """审查工作流运行时：图构建(checkpointer) + 节点路由 + 状态落库。"""

    def __init__(self):
        self.checkpointer = build_checkpointer()
        self.checkpointer_type = self._detect_checkpointer_type(self.checkpointer)
        if self.checkpointer_type == "memory":
            logger.warning(
                "checkpointer=InMemorySaver — HITL resume WILL fail after process restart. "
                "Configure PostgreSQL (database_url or vector_store_url with postgresql://) for production."
            )
        elif self.checkpointer_type == "none":
            logger.warning(
                "checkpointer=None (langgraph not installed) — graph compiles but HITL resume disabled."
            )
        # 延迟导入避免循环依赖（review_graph 需要 import 本类作类型）
        from app.compliance.workflows.review_graph import build_review_graph

        self.graph = build_review_graph(self).compile(checkpointer=self.checkpointer)
        self.supervisor = SupervisorAgent()
        self.extractor = ExtractorAgent()
        self.reporter = ReporterAgent()
        # 能力层（skills）
        self._parse_skill = ParseSkill()
        self._rag_skill = RagSkill()
        self._risk_skill = RiskSkill()
        self._report_skill = ReportSkill()

    @staticmethod
    def _detect_checkpointer_type(cp) -> str:
        """识别 checkpointer 实际类型，返回 'postgres' / 'memory' / 'none'。"""
        if cp is None:
            return "none"
        cls_name = type(cp).__name__
        if "Postgres" in cls_name:
            return "postgres"
        if "InMemory" in cls_name or "Memory" in cls_name:
            return "memory"
        return "unknown"

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
                .filter(ComplianceClause.review_id == review_id)
                .all()
            }

            clause_id_by_index: dict[int, str] = {}
            clause_id_by_number: dict[str, str] = {}
            for idx, c in enumerate(clauses or []):
                clause_id = str(_uuid.uuid4())
                c_obj = ComplianceClause(
                    id=clause_id,
                    review_id=review_id,
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
                if c_obj.clause_number:
                    clause_id_by_number[c_obj.clause_number] = clause_id
                existing_clause_ids.discard(clause_id)

            for old_id in existing_clause_ids:
                db.query(ComplianceClause).filter(ComplianceClause.id == old_id).delete()

            db.query(ComplianceKeyInfo).filter(ComplianceKeyInfo.review_id == review_id).delete(
                synchronize_session=False
            )
            for k, v in (key_info or {}).items():
                if v is None or v == "":
                    continue
                db.add(
                    ComplianceKeyInfo(
                        id=str(_uuid.uuid4()),
                        review_id=review_id,
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

                clause_id = r.get("clause_id")
                if not clause_id:
                    clause_number = r.get("clause_number") or ""
                    clause_id = clause_id_by_number.get(clause_number)

                risk_obj = ComplianceRisk(
                    id=r.get("id") or str(_uuid.uuid4()),
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
        """节点 review：Playbook 命中 → 风险识别 → clause_id/rule_id 追溯注入 → RAG 引用校验。

        review_hints（来自 reflect 节点）：
          - mode=playbook_only → 跳过 LLM 路径，只用 Playbook 命中（LLM 全失败降级）
          - rag_top_k=N → RAG 引用检索扩大 top_k（无 verified 引用时用）
        """
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
        self._persist_status(state["review_id"], STATUS_REVIEWING)
        clauses = state.get("clauses") or []
        rules = state.get("rules") or []
        hints = state.get("review_hints") or {}

        risk_ctx = {"clauses": clauses, "rules": rules, "hints": hints}
        rr = self._risk_skill.execute(risk_ctx)
        risk_data = rr.get("data") or {}
        raw_risks = risk_data.get("risks") if rr.get("ok") else []
        llm_error_count = int(risk_data.get("llm_error_count") or 0)

        clauses_by_number = {(c.get("clause_number") or ""): c.get("clause_id") for c in clauses}
        rules_by_category = {
            (r.get("risk_category") or r.get("category") or ""): r.get("id") for r in rules
        }
        rules_by_name = {(r.get("name") or r.get("rule_name") or ""): r.get("id") for r in rules}

        import uuid as _uuid

        risks = []
        for r in raw_risks:
            risk = dict(r)
            risk.setdefault("id", str(_uuid.uuid4()))
            clause_number = risk.get("clause_number") or ""
            if not risk.get("clause_id") and clause_number in clauses_by_number:
                risk["clause_id"] = clauses_by_number[clause_number]
            risk_category = risk.get("risk_category") or risk.get("category") or ""
            if not risk.get("playbook_rule_id"):
                risk["playbook_rule_id"] = rules_by_category.get(
                    risk_category
                ) or rules_by_name.get(risk_category)
            risks.append(risk)

        risks = self._enrich_references_with_rag(risks)
        counts = {
            "high_risk_count": sum(1 for r in risks if r.get("risk_level") == "high"),
            "medium_risk_count": sum(1 for r in risks if r.get("risk_level") == "medium"),
            "low_risk_count": sum(1 for r in risks if r.get("risk_level") == "low"),
        }
        self._persist_status(state["review_id"], STATUS_REVIEWING, **counts)
        logger.info(
            "review_clauses: %d raw → %d enriched risks, clause_id resolved=%d rule_id resolved=%d",
            len(raw_risks),
            len(risks),
            sum(1 for r in risks if r.get("clause_id")),
            sum(1 for r in risks if r.get("playbook_rule_id")),
        )
        return {
            **state,
            "risks": risks,
            "llm_error_count": llm_error_count,
            "status": STATUS_REVIEWING,
        }

    def _enrich_references_with_rag(self, risks: list[dict]) -> list[dict]:
        """对 risks 的 legal_references 做法规检索 + 引用校验（空库/异常降级不阻断）。

        三种情况：
          1. refs 已有内容 → 检索候选池 + 校验每条引用（citation_verifier 判定 verified）
          2. refs 为空但有 query → 主动检索法规，取 top-3 补充（verified=False, needs_human_check=True）
          3. 无 query → 跳过

        并发：ThreadPoolExecutor 按 risk 粒度并发 RAG 检索，最多 8 worker。
        """
        if not risks:
            return risks

        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = min(8, len(risks))

        def _enrich_one(risk: dict) -> dict:
            refs = list(risk.get("legal_references") or [])
            query = (risk.get("description") or risk.get("clause_number") or "").strip()
            if not query:
                return risk
            if refs:
                rag_result = self._rag_skill.execute({"query": query, "references": refs})
                if rag_result.get("ok"):
                    verified = (rag_result.get("data") or {}).get("references") or refs
                    for ref in verified:
                        if not ref.get("verified"):
                            ref.setdefault("needs_human_check", True)
                    risk["legal_references"] = verified
                else:
                    for ref in refs:
                        ref.setdefault("verified", False)
                        ref.setdefault("needs_human_check", True)
            else:
                rag_result = self._rag_skill.execute({"query": query})
                if rag_result.get("ok"):
                    hits = (rag_result.get("data") or {}).get("hits") or []
                    score_floor = 0.5
                    qualified = [
                        h
                        for h in hits
                        if float(h.get("score") or h.get("similarity") or 0.0) >= score_floor
                    ]
                    injected = self._rag_hits_to_references(qualified[:3])
                    if injected:
                        risk["legal_references"] = injected
            return risk

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_enrich_one, risk): risk for risk in risks}
            for fut in as_completed(futures):
                risk = futures[fut]
                try:
                    fut.result(timeout=15)
                except Exception as e:  # noqa: BLE001
                    logger.warning("rag enrichment failed for risk: %s", e)

        verified_count = 0
        supplemented_count = 0
        for risk in risks:
            for ref in risk.get("legal_references") or []:
                if ref.get("verified"):
                    verified_count += 1
            refs = risk.get("legal_references") or []
            if refs and not any(r.get("verified") for r in refs):
                supplemented_count += 1

        logger.info(
            "rag enrichment(parallel): verified=%d supplemented=%d total_risks=%d",
            verified_count,
            supplemented_count,
            len(risks),
        )
        return risks

    @staticmethod
    def _rag_hits_to_references(hits: list[dict]) -> list[dict]:
        """把 RagSkill 返回的 hits 转成 legal_references 格式。

        检索命中只是"候选法规依据"——默认 verified=False，需要 citation_verifier
        后续判定或法务人工确认。这比无条件 verified=True 更诚实。
        """
        refs = []
        for h in hits:
            score = h.get("score") or h.get("similarity") or 0.0
            refs.append(
                {
                    "ref_type": "regulation_retrieved",
                    "ref_name": h.get("title") or h.get("regulation_id") or "",
                    "ref_article": h.get("article_number") or h.get("article_id") or "",
                    "ref_content": h.get("content") or "",
                    "verified": False,
                    "needs_human_check": True,
                    "retrieval_score": round(float(score), 4),
                }
            )
        return refs

    def reflect(self, state: dict) -> dict:
        """多维度自反思：覆盖率 + 置信度 + 重审衰减 + 降级原因。

        degraded_reasons 回流为 review_hints —— retry 时 review_clauses 能采取
        纠正性策略（纯 Playbook 降级、扩大 RAG top_k、放宽阈值等），而不是空转重掷。
        """
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
        self._persist_status(state["review_id"], STATUS_REFLECTING)
        retry_count = int(state.get("retry_count") or 0)
        risks = state.get("risks") or []
        clauses = state.get("clauses") or []
        rules = state.get("rules") or []

        quality, coverage_score, avg_conf, degraded = compute_reflect_quality(
            clauses,
            risks,
            retry_count=retry_count,
            rules=rules,
            llm_error_count=int(state.get("llm_error_count") or 0),
        )
        next_retry = retry_count + 1

        hints = self._build_review_hints(degraded, clauses, risks, rules)

        self._persist_status(state["review_id"], STATUS_REFLECTING, retry_count=next_retry)
        logger.info(
            "reflect: q=%.2f cov=%.2f conf=%.2f retry=%d r=%d c=%d rules=%d degraded=%s hints=%s",
            quality,
            coverage_score,
            avg_conf,
            retry_count,
            len(risks),
            len(clauses),
            len(rules),
            degraded,
            hints,
        )

        result = {
            **state,
            "retry_count": next_retry,
            "quality_score": round(quality, 2),
            "coverage_score": round(coverage_score, 2),
            "avg_confidence": round(avg_conf, 2),
            "status": STATUS_REFLECTING,
            "review_hints": hints,
        }
        if degraded:
            result["degraded_reasons"] = degraded
        return result

    @staticmethod
    def _build_review_hints(
        degraded: list[str],
        clauses: list,
        risks: list,
        rules: list,
    ) -> dict:
        """把 degraded_reasons 转为 review_hints（retry 时 review 策略调整输入）。

        映射规则：
          - no_playbook_rules → mode=llm_only（无规则，纯 LLM 审查）
          - llm_failed_X/Y 且全失败 → mode=playbook_only（LLM 全挂，降级纯 Playbook）
          - no_verified_references → rag_top_k=5（扩大 RAG 重检索范围）
          - no_clauses_extracted → parse_retry=1（建议换解析策略）
        """
        hints: dict = {}
        if "no_playbook_rules" in degraded:
            hints["mode"] = "llm_only"
            hints["note"] = "no Playbook rules available — pure LLM review"
        if "no_clauses_extracted" in degraded:
            hints["parse_retry"] = 1
        if "no_verified_references" in degraded:
            hints["rag_top_k"] = 5
        for d in degraded:
            if d.startswith("llm_failed_"):
                try:
                    ratio = d.split("_")  # ["llm", "failed", "X", "Y"]
                    if len(ratio) == 4:
                        failed_n = int(ratio[2])
                        total_n = int(ratio[3])
                        if total_n > 0 and failed_n >= total_n:
                            hints["mode"] = "playbook_only"
                            hints["note"] = "LLM failed all clauses — fallback to Playbook only"
                            break
                except (ValueError, IndexError):
                    continue
        return hints

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
        """节点 human_review：HITL 阻塞落库后等待人工确认。

        必须先落库（risks/clauses/key_info）再进入 END，否则 pending_human 状态下
        前端拿不到任何风险明细 — 审查报告里最有价值的 high 风险会彻底消失。
        resume_review 从 checkpoint 取 state 再调 generate_report 生成报告。
        """
        guarded = self._guard_failed(state)
        if guarded is not None:
            return guarded
        review_id = state["review_id"]
        clauses = state.get("clauses") or []
        risks = state.get("risks") or []
        key_info = state.get("key_info") or {}
        risk_counts = state.get("risk_counts") or {}
        compliance_doc_id = state.get("compliance_doc_id") or ""

        if risks or clauses:
            self._persist_results(
                review_id=review_id,
                compliance_doc_id=compliance_doc_id,
                clauses=clauses,
                key_info=key_info,
                risks=risks,
                report_paths={},
                risk_counts=risk_counts,
            )
            logger.info(
                "human_review: persisted %d clauses / %d risks before blocking",
                len(clauses),
                len(risks),
            )

        self._persist_status(review_id, STATUS_PENDING_HUMAN)
        pending = [r.get("clause_number") for r in risks if r.get("risk_level") == "high"]
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
        """企业模板比对 — 保守下线（template_id 未真正加载模板，规则关键词无法匹配条款号）。

        后续模板比对应改为：1) 真正加载模板文档；2) 匹配 risk.description 或关联 clause.content，
        而非在 clause_number 上做 in 子串匹配。当前恒 skip，template_id 仅作预留字段。
        """
        return "skip"

    def should_retry(self, state: dict) -> str:
        """质量不达标或置信度过低 → retry；HITL 且有 high → human；否则 → skip_human。

        短路规则（空转保护）：
          - 零 Playbook 规则 + 零风险 → 不 retry（每次结果都一样，空跑 4 轮没意义）
          - LLM 全失败（所有条款都调挂了）→ 不 retry（retry 也必败）
        """
        quality = float(state.get("quality_score") or 0.0)
        avg_conf = float(state.get("avg_confidence") or 0.5)
        retry = int(state.get("retry_count") or 0)
        max_retry = int(settings.compliance_reflect_max_retry)
        clauses = state.get("clauses") or []
        risks = state.get("risks") or []
        rules = state.get("rules") or []
        llm_errors = int(state.get("llm_error_count") or 0)

        all_llm_failed = clauses and llm_errors >= len(clauses) and not risks

        # 短路：空转场景直接跳过 retry
        if not rules and not risks:
            logger.info("should_retry: zero rules + zero risks → skip retry (空转保护)")
        elif all_llm_failed:
            logger.info(
                "should_retry: LLM ALL failed (%d/%d) → skip retry (必败)",
                llm_errors,
                len(clauses),
            )
        elif (
            quality < settings.compliance_quality_threshold
            or (avg_conf < 0.6 and clauses and not risks)
        ) and retry <= max_retry:
            return "retry"

        if settings.compliance_hitl_enabled and any(r.get("risk_level") == "high" for r in risks):
            return "human"
        return "skip_human"

    # ===================== 对外执行入口 =====================

    def resume_review(self, review_id: str) -> dict:
        """人工确认后续跑 generate_report（HITL resume 入口）。

        关键：generate_report 之前必须把 DB 中人工决策过的风险（risk_level 修改、
        suggestion 重写、mark_false 剔除）合并回 state。否则报告里展示的仍是
        LLM 原始输出，人工审核等于白做 — 这是 resume 最容易踩的坑。
        """
        config = _thread_config(review_id)
        if self.checkpointer is None:
            return {
                "thread_id": config["configurable"]["thread_id"],
                "status": "failed",
                "error": "checkpointer unavailable — cannot resume, please re-initiate review",
            }
        try:
            tuple_result = self.checkpointer.get_tuple(config)
            if tuple_result is None:
                return {
                    "thread_id": config["configurable"]["thread_id"],
                    "status": "failed",
                    "error": "state not found in checkpointer — may have been lost after restart",
                }
            checkpoint = tuple_result.checkpoint
            channel_values = (
                checkpoint.get("channel_values") if isinstance(checkpoint, dict) else None
            )
            state = channel_values or {}
            if not isinstance(state, dict):
                state = dict(state) if state else {}
            if state.get("review_id") != review_id:
                state["review_id"] = review_id

            state = self._merge_human_decisions(review_id, state)

            result = self.generate_report(state)
            return {
                "thread_id": config["configurable"]["thread_id"],
                "status": result.get("status", STATUS_FAILED),
                "error": result.get("error"),
            }
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning("resume_review transient error (will be retried by Celery): %s", e)
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("resume_review failed (permanent): %s", e)
            self._persist_status(review_id, STATUS_FAILED, error_message=str(e))
            return {
                "thread_id": config["configurable"]["thread_id"],
                "status": "failed",
                "error": str(e),
            }

    def _merge_human_decisions(self, review_id: str, state: dict) -> dict:
        """把 DB 中人工审核的修改合并回 checkpoint state 的 risks。

        三种人工操作：
          1. mark_false / human_decision="rejected" → 从 state.risks 中剔除
          2. modify_level → 覆盖 risk_level，加 _human_modified=True
          3. edit_suggestion → 覆盖 suggestion，加 _human_modified=True, _human_note=...

        合并后重算 high/medium/low_risk_count，保证报告里的计数与展示一致。
        """
        try:
            from app.database import SessionLocal
            from app.compliance.models.review import ComplianceRisk

            db = SessionLocal()
            try:
                db_risks = (
                    db.query(ComplianceRisk).filter(ComplianceRisk.review_id == review_id).all()
                )
            finally:
                db.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("resume: cannot load human decisions from DB: %s", e)
            return state

        if not db_risks:
            return state

        db_by_id = {str(r.id): r for r in db_risks}
        original_risks = list(state.get("risks") or [])
        merged_risks = []
        rejected_count = 0
        modified_count = 0

        for risk in original_risks:
            rid = risk.get("id")
            db_row = db_by_id.get(rid) if rid else None

            if db_row is None:
                merged_risks.append(risk)
                continue

            decision = (db_row.human_decision or "na").lower()
            if decision in ("rejected", "false_positive", "mark_false"):
                rejected_count += 1
                logger.info("resume: filtering out risk %s (human_decision=%s)", rid, decision)
                continue

            merged = dict(risk)
            changed = False

            if db_row.risk_level and db_row.risk_level != merged.get("risk_level"):
                merged["risk_level"] = db_row.risk_level
                changed = True

            if db_row.suggestion and db_row.suggestion != merged.get("suggestion"):
                merged["suggestion"] = db_row.suggestion
                changed = True

            if db_row.description and db_row.description != merged.get("description"):
                merged["description"] = db_row.description
                changed = True

            if db_row.human_note:
                merged["_human_note"] = db_row.human_note
                changed = True

            if changed:
                merged["_human_modified"] = True
                modified_count += 1

            merged_risks.append(merged)

        counts = {"high": 0, "medium": 0, "low": 0}
        for r in merged_risks:
            lv = r.get("risk_level") or "medium"
            counts[lv] = counts.get(lv, 0) + 1

        logger.info(
            "resume: merged human decisions — kept=%d rejected=%d modified=%d counts=%s",
            len(merged_risks),
            rejected_count,
            modified_count,
            counts,
        )

        return {
            **state,
            "risks": merged_risks,
            "risk_counts": {
                **(state.get("risk_counts") or {}),
                **counts,
                "total": len(merged_risks),
            },
            "human_decision_count": len(db_risks),
            "human_rejected_count": rejected_count,
            "human_modified_count": modified_count,
        }

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
        template_id: Optional[str] = None,
        contract_type_override: Optional[str] = None,
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
            template_id: 合同模板 ID（compare 节点用）。
            contract_type_override: 合同类型 override（影响 Playbook 规则筛选）。

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
            "template_id": template_id,
            "contract_type_override": contract_type_override,
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
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning("review run transient error (will be retried by Celery): %s", e)
            raise
        except Exception as e:  # noqa: BLE001
            logger.exception("review run failed (permanent): %s", e)
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
