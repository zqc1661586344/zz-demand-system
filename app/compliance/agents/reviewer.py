"""审查 Agent（app/compliance/agents/reviewer.py）——核心风险识别。

职责（设计文档 F02/§5.6）：逐条审查，识别 5 类风险（legality/equality/clarity/
completeness/reasonableness）× 3 级（high/medium/low），输出结构化 RiskItem + 修改建议。

执行路径：
  - test 模式：确定性 mock——直接复用 `playbook.engine.match_rules_for_clauses()` 的
    Playbook 命中转成风险项（不依赖外部 LLM，端到端可验证）。
  - openai/ollama：结构化 LLM 输出 RiskItem（low temperature 0.1），结合 Playbook 命中
    线索与法规引用候选（reviewer_prompt.build_clause_review_prompt）。

风险分类 fallback：Playbook 命中未明确风险维度时，按规则名/条款类型推断 risk_category。

并发：ThreadPoolExecutor 条款级并发审查，信号量限流（默认 8），per-call 超时 30s。
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

from app.compliance.agents.base import AgentBase, get_structured_llm
from app.compliance.agents.prompts.reviewer_prompt import build_clause_review_prompt
from app.compliance.playbook.engine import match_rules_for_clauses
from app.compliance.schemas.review import RiskItem, RiskItemList

logger = get_logger(__name__)

# Playbook 命中 → 风险维度的启发映射（按规则名关键词）
_CATEGORY_KEYWORDS: list[tuple[str, str]] = [
    ("试用期", "legality"),
    ("违约", "reasonableness"),
    ("红线", "legality"),
    ("社保", "legality"),
    ("竞业", "legality"),
    ("保密", "clarity"),
    ("加班", "equality"),
    ("补偿", "completeness"),
    ("争议解决", "clarity"),
]


def _category_for_hit(hit: dict) -> str:
    """根据 Playbook 命中的规则名/描述推断 risk_category（5 类之一）。"""
    haystack = f"{hit.get('name') or ''} {hit.get('standard_position') or ''}".lower()
    for kw, cat in _CATEGORY_KEYWORDS:
        if kw in haystack:
            return cat
    if hit.get("red_line"):
        return "legality"
    return "reasonableness"


def _hit_to_risk(hit: dict) -> dict:
    """把一个 Playbook 命中转成风险项 dict（供 API 入库 / 前端展示）。"""
    category = _category_for_hit(hit)
    return {
        "clause_number": hit.get("clause_number", ""),
        "clause_id": hit.get("clause_id"),
        "playbook_rule_id": hit.get("rule_id") or hit.get("playbook_rule_id"),
        "risk_level": hit.get("risk_level", "medium"),
        "risk_category": category,
        "description": _build_description(hit, category),
        "suggestion": hit.get("suggested_clause"),
        "suggestion_reason": hit.get("standard_position"),
        "legal_references": (
            [
                {
                    "ref_type": "playbook",
                    "ref_name": hit.get("legal_basis_ref") or "",
                    "ref_article": None,
                    "ref_content": hit.get("standard_position") or "",
                }
            ]
            if hit.get("legal_basis_ref")
            else []
        ),
        "ai_confidence": hit.get("confidence", 0.9),
    }


def _build_description(hit: dict, category: str) -> str:
    """组装风险描述（含条款号与规则理由，前端可直接展示）。"""
    base = hit.get("name") or "条款风险"
    reason = hit.get("standard_position") or ""
    return f"条款 {hit.get('clause_number') or ''}：{base}（{category}）。规则说明：{reason}"


def _merge_hits_and_llm_results(
    raw_hits: list[dict],
    llm_risks: list[RiskItem],
    clause: dict,
) -> list[RiskItem]:
    """确定性 hits + LLM 风险项 union 去重融合。

    规则：
      1. 先把所有确定性 hits 转成 RiskItem 并入（永不被否决）；
      2. 遍历 LLM 结果，按 `(clause_number, playbook_rule_id)` 去重：
         - LLM result 有 rule_id 且命中已存在 → 跳过（保留 hit 的 red_line 等精确标记）
         - LLM result 无 rule_id（纯 LLM 发现）→ 直接加入
         - LLM result 有 rule_id 但 hit 层没命中 → 加入
      3. 若 LLM 返回的 risk 没有 clause_number，用当前 clause 补齐。
    """
    clause_number = clause.get("clause_number", "")

    merged: list[RiskItem] = []
    seen_keys: set[tuple[str, str | None]] = set()

    for h in raw_hits:
        raw = _hit_to_risk(h)
        risk_item = RiskItem(**raw)
        rid = risk_item.playbook_rule_id
        key = (risk_item.clause_number, rid)
        seen_keys.add(key)
        merged.append(risk_item)

    for r in llm_risks:
        cn = r.clause_number or clause_number
        rid = r.playbook_rule_id
        key = (cn, rid)
        if rid and key in seen_keys:
            continue
        seen_keys.add(key)
        enriched = r.model_copy(update={"clause_number": cn} if not r.clause_number else {})
        merged.append(enriched)

    return merged


class ReviewerAgent(AgentBase):
    name = "reviewer"

    def review_clause(
        self,
        clause: dict,
        rules: Optional[list[dict]] = None,
        regulation_hits: Optional[list[dict]] = None,
    ) -> list[RiskItem]:
        """审查单条条款，返回风险项列表（无风险返回 []）。

        Args:
            clause: {"clause_number", "content", ...}。
            rules: 活跃 Playbook 规则（service 层按合同类型过滤后喂入）。
            regulation_hits: 法规引用候选（开放 ai 模式用）。
        """
        clause_number = clause.get("clause_number", "")
        text = clause.get("content", "")

        # test 模式：确定性 mock —— 用 Playbook 命中转风险项
        if self.test_mode:
            raw_hits = match_rules_for_clauses([clause], rules or [], llm=None)
            self.log(f"test review clause {clause_number}: {len(raw_hits)} hits")
            risks = []
            for h in raw_hits:
                raw = _hit_to_risk(h)
                risks.append(RiskItem(**raw))
            return risks

        # 非 test：结构化 LLM 输出（列表容器） + 融合 Playbook 命中线索
        structured = get_structured_llm(RiskItemList)
        if structured is None:
            # 结构化失败降级：仍用 Playbook 命中（确定性路径）
            raw_hits = match_rules_for_clauses([clause], rules or [], llm=None)
            return [RiskItem(**_hit_to_risk(h)) for h in raw_hits]

        try:
            prompt = build_clause_review_prompt(
                clause_number,
                text,
                playbook_hints=rules,
                regulation_hits=regulation_hits,
            )
            result = structured.invoke(prompt)
            if isinstance(result, RiskItemList):
                llm_risks = result.risks
            elif isinstance(result, list):
                llm_risks = [r for r in result if isinstance(r, RiskItem)]
            elif isinstance(result, RiskItem):
                llm_risks = [result]
            else:
                llm_risks = []

            # ── 后置融合：确定性 hits + LLM 结果 ──
            # 业界标准做法：确定性规则层保底 + LLM 增量发现；
            # 两层结果按 (clause_number, playbook_rule_id) 去重，规则命中永不被 LLM 否决。
            raw_hits = match_rules_for_clauses([clause], rules or [], llm=None)
            return _merge_hits_and_llm_results(raw_hits, llm_risks, clause)
        except Exception as e:  # noqa: BLE001
            self.log(f"LLM review failed for clause {clause_number}: {e}")
            raise

    def review_all(
        self,
        clauses: list[dict],
        rules: Optional[list[dict]] = None,
        regulation_hits: Optional[dict] = None,
        hints: Optional[dict] = None,
    ) -> tuple[list[RiskItem], int]:
        """逐条审查全部条款（并发），汇总所有风险。

        hints（来自 reflect 纠正反馈）：
          - mode=playbook_only → 跳过 LLM 路径，只用 Playbook 命中（LLM 全失败降级）
          - mode=llm_only → 无 Playbook 规则场景，纯 LLM 审查（默认无规则时即此模式）

        Returns:
            (risks, failed_count) — failed_count 是 LLM 调用异常/超时的条款数。
        """
        hints = hints or {}
        mode = hints.get("mode")

        if mode == "playbook_only":
            logger.info("review_all: hints.mode=playbook_only → skip LLM, Playbook only")
            all_risks: list[RiskItem] = []
            for clause in clauses:
                raw_hits = match_rules_for_clauses([clause], rules or [], llm=None)
                all_risks.extend(RiskItem(**_hit_to_risk(h)) for h in raw_hits)
            self.log(
                "review_all(playbook_only): %d clauses -> %d risks, 0 llm calls",
                len(clauses),
                len(all_risks),
            )
            return all_risks, 0

        if self.test_mode or len(clauses) <= 1:
            return self._review_all_serial(clauses, rules, regulation_hits)
        return self._review_all_parallel(clauses, rules, regulation_hits)

    def _review_all_serial(
        self,
        clauses: list[dict],
        rules: Optional[list[dict]] = None,
        regulation_hits: Optional[dict] = None,
    ) -> tuple[list[RiskItem], int]:
        """串行审查 —— test 模式或单条款场景。"""
        all_risks: list[RiskItem] = []
        failed = 0
        for clause in clauses:
            hits = (regulation_hits or {}).get(clause.get("clause_number"))
            try:
                risks = self.review_clause(clause, rules, hits)
                all_risks.extend(risks)
            except Exception as e:  # noqa: BLE001
                failed += 1
                self.log(f"LLM review FAILED clause {clause.get('clause_number')}: {e}")
        self.log(f"review_all: {len(clauses)} clauses -> {len(all_risks)} risks, {failed} failed")
        return all_risks, failed

    def _review_all_parallel(
        self,
        clauses: list[dict],
        rules: Optional[list[dict]] = None,
        regulation_hits: Optional[dict] = None,
    ) -> tuple[list[RiskItem], int]:
        """并发审查 — ThreadPoolExecutor + 信号量限流 + per-call 超时。"""
        max_workers = min(
            getattr(settings, "compliance_review_max_workers", 8),
            len(clauses),
        )
        per_call_timeout = getattr(settings, "compliance_review_call_timeout", 30)

        import threading

        semaphore = threading.Semaphore(max_workers)

        def _submit(clause: dict) -> tuple[list[RiskItem], bool]:
            hits = (regulation_hits or {}).get(clause.get("clause_number"))
            clause_number = clause.get("clause_number", "")
            semaphore.acquire()
            try:
                risks = self.review_clause(clause, rules, hits)
                return risks, False
            except FuturesTimeoutError:
                logger.warning(
                    "LLM review TIMEOUT clause %s (>%ds)", clause_number, per_call_timeout
                )
                return [], True
            except Exception as e:  # noqa: BLE001
                logger.warning("LLM review FAILED clause %s: %s", clause_number, e)
                return [], True
            finally:
                semaphore.release()

        all_risks: list[RiskItem] = []
        failed = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_submit, clause): clause.get("clause_number", "") for clause in clauses
            }
            for fut, cn in futures.items():
                try:
                    risks, had_error = fut.result(timeout=per_call_timeout + 5)
                    all_risks.extend(risks)
                    if had_error:
                        failed += 1
                except FuturesTimeoutError:
                    logger.warning(
                        "Future TIMEOUT clause %s (thread blocked >%ds)",
                        cn,
                        per_call_timeout + 5,
                    )
                    failed += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning("Future EXCEPTION clause %s: %s", cn, e)
                    failed += 1

        self.log(
            "review_all(parallel): %d clauses workers=%d -> %d risks, %d failed",
            len(clauses),
            max_workers,
            len(all_risks),
            failed,
        )
        return all_risks, failed
