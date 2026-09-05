"""引用强制校验器 — LLM 输出的法规引用必须与法规库原文逐字匹配。

防幻觉核心组件（设计文档 F03）：
  1. LLM 输出的 `ref_content`（法规原文摘录）与法规库条款原文做归一化相似度对比；
  2. 归一化：去所有空白、统一全角→半角、转小写，消除排版差异；
  3. 相似度 >= settings.compliance_citation_similarity_threshold（默认 0.95）→ verified=True；
  4. 不匹配或法规库为空 → verified=False 并标记「需人工核实」（调用方据此处理）。

策略偏保守：宁可标记「需人工核实」也不放行可疑引用，与设计文档防幻觉机制一致。
"""

from difflib import SequenceMatcher
from typing import Optional

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


def normalize_text(text: str) -> str:
    """归一化文本：去空白、全角转半角、转小写（用于逐字对比）。

    法规原文常带全角空格/换行/多余空行，归一化后能对齐「排版差异」而保留「文字差异」。
    """
    chars = []
    for ch in text:
        code = ord(ch)
        # 全角字符（65281~65374）转半角（33~126），全角空格（12288）转半角空格（32）
        if 0xFF01 <= code <= 0xFF5E:
            chars.append(chr(code - 0xFEE0))
        elif code == 0x3000:  # 全角空格（中文对齐）→ 普通空格
            chars.append(" ")
        else:
            chars.append(ch)
    # 去所有空白字符（含空格/制表/换行/全角空格归一后的空格）
    return "".join(ch for ch in chars if not ch.isspace()).lower()


def text_similarity(a: str, b: str) -> float:
    """归一化后的字符串相似度（difflib ratio，逐字匹配的保守度量）。"""
    return SequenceMatcher(None, a, b).ratio()


def verify_citation(ref_content: str, candidate_articles: list[dict]) -> bool:
    """校验一条引用是否与法规库原文逐字匹配。

    Args:
        ref_content: LLM 输出的法规原文摘录（引用内容）。
        candidate_articles: 候选条款列表，每项至少含 `content` 字段
            （通常为检索命中的条款原文，或该法规的全部条款）。

    Returns:
        True 仅当：candidates 非空，且 ref_content 与至少一个条款原文
        归一化相似度达到阈值。

    Note:
        - 空库（candidates 为空）恒返回 False → 调用方标记「需人工核实」；
        - ref_content 可能为引用摘录（少于全文），ratio 偏低时宁可判 False；
          引用应输出完整条款原文，而非摘要——Prompt 已约束。
    """
    if not ref_content or not candidate_articles:
        return False

    ref_norm = normalize_text(ref_content)
    if not ref_norm:
        return False
    if len(ref_norm) < 10:
        # 过短的摘录无法支撑「逐字匹配」判定，保守降级
        logger.debug("citation too short to verify: %r", ref_content[:50])
        return False

    threshold = settings.compliance_citation_similarity_threshold
    best = 0.0
    for article in candidate_articles:
        content = (article.get("content") or "") if isinstance(article, dict) else str(article)
        if not content:
            continue
        sim = text_similarity(ref_norm, normalize_text(content))
        best = max(best, sim)
        if best >= threshold:
            logger.debug("citation verified: sim=%.3f >= %.3f", best, threshold)
            return True

    logger.info("citation NOT verified: best sim=%.3f < %.3f", best, threshold)
    return False


def verify_references(
    references: list[dict],
    candidate_articles: Optional[list[dict]] = None,
) -> list[dict]:
    """批量校验一组引用，就地标记 verified 字段。

    Args:
        references: 引用列表，每项为 dict，至少含 `ref_content`；输出时回写 `verified`。
        candidate_articles: 检索命中的候选条款（所有引用的公共候选池）。
            空/None 时全部 verified=False（法规库为空 → 降级标记）。

    Returns:
        回写 verified 后的原列表（每项增加 verified: bool）。
    """
    pool = candidate_articles or []
    if not pool:
        logger.info("regulation KB empty — all citations marked unverified (需人工核实)")
    for ref in references:
        ref["verified"] = verify_citation(ref.get("ref_content", ""), pool)
        if not ref["verified"]:
            # 调用方可据此在展示层追加「需人工核实」标记
            ref["needs_human_check"] = True
    return references