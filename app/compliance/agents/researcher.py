"""检索 Agent — 法规检索 + 引用强制校验。

职责（设计文档 F03）：每个风险点自动检索相关法规、司法解释；引用与法规库原文
逐字匹配校验（不匹配 → verified=False + 标记「需人工核实」）；法规有效性检查。

执行路径：
  - 所有模式都走同一套确定性逻辑（检索→引用校验），不依赖 LLM：
    `knowledge.retrieval.search()` 拿命中 → `knowledge.citation_verifier.verify_references()`
    逐字校验。
  - 法规空库（MVP 阶段）：检索返回空 → 引用校验降级全部 verified=False，
    由展示层标「需人工核实」——与计划确认的「空库降级」策略一致。
"""

from typing import Optional

from app.compliance.agents.base import AgentBase
from app.compliance.knowledge.citation_verifier import verify_references
from app.compliance.knowledge.retrieval import search as search_regulations


class ResearcherAgent(AgentBase):
    name = "researcher"

    def find_regulations(
        self,
        query: str,
        top_k: int = 5,
        regulation_type: Optional[str] = None,
    ) -> list[dict]:
        """按风险描述/条款关键词检索法规命中（空库返回 []，不抛错）。"""
        return search_regulations(
            query,
            top_k=top_k,
            regulation_type=regulation_type,
        )

    def verify_risk_references(
        self,
        references: list[dict],
        candidate_articles: Optional[list[dict]] = None,
    ) -> list[dict]:
        """对一组引用做强制校验：回写 verified（及 needs_human_check）。

        Args:
            references: [{"ref_type", "ref_name", "ref_article", "ref_content"}, ...]。
            candidate_articles: 候选条款池（检索命中转成，见 build_candidate_pool）；
                空/None → 全部 verified=False（空库降级）。

        Returns:
            回写 verified 后的引用列表。
        """
        return verify_references(references, candidate_articles)

    def build_candidate_pool(self, hits: list[dict]) -> list[dict]:
        """把检索命中转成引用校验候选池（[{content, article_number}, ...]）。"""
        return [
            {
                "content": h.get("content") or "",
                "article_number": h.get("article_number") or "",
                "regulation_id": h.get("regulation_id") or "",
            }
            for h in hits
        ]

    def research(self, risk_items: list[dict], top_k: int = 5) -> list[dict]:
        """对一批风险项统一做法规检索与引用校验。

        Args:
            risk_items: ReviewResult 的 risks 列表（每项含 clause_number/description）。
            top_k: 每个风险检索的 Top-K。

        Returns:
            risks 原列表的回写版本：每项增加
              "legal_references": [{"ref_type", "ref_name", "ref_article", "ref_content",
                                     "verified", "needs_human_check"}]
            空库/无命中 → 引用为空或全 verified=False。
        """
        for risk in risk_items or []:
            query = (risk.get("description") or risk.get("clause_number") or "").strip()
            refs = list(risk.get("legal_references") or [])
            if not query or not refs:
                continue
            hits = self.find_regulations(query, top_k=top_k)
            pool = self.build_candidate_pool(hits)
            risk["legal_references"] = self.verify_risk_references(refs, pool)
        return risk_items
