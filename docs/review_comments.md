# 审查结果问题清单

## 一、问题清单（按严重性排序）

### 问题 1（严重・正确性/回归）：`create_review` 文档归属校验写错查询对象，审查创建功能必然失败 — **本轮新引入**
**位置**：`app/compliance/api/reviews.py::_assert_document_access`（第 39-45 行）

```python
doc = db.query(ComplianceDocument).filter(ComplianceDocument.id == document_id).first()
...
if not user.is_superuser and doc.uploaded_by != user.id:
```

**问题（两处叠加）**：
1. **查询对象错误**：`req.document_id` 是**业务表 `documents.id`**（`service.create_review` 第 61 行用 `Document.id == document_id` 校验存在，前端也从 `/api/documents` 拿 id），而这里查的是 **`compliance_documents.id`**（审查时才生成的 uuid 主键，用户不可能知道）→ 必然查不到 → **所有 `POST /reviews` 返回 404 "document not found"**，无论是否超管。
2. **字段不存在**：`ComplianceDocument` 模型（`models/document.py`）**没有 `uploaded_by` 字段**（字段仅 id/document_id/doc_type/status 等）→ 即使第 1 点修好，非超管请求也会 `AttributeError` → 500。

**影响**：整个合规审查入口被打断，前端"发起审查"按钮永远报错；同时越权校验实际**没有生效**（没校验到业务表归属）。

**修复建议**：
```python
def _assert_document_access(db, document_id, user):
    from app.models.document import Document
    doc = db.query(Document).filter(Document.id == document_id).first()
    if doc is None:
        raise HTTPException(404, "document not found")
    if not user.is_superuser and (doc.uploaded_by != user.id and doc.visibility != "shared"):
        raise HTTPException(403, "forbidden: not your document")
    return doc
```

### 问题 2（重要・正确性）：法规检索 + 引用校验仍未接入审查链路 — **未修复**
**位置**：`app/compliance/harness/runtime.py:73`（`self._rag_skill = RagSkill()` 仍仅初始化，无调用点）；`workflows/review_graph.py`（节点列表仍无 research）；`api/knowledge.py::seed_regulations / ingest_regulation`（inline articles 路径仍走 `_ingest_articles_json` 只写 DB，**不向量化**）

**本轮进展**：`ingest_from_file`（file_path 录入）会向量化 ✅；`schemas/regulation.py` 加了 file_path 目录 jail + 扩展名 + symlink 校验 ✅。
**仍未解决**：种子初始化（`POST /seed`）与前端手动录入（articles 数组）**仍不写向量库** → `compliance_regulations` collection 恒空 → `/knowledge/search` 空、审查引用校验空库降级。**引用防幻觉特性依然未生效**。

**建议**：inline/seed 路径也调用 `knowledge_ingestion.ingest_regulation`（幂等+向量化）；图中新增 research 节点调 `ResearcherAgent`。

### 问题 3（重要・正确性）：Word 报告字段错位依旧 — **未修复**
**位置**：`app/compliance/reporting/exporters/word_exporter.py::export_word`（未改动，仍读 `review_data["review_id"]/["doc_type"]/["high_risk_count"]/["medium_risk_count"]/["low_risk_count"]` 顶层键）；`reporting/generator.py::generate_reports_for_review`（第 263 行仍直接 `export_word(report_data, ...)`）

**证据**：`report_data`（ReporterAgent 结构）顶层**没有** `review_id/doc_type/high_risk_count` 等键（这些在 `doc_info/risk_counts` 内，且 review_id 是函数参数）。结果：
- 文件名 `compliance_report_unknown_<时间>.docx`；
- 封面合同类型"—"、完成时间取当前时间；
- 风险统计恒 0 → 摘要恒"**本次审查未检出风险条款，合同合规**"——**高风险合同拿到"合规"的 Word 报告**。

**建议**：`export_word` 前做适配层（从 `doc_info/risk_counts` 取字段、注入 review_id），并补单测断言 Word 文本。

