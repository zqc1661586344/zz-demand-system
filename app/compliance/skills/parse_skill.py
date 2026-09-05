"""条款解析 Skill（app/compliance/skills/parse_skill.py）——能力层统一封装。

职责（设计文档 F01）：加载文档 → 条款拆分 → 合同类型判定 → 关键信息骨架。
复用 `app/rag/pipeline.load_document`（PDF/TXT/MD/DOCX 等）与 `parsing.clause_splitter`。
所有模式确定性执行（不依赖 LLM）；关键信息(key_info)完整提取由 extractor 节点负责，
本 Skill 只产出骨架（空 dict）。

ctx 输入键（由 review_graph 的 parse 节点注入）：
    document_id: 业务表 documents.id
    file_path:   磁盘文件绝对路径
    mime_type:   文件 MIME 类型
    llm:         (可选) 审查 LLM（MVP 不使用，保留钩子）

返回：{"ok": True, "data": {"raw_text", "doc_type", "doc_confidence", "clauses", "key_info"}}
    或 {"ok": False, "error", "data": None}。
"""

from app.compliance.agents.base import get_llm_for_compliance
from app.compliance.parsing.clause_splitter import split_clauses_from_text
from app.compliance.parsing.parser import classify_doc_type, load_text
from app.compliance.skills.base import SkillBase


class ParseSkill(SkillBase):
    name = "parse"

    def execute(self, ctx: dict) -> dict:
        document_id = ctx.get("document_id")
        file_path = ctx.get("file_path")
        mime_type = ctx.get("mime_type")
        if not document_id or not file_path or not mime_type:
            return self.err("parse skill: 缺 document_id/file_path/mime_type")

        try:
            full_text = load_text(file_path, mime_type)
            doc_type, confidence = classify_doc_type(full_text)
            clauses = split_clauses_from_text(full_text)
            # 补页号缺省（PDF 的 page 由 load_document 元数据带，MVP 简化不逐条映射）
            for idx, c in enumerate(clauses):
                c["page_number"] = c.get("page_number")
            self.log(
                f"parsed doc={document_id} type={doc_type} "
                f"clauses={len(clauses)} chars={len(full_text)}"
            )
            return {
                "ok": True,
                "data": {
                    "document_id": document_id,
                    "raw_text": full_text,
                    "doc_type": doc_type,
                    "doc_confidence": confidence,
                    "clauses": clauses,
                    "key_info": {},
                },
            }
        except Exception as e:  # noqa: BLE001 — 解析失败统一降级返回
            self.log(f"parse failed: {e}")
            return self.err(f"parse skill 失败：{e}")