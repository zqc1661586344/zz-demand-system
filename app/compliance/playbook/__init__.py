"""审查规则引擎包（app/compliance/playbook/）—— 企业 Playbook 三层规则匹配。

engine.py 实现三层匹配（关键词/正则 → 语义 → LLM 判定）；default_rules/ 存放
按合同类型分类的默认规则包（labor_contract.json 等），由 playbook_service（Step 11）
在首次启动时播种到 compliance_playbooks 表。

用法：
    from app.compliance.playbook import match_rules_for_clauses
    hits = match_rules_for_clauses(clauses, rules)
"""

from app.compliance.playbook.engine import (
    match_rules_for_clauses,
)

__all__ = ["match_rules_for_clauses"]