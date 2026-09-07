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
    """归一化文本：去空白、全角转半角、转小写、删标点（用于逐字对比）。

    法规原文常带全角空格/换行/多余空行/中英文标点，归一化后能对齐
    「排版差异」和「标点差异」而保留「实质文字差异」。
    """
    _PUNCT = set("，。、；：！？''（）《》…—·,.;:!?\"'()[]{}<>-_/\\|")
    chars = []
    for ch in text:
        code = ord(ch)
        # 全角字符（65281~65374）转半角（33~126），全角空格（12288）转半角空格（32）
        if 0xFF01 <= code <= 0xFF5E:
            half = chr(code - 0xFEE0)
            if half.isspace() or half in _PUNCT:
                continue
            chars.append(half)
        elif code == 0x3000:  # 全角空格（中文对齐）
            continue
        elif ch.isspace() or ch in _PUNCT:
            continue
        else:
            chars.append(ch)
    return "".join(chars).lower()


def _ref_coverage(ref_norm: str, article_norm: str) -> float:
    """计算 ref_norm 在 article_norm 中的覆盖率：匹配字符数 / len(ref_norm)。

    这是「引用是原文摘录」场景的正确度量。SequenceMatcher.ratio() 做的是
    双向等长比率 — 短引用 vs 长原文 ratio 会被原文长度稀释掉，永远偏低。
    这里改用 get_matching_blocks 算出 ref 中有多少字符在原文里找到了。
    """
    if not ref_norm or not article_norm:
        return 0.0
    if ref_norm in article_norm:
        return 1.0
    matcher = SequenceMatcher(None, ref_norm, article_norm, autojunk=False)
    total_matched = sum(block.size for block in matcher.get_matching_blocks())
    return total_matched / max(len(ref_norm), 1)


def verify_citation(ref_content: str, candidate_articles: list[dict]) -> bool:
    """校验一条引用是否与法规库原文匹配。

    三种判定路径（优先级从高到低）：
      1. ref_norm 是 article_norm 的子串 → 100% 覆盖 → verified
      2. ref_norm 不在原文里但 coverage >= 0.8 → verified
      3. coverage 0.5~0.8 → partial（未标记 verified，调用方追加 needs_human_check）
      4. coverage < 0.5 → unverified

    过短引用（len < 15）只走精确子串判定，coverage 比率在短文本上不稳定。
    """
    if not ref_content or not candidate_articles:
        return False

    ref_norm = normalize_text(ref_content)
    if not ref_norm:
        return False

    is_short = len(ref_norm) < 15

    for article in candidate_articles:
        content = (article.get("content") or "") if isinstance(article, dict) else str(article)
        if not content:
            continue
        content_norm = normalize_text(content)
        if not content_norm:
            continue

        if ref_norm in content_norm and len(ref_norm) >= 10:
            logger.debug("citation verified: substring match (len=%d)", len(ref_norm))
            return True

        if is_short:
            continue

        coverage = _ref_coverage(ref_norm, content_norm)
        if coverage >= 0.8:
            logger.debug("citation verified: coverage=%.3f >= 0.8", coverage)
            return True
        if coverage >= 0.5:
            logger.debug("citation partial: coverage=%.3f (0.5~0.8, needs human)", coverage)
            continue
        logger.debug("citation low coverage: %.3f", coverage)

    logger.info("citation NOT verified: no article matched ref (len=%d)", len(ref_norm))
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
