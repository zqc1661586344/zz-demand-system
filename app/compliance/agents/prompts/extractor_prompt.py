"""Extractor Agent 提示词（app/compliance/agents/prompts/extractor_prompt.py）。

抽取 KeyInfo 时使用的提示词模板，供 extractor._extract_with_llm 调用。
"""

EXTRACT_KEY_INFO_PROMPT = """请从以下合同全文提取关键信息字段。
只输出有明确内容的字段，无内容留空。

合同全文：
{raw_text}
"""


def build_extract_key_info_prompt(raw_text: str, max_chars: int = 8000) -> str:
    """构造 KeyInfo 抽取提示词，超长截断避免超 LLM context。"""
    truncated = raw_text[:max_chars] if len(raw_text) > max_chars else raw_text
    return EXTRACT_KEY_INFO_PROMPT.format(raw_text=truncated)
