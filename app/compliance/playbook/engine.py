"""Playbook 规则引擎 — 三层匹配（设计文档 §5.8 / F08）。

  1. **关键词/正则（确定性）**：match_type="keyword"，match_pattern 即关键词包或正则，
     对条款原文直接匹配，命中即产生候选（MVP 主路径，test 模式确定可验证）。
  2. **语义匹配（预留钩子）**：match_type="semantic"/"hybrid"，按规则语义阈值做向量匹配。
     MVP 依赖法规向量库（collection=compliance_regulations）时启用；test 模式/空库
     降级为关键词扩展匹配。引擎不直接持 DB 会话，规则由 service 层喂入（便于单测）。
  3. **LLM 判定（预留钩子）**：hybrid 模式下对候选命中做 LLM 最终确认；
     test 模式直接采信确定性层结果，保证测试可复现。

输出（每命中一项）：
{
  "rule_id": 规则 id, "name": 规则名, "risk_level": high/medium/low,
  "red_line": bool, "standard_position": 企业标准立场, "suggested_clause": 建议措辞,
  "legal_basis_ref": 法规依据线索, "clause_number": 命中条款号, "clause_content": 条款原文,
  "matched_by": keyword/semantic/llm, "confidence": 0~1 置信度,
}
"""

import re
from typing import Optional

from app.logging_config import get_logger

logger = get_logger(__name__)


def _split_keywords(pattern: str | None) -> list[str]:
    """把 match_pattern 拆成关键词列表。

    MVP 约定：match_pattern 为逗号/顿号分隔的关键词串（如"试用期,试用期工资"），
    或以 "re:" 前缀开头表示正则表达式（如 "re:违约金.*%30"）。均未命中返回空列表。
    """
    if not pattern:
        return []
    if pattern.startswith("re:"):
        return [pattern[3:]]  # 交给 _keyword_hit 里按正则分支处理
    seps = re.split(r"[,，、;；\s]+", pattern.strip())
    return [s for s in seps if s]


def _keyword_hit(text: str, pattern: str | None) -> bool:
    """关键词/正则层：pattern 含任一关键词则命中；re: 前缀按正则整体匹配。"""
    if not pattern:
        return False
    if pattern.startswith("re:"):
        try:
            return re.search(pattern[3:], text) is not None
        except re.error:
            logger.warning("invalid regex in playbook pattern: %r", pattern)
            return False
    return any(kw and kw in text for kw in _split_keywords(pattern))


def _semantic_hit(text: str, rule: dict, threshold: float) -> bool:
    """语义层（预留钩子）：MVP 降级为关键词扩展命中。

    说明：真正的语义向量匹配需要条款与规则描述在同一向量空间、且法规/规则向量库可用。
    MVP 阶段（法规空库 + test 模式确定性要求）这里先按关键词层判定，
    若规则的 match_pattern 为空则用 name/description 分词做包含匹配。
    openai 模式 + 向量库就绪后，可替换为 PGVector 检索实现（见 knowledge/vector_store）。
    """
    if _keyword_hit(text, rule.get("match_pattern")):
        return True
    if rule.get("description"):
        return any(kw and kw in text for kw in _split_keywords(rule["description"]))
    return False


def _llm_confirm(text: str, rule: dict, llm=None) -> bool:
    """LLM 判定层（预留钩子）：test 模式直接采信确定性命中结果。

    说明：hybrid 规则在 openai 模式可让 LLM 结合法规做最终确认；
    test 模式无 LLM 结构化能力，一律返回 True（命中即确认），保证流程可跑通。
    """
    if llm is None:
        return True
    # 预留：llm.invoke(确认 prompt) 的实现放 agents/reviewer（Step 8），这里不展开
    return True


def _rule_confidence(rule: dict, matched_by: str) -> float:
    """命中置信度：keyword 确定性最高（0.95）；semantic 按阈值（0.8+）；LLM 判定 1.0。"""
    if matched_by == "keyword":
        return 0.95
    if matched_by == "llm":
        return 1.0
    # semantic：取规则配置阈值，最低保底 0.6
    return max(0.6, float(rule.get("match_threshold", 0.8)))


def _match_rule(text: str, clause_number: str, rule: dict, llm=None) -> Optional[dict]:
    """单条规则对单条款的匹配（三层按序探测）。命中返回候选 dict，否则 None。"""
    match_type = rule.get("match_type", "keyword")

    matched_by = None
    if _keyword_hit(text, rule.get("match_pattern")):
        matched_by = "keyword"
    elif match_type in ("semantic", "hybrid") and _semantic_hit(
        text, rule, float(rule.get("match_threshold", 0.8))
    ):
        matched_by = "semantic"
    elif match_type == "hybrid" and _llm_confirm(text, rule, llm):
        matched_by = "llm"

    # hybrid 规则：确定性命中后仍须 LLM 确认（test 模式 _llm_confirm 恒真）
    if match_type == "hybrid" and matched_by == "keyword":
        if not _llm_confirm(text, rule, llm):
            return None

    if matched_by is None:
        return None

    return {
        "rule_id": rule.get("id") or rule.get("rule_id"),
        "name": rule.get("name"),
        "risk_level": rule.get("risk_level", "medium"),
        "red_line": bool(rule.get("red_line", False)),
        "negotiable": bool(rule.get("negotiable", True)),
        "standard_position": rule.get("standard_position"),
        "suggested_clause": rule.get("suggested_clause"),
        "legal_basis_ref": rule.get("legal_basis_ref"),
        "clause_number": clause_number,
        "clause_content": text[:500],  # 报告只展示命中条款摘要
        "matched_by": matched_by,
        "confidence": _rule_confidence(rule, matched_by),
    }


def match_rules_for_clauses(
    clauses: list[dict],
    rules: list[dict],
    llm=None,
) -> list[dict]:
    """对一批条款跑所有活跃规则，返回全部命中候选。

    Args:
        clauses: 条款列表，每项 {"clause_number", "content", ...}。
        rules: 活跃规则列表（由 service 层按 contract_type 过滤后喂入），
               每项为 compliance_playbooks 行转 dict 或等价结构。
        llm: 可选 LLM（OpenAI 模式 hybrid 判定用；test 模式不传）。

    Returns:
        命中候选列表，按风险等级排序（high 在前）。空规则/空条款返回 []。
    """
    hits: list[dict] = []
    for clause in clauses:
        text = clause.get("content") or ""
        number = clause.get("clause_number") or ""
        if not text.strip():
            continue
        for rule in rules:
            hit = _match_rule(text, number, rule, llm)
            if hit:
                hits.append(hit)
    # 风险等级权重：high=0 / medium=1 / low=2；同权重按规则 priority 升序
    level_order = {"high": 0, "medium": 1, "low": 2}
    hits.sort(key=lambda h: (level_order.get(h["risk_level"], 3), h.get("clause_number", "")))
    logger.info("playbook engine: %d clauses -> %d rule hits", len(clauses), len(hits))
    return hits