### 问题 4（重要・流程）：HITL 仍不阻塞，高风险未经人工确认即出报告 — **未修复**
**位置**：`app/compliance/harness/runtime.py::human_review`（第 477-491 行，注释仍"MVP 简化，不真正 interrupt"）；`review_graph.py:61`（`human_review → generate_report` 无条件直连）

**本轮进展**：risks 已落库 → `human_action` 能查到风险了 ✅；`human_action` 加了 `review_id` 过滤（不能跨 review 操作风险）✅。
**仍未解决**：`should_retry` 返回 `"human"` 后节点只记录 `pending_human_review` 列表即放行，**报告照常生成**。

**建议**：`compliance_hitl_enabled=true` 且有 high 时 `interrupt()`，人工确认后 `Command(resume=...)` 恢复（`hitl.py::build_resume_command` 已备）。

### 问题 5（重要・架构）：审查长任务仍跑 BackgroundTasks，无队列/恢复 — **未修复**
**位置**：`app/compliance/api/reviews.py:72`（`background_tasks.add_task(service.run_review, payload)`）

**建议**：接 Celery；DB 状态 + PostgresSaver（checkpointer 已备）支持断点恢复。

### 问题 6（重要・正确性）：COMPLIANCE_ENABLED 开关仍失效 — **未修复**
**位置**：`app/api/router.py`（不在本轮改动列表，仍无条件 include compliance 路由）；`app/models/__init__.py`（仅模型注册门控）

**建议**：`if settings.compliance_enabled:` 门控路由与前端页面。

### 问题 7（重要・正确性）：法规删除仍不清理向量库 — **未修复**
**位置**：`app/compliance/api/knowledge.py::delete_regulation`（仍只删 DB 行）

**建议**：删除前调 `delete_regulations_from_store(regulation_id)`。

### 问题 8（重要・契约）：前端合同类型选择仍未生效 — **未修复**
**位置**：`app/streamlit_app/views/compliance.py::_start_review`（第 39-42 行仍 `payload["doc_type"] = doc_type`）；后端 `ReviewCreateRequest` 字段为 `contract_type_override`

**本轮进展**：前端仅适配了分页解包（`data.get("items")`）。合同类型覆盖仍传不进去 → 永远用默认 `labor_contract`。

**建议**：改为 `payload["contract_type_override"] = doc_type`。

### 问题 9（重要・可靠性）：SSE 异常时 assistant 消息仍不落库 — **未修复**
**位置**：`app/api/conversations.py:306-318`（落库仍只挂在 `sources` 事件的 `background_tasks.add_task`；`except` 分支第 319-332 行仅补发 partial + error 事件，不同步写库）

**影响**：改写/检索/生成中途异常或客户端断连 → 历史只剩 user 消息，多轮上下文残缺（上轮问题 16，非 compliance 但属 RAG 主链路）。

**建议**：except 分支内**同步** `add_message(..., status="error")`。

### 问题 10（一般・正确性）：`_load_active_rules` 兜底跨类型拉全量规则 — **未修复**
**位置**：`services/review_service.py:145-150`

**建议**：无匹配规则时显式返回空并提示运营配置。

### 问题 11（一般・流程）：`reflect` 的"无风险即低质量"误伤合法合同 — **未修复**
**位置**：`harness/runtime.py::reflect`（`clauses and not risks → coverage=0.4`）

**建议**：无风险时 coverage 置 1.0，回炉条件聚焦风险质量。

### 问题 12（一般・安全）：内置法规种子仍为"占位条文" — **未修复**
**位置**：`app/compliance/knowledge/seed_data/labor_contract/*.json`（内容含【占位示例条文】）

**建议**：标注演示占位；上线前导入真实法条。

### 问题 13（一般・质量）：测试仍缺失 — **未修复**
**位置**：`tests/`（仅空 `__init__.py`）

**本轮佐证**：`_assert_document_access` 这类"必然 404/500"的回归，若有 1 条端到端测试即可拦截。建议补：create_review 归属校验（403/404/200）、_persist_results 落库断言、export_word 字段断言、render_html 转义断言。

---
