"""法规 RAG Skill（app/compliance/skills/rag_skill.py）——能力层统一封装。

职责（设计文档 F03）：为审查检索相关法规、司法解释，并做**引用强制校验**（防幻觉）：
  1. 按条款/风险描述语义检索法规命中（knowledge.retrieval.search，空库返回 []）；
  2. 把 LLM 输出的引用 ref_content 与候选条款原文逐字比对
     （citation_verifier.verify_references）；匹配失败或空库 → verified=False + 标记
     「需人工核实」（needs_human_check=True）。

ctx 输入键（由 review_graph 的 review 节点调用）：
    query:         检索语（通常为条款内容或风险描述）
    references:    待校验的引用列表 [{"ref_type","ref_name","ref_article","ref_content"}, ...]
    top_k:         可选，检索条数（默认 settings.compliance_rag_top_k）
    regulation_type: 可选，法规类型过滤（law/judicial_interpretation/...）

返回：{"ok": True, "data": {"references": [已回写 verified/needs_human_check 的引用], "hits": [...], "count": int}}
    或 {"ok": False, "error"}。
"""

from app.config import settings
from app.compliance.knowledge.citation_verifier import verify_references
from app.compliance.knowledge.retrieval import search as search_regulations
from app.compliance.skills.base import SkillBase


class RagSkill(SkillBase):
    name = "rag"

    def execute(self, ctx: dict) -> dict:
        query = ctx.get("query") or ""
        references = ctx.get("references") or []
        if not query:
            return self.err("rag skill: 缺 query")
        if not references:
            # 无引用需要校验 → 检索命中即可（供 review 注入法规候选）
            try:
                hits = search_regulations(
                    query,
                    top_k=ctx.get("top_k") or settings.compliance_rag_top_k,
                    regulation_type=ctx.get("regulation_type"),
                )
                self.log(f"retrieved {len(hits)} regulation hits (query={query[:40]})")
                return {"ok": True, "data": {"references": [], "hits": hits, "count": len(hits)}}
            except Exception as e:  # noqa: BLE001 — 检索失败降级，不阻断审查
                self.log(f"rag retrieve failed: {e}")
                return self.err(f"rag skill 检索失败：{e}")

        # 有引用 → 检索候选池 + 强制校验（空库时 verify_references 全部 verified=False）
        try:
            hits = search_regulations(
                query,
                top_k=ctx.get("top_k") or settings.compliance_rag_top_k,
                regulation_type=ctx.get("regulation_type"),
            )
        except Exception as e:  # noqa: BLE001
            self.log(f"rag retrieve failed, verify against empty pool: {e}")
            hits = []

        # 候选池（校验的比对对象）
        candidate_pool = [
            {
                "content": h.get("content") or "",
                "article_number": h.get("article_number") or "",
                "regulation_id": h.get("regulation_id") or "",
            }
            for h in hits
        ]

        verified = verify_references(references, candidate_pool)
        self.log(
            f"verified {len(verified)} references "
            f"(verified_true={sum(1 for r in verified if r.get('verified'))})"
        )
        return {"ok": True, "data": {"references": verified, "hits": hits, "count": len(verified)}}