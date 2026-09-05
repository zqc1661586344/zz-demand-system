"""条款拆分器 — 把合同全文按「第X条」拆成条款（审查最小单元）。

策略（设计文档 F01 / §7.3.2）：
  1. **正则主切分**：按「第X条」（支持阿拉伯数字、一~十百千万中文数字、`3.1` 小节式）
     切分；「第三章」等章节标题不触发切分（只认「条」层级）。
  2. **LLM 校正续拆**（预留钩子）：对单条过长/混入多条款的情况，openai 模式可调用
     LLM 进一步拆出条款类型与子款；test 模式纯正则，保证测试环境确定性。

输出每项：{"clause_number": 条号, "title": 标题(可选), "content": 条款原文}。
页面号由上层（parser）从 Document 的 page 元数据补，这里不做分页。
"""

import re
from app.logging_config import get_logger

logger = get_logger(__name__)

# 「第X条」：阿拉伯数字 | 中文数字(一二三…百千万) | 小节式(3.1)；可选后缀「之X条」
_CLAUSE_RE = re.compile(
    r"第(?:\d+\.\d+|[一二三四五六七八九十百千万零]+|\d+)(?:之[一二三四五六七八九十百千万零\d]+)?条"
)


def _looks_like_title(line: str) -> bool:
    """条款首段是否像标题：较短、非以「第…条」开头、不含句号。"""
    if _CLAUSE_RE.match(line):
        return False
    if len(line) > 40 or "。" in line or "；" in line:
        return False
    return bool(line.strip())


def split_clauses_from_text(
    text: str,
    llm=None,  # 预留：LLM 校正用（test 模式纯正则，不需 LLM）
) -> list[dict]:
    """把合同全文切成条款列表。

    Args:
        text: 合同全文（由 load_document 拼接）。
        llm: 可选 LLM 实例（后续 LLM 校正续拆的钩子，MVP 未用）。

    Returns:
        list[dict]，每项 {"clause_number", "title", "content"}。
        无「第X条」标记时，整篇作为唯一一条「第一条」返回。
    """
    raw = (text or "").replace("\r\n", "\n")
    if not raw.strip():
        logger.warning("clause_splitter: empty text, returning empty list")
        return []

    markers = list(_CLAUSE_RE.finditer(raw))
    if not markers:
        logger.info("clause_splitter: no 第X条 markers found, treat whole text as one clause")
        return [{"clause_number": "第一条", "title": None, "content": raw.strip()}]

    clauses: list[dict] = []
    for i, m in enumerate(markers):
        number = m.group(0)  # 如「第三条」「第3.1条」
        start = m.start()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(raw)
        body = raw[start:end].strip()
        # 尝试解析标题：去掉条号行后的第一行短文本
        title = None
        lines = body.split("\n")
        if len(lines) >= 2:
            first_line = lines[0].strip()
            second_line = lines[1].strip()
            # 条号行 + 单行短标题（如「第三条　合同期限」）
            if not _CLAUSE_RE.match(first_line) and len(first_line) <= 40:
                body = "\n".join(lines[1:]).strip()
                title = first_line
            elif _looks_like_title(second_line):
                body = "\n".join(lines[2:]).strip()
                title = second_line
        clauses.append({"clause_number": number, "title": title, "content": body})

    logger.info("clause_splitter: split %d clauses from text", len(clauses))
    return clauses


def _refine_with_llm(clauses: list[dict], llm) -> list[dict]:
    """LLM 校正续拆（预留）：对过长的条款进一步拆分子款 / 标注类型。

    MVP 阶段仅预留钩子；本函数当前做保守合并：
      - 单条约 2000 字以上记一条 warning，不做自动拆分（交由 extractor 在条款内处理）。
    test / 环境无 LLM 时直接原样返回（确保测试确定性）。
    """
    for c in clauses:
        if len(c["content"]) > 2000:
            logger.debug(
                "clause %s is long (%d chars), sub-splitting deferred to extractor",
                c["clause_number"],
                len(c["content"]),
            )
    return clauses