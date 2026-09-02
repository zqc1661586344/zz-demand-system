"""Extractor Agent — 条款类型分类 + 关键信息提取。

职责（设计文档 F01 / §5.4）：
  1. 条款分类：给每条条款标注 clause_type（parties/term/payment/.../other）；
  2. 关键信息提取：从合同全文抽取 KeyInfo（party_a/party_b/amount/term/...）。

执行路径：
  - test 模式：规则关键词匹配（确定性、无 LLM）；
  - openai/ollama：结构化 LLM（KeyInfo/Clause 输出）+ 规则兜底。
"""

from typing import Optional

from app.compliance.agents.base import AgentBase, get_structured_llm
from app.compliance.agents.prompts.extractor_prompt import build_extract_key_info_prompt
from app.compliance.schemas.review import ClauseType, KeyInfo


_CLAUSE_TYPE_RULES: list[tuple[ClauseType, list[str]]] = [
    (
        ClauseType.PARTIES,
        ["甲方", "乙方", "用人单位", "劳动者", "公司名称", "统一社会信用代码", "住址"],
    ),
    (ClauseType.TERM, ["合同期限", "劳动合同期限", "有效期", "试用期", "自.*起至"]),
    (ClauseType.PAYMENT, ["工资", "薪酬", "支付", "结算", "付款", "报酬"]),
    (ClauseType.DELIVERY, ["交付", "发货", "验收", "交付时间"]),
    (ClauseType.PENALTY, ["违约金", "违约责任", "赔偿", "损失"]),
    (ClauseType.IP, ["知识产权", "著作权", "专利", "商标", "归属"]),
    (ClauseType.CONFIDENTIALITY, ["保密", "竞业限制", "商业秘密", "非公开"]),
    (ClauseType.DISPUTE, ["争议解决", "仲裁", "诉讼", "管辖"]),
    (ClauseType.TERMINATION, ["解除合同", "终止合同", "解除条件", "离职"]),
    (ClauseType.FORCE_MAJEURE, ["不可抗力", "意外事件", "不能预见"]),
]


def _classify_clause_type(text: str, title: str | None = None) -> ClauseType:
    """规则关键词分类：条款原文或标题命中即匹配。命中多个取优先级最前的。"""
    haystack = f"{title or ''} {text or ''}"
    for ctype, keywords in _CLAUSE_TYPE_RULES:
        for kw in keywords:
            if kw in haystack:
                return ctype
    return ClauseType.OTHER


_KEYINFO_RULES: list[tuple[str, list[str], str]] = [
    ("party_a", ["甲方", "用人单位", "公司", "法定代表人"], ""),
    ("party_b", ["乙方", "劳动者", "个人", "身份证"], ""),
    ("amount", ["金额", "人民币", "¥", "￥", "元整"], ""),
    ("term", ["合同期限", "自", "起至"], ""),
    ("payment_method", ["支付方式", "结算方式", "银行账户"], ""),
    ("penalty_cap", ["违约金", "上限", "不超过"], ""),
    ("confidentiality_period", ["保密期限", "保密期", "竞业限制"], ""),
    ("dispute_resolution", ["争议解决", "仲裁委员会", "人民法院"], ""),
]


def _extract_key_info_rule_based(text: str) -> dict:
    """规则抽取 KeyInfo（test 模式用）。
    启发式：关键词所在段落或其后 50 字符作为候选值。
    """
    info: dict = {}
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    haystack = text or ""
    for field, keywords, _ in _KEYINFO_RULES:
        for kw in keywords:
            if kw in haystack:
                idx = haystack.find(kw)
                snippet = haystack[idx : idx + 120].strip()
                info[field] = snippet[:200]
                break
    return info


class ExtractorAgent(AgentBase):
    name = "extractor"

    def extract(
        self,
        parsing_result: dict,
    ) -> dict:
        """执行条款分类 + 关键信息提取。

        Args:
            parsing_result: {"clauses": [...], "raw_text": "..."}。

        Returns:
            {"clauses": [含 clause_type 的条款], "key_info": dict}。
        """
        clauses = list(parsing_result.get("clauses") or [])
        raw_text = parsing_result.get("raw_text") or ""

        # 1) 条款分类
        for c in clauses:
            ctype = _classify_clause_type(c.get("content", ""), c.get("title"))
            c["clause_type"] = ctype.value if isinstance(ctype, ClauseType) else ctype

        # 2) 关键信息
        if self.test_mode:
            key_info = _extract_key_info_rule_based(raw_text)
            self.log(f"test extract: {len(clauses)} clauses, key_info keys={list(key_info.keys())}")
        else:
            key_info = self._extract_with_llm(raw_text)

        return {"clauses": clauses, "key_info": key_info}

    def _extract_with_llm(self, raw_text: str) -> dict:
        """openai/ollama 路径：用结构化 LLM 抽 KeyInfo；失败降级规则抽取。"""
        structured = get_structured_llm(KeyInfo)
        if structured is None:
            return _extract_key_info_rule_based(raw_text)
        try:
            prompt = build_extract_key_info_prompt(raw_text)
            result = structured.invoke(prompt)
            if hasattr(result, "model_dump"):
                data = result.model_dump(exclude_none=True)
                self.log(f"llm key_info extracted: {list(data.keys())}")
                return data
        except Exception as e:  # noqa: BLE001
            self.log(f"llm extract key_info failed: {e}, fallback rules")
        return _extract_key_info_rule_based(raw_text)
