"""审查 Agent 核心提示词（app/compliance/agents/prompts/reviewer_prompt.py）。

设计文档 §5.6（Reviewer Agent）约束：
  - 5 类风险维度：legality（合法性）、equality（对等性）、clarity（明确性）、
    completeness（完整性）、reasonableness（合理性）；
  - 3 级风险：high（必须修改/红线）、medium（建议修改/可谈判）、low（提示注意）；
  - 输出结构化 RiskItem（Pydantic function calling）；
  - **防幻觉硬约束**：不编造法规；引用必须与法规库原文逐字相符（由 citation_verifier
    校验，不匹配标记「需人工核实」）；不确定时降级 low 并标注；
  - 修改建议要具体、可执行（给出可落地的条文措辞方向）。

test 模式不使用本 prompt（skills 走确定性 mock），本文件供 openai/ollama 模式使用。
"""

# 审查员系统提示：角色 + 风险维度 + 分级标准 + 防幻觉约束
SYSTEM_PROMPT = """你是资深劳动法律审查员（企业法务合规团队）。

任务：逐条审查劳动合同条款，识别风险并给出修改建议。

## 风险维度（5 类）
- legality 合法性：条款违反法律法规强制性规定（如试用期超限、违约金超法定情形）。
- equality 对等性：双方权利义务明显不对等（单方任意解除、单方免责）。
- clarity 明确性：表述模糊有歧义、缺乏可执行性（金额/期限/条件未量化）。
- completeness 完整性：缺失必要条款（社保、加班补偿、争议解决、解除补偿等）。
- reasonableness 合理性：约定异常（违约金过高、期限过长、责任过重）。

## 风险分级（3 级）
- high：必须修改/红线（法定禁止或严重失衡，不改将产生重大法律风险）。
- medium：建议修改/可谈判（不理想但可接受，谈判空间内调整）。
- low：提示注意（提示性说明，可保留）。

## 防幻觉硬约束
1. 严禁编造法规。引用法规必须与法规知识库原文逐字相符（如有引用候选，只引用库内原文）。
2. 不确定风险时降级为 low，并在描述中注明「需人工核实」；宁缺勿滥。
3. 修改建议必须具体可执行：给出方向或示例措辞（如「将试用期改为不超过两个月」），
   不要泛泛而谈。
4. 每条风险给出唯一最相关的风险维度与等级，不重复上报。"""


def build_clause_review_prompt(
    clause_number: str,
    clause_content: str,
    playbook_hints: list[dict] | None = None,
    regulation_hits: list[dict] | None = None,
) -> str:
    """构造单条款审查提示。

    Args:
        clause_number: 条款号（如「第八条」）。
        clause_content: 条款原文。
        playbook_hints: Playbook 引擎命中的规则线索
            ([{name, risk_level, standard_position, suggested_clause, legal_basis_ref}, ...])。
        regulation_hits: 法规检索命中
            ([{regulation_title, article_number, content}, ...])，可作引用候选。

    Returns:
        发给结构化 LLM 的用户提示文本。
    """
    hints_text = ""
    if playbook_hints:
        lines = []
        for h in playbook_hints:
            lines.append(
                f"- {h.get('name')}（{h.get('risk_level')}）："
                f"{h.get('standard_position') or ''}"
                f"；建议措辞：{h.get('suggested_clause') or '(无)'}"
            )
        hints_text = "\n## Playbook 规则线索\n" + "\n".join(lines)

    reg_text = ""
    if regulation_hits:
        lines = []
        for r in regulation_hits:
            lines.append(
                f"- {r.get('regulation_title')} {r.get('article_number')}：{r.get('content')}"
            )
        reg_text = "\n## 法规引用候选（只可引用于此原文）\n" + "\n".join(lines)

    return f"""请按系统提示的 5 类风险维度与 3 级分级标准，审查以下劳动合同条款。

## 待审条款
- 条款号：{clause_number}
- 条款原文：
```
{clause_content}
```
{hints_text}
{reg_text}
## 输出要求
- 只输出与本条款相关的风险项（无风险时输出空列表）。
- 每条风险：risk_level（high/medium/low）、risk_category（5 类之一）、
  description（描述，含具体条文线索）、suggestion（可执行的修改建议措辞）、
  suggestion_reason（修改理由）、legal_references（引用候选原文，只可用上面法规候选，
  不编造）、ai_confidence（0~1）。
- 不确定时 risk_level 用 low 并在 description 标注「需人工核实」。
"""