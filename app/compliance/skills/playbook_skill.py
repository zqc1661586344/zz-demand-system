"""Playbook Skill — 规则匹配能力层封装。

职责：对条款列表跑 Playbook 三层匹配（keyword/semantic/hybrid），返回命中候选。
test 模式纯 keyword 确定性匹配；openai/ollama 模式可喂入 LLM 做 hybrid 判定。

ctx 输入键：
    clauses: 条款列表 [{clause_number, content, ...}]
    rules:   活跃 Playbook 规则列表（service 层按合同类型过滤后喂入）
    llm:     (可选) 非 test 模式 hybrid 判定用

返回：{"ok": True, "data": {"hits": [命中候选]}}；失败 {"ok": False, "error"}。
"""

from app.compliance.agents.base import get_llm_for_compliance
from app.compliance.playbook.engine import match_rules_for_clauses
from app.compliance.skills.base import SkillBase


class PlaybookSkill(SkillBase):
    name = "playbook"

    def execute(self, ctx: dict) -> dict:
        clauses = ctx.get("clauses") or []
        rules = ctx.get("rules") or []
        if not clauses:
            return self.err("playbook skill: 缺 clauses")
        if not rules:
            self.log("no playbook rules configured, skip")
            return self.ok({"hits": []})

        llm = ctx.get("llm")
        try:
            hits = match_rules_for_clauses(clauses, rules, llm=llm)
            self.log(f"playbook matched {len(hits)} hits across {len(clauses)} clauses")
            return self.ok({"hits": hits})
        except Exception as e:  # noqa: BLE001
            self.log(f"playbook match failed: {e}")
            return self.err(f"playbook skill 匹配失败：{e}")
