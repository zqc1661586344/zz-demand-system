# 合规审查模块复评报告（第四轮）

## 一、问题清单（按严重性排序）

### 问题 1（严重・安全/回归）：`create_review` 文档归属校验被整体删除，越权审查他人私有文档回归
**位置**：`app/compliance/api/reviews.py`（删除 `_assert_document_access` 及其调用）；`app/compliance/services/review_service.py::create_review`（第 59-70 行仅校验文档存在 + indexed）

**证据**：`grep -n "uploaded_by\|visibility\|_assert_document" app/compliance/services/review_service.py app/compliance/api/reviews.py` 无任何输出。

**问题**：上一轮修复 404 回归的方式是**删掉整个校验**，而不是把查询对象改对。当前状态：任何登录用户传入任意 `document_id`（含他人私有文档）即可发起审查 → 系统读取对方私有文件、生成含合同关键信息的报告 → **IDOR 数据泄露与第一轮评审时完全一样**。

**修复建议**（在 `create_review` 入口或 `service.create_review` 内）：
```python
from app.models.document import Document
biz_doc = db.query(Document).filter(Document.id == document_id).first()
if biz_doc is None:
    raise HTTPException(404, "document not found")
if not current_user.is_superuser and (
    biz_doc.uploaded_by != current_user.id and biz_doc.visibility != "shared"
):
    raise HTTPException(403, "forbidden: not your document")
```
（注意 shared 文档应允许他人审查——这是共享语义；放 API 层或 service 层均可，建议 service 层并配单测。）

### 问题 2（重要・正确性）：法规库种子/手动录入仍不向量化，引用校验实际是"空库降级"
**位置**：`app/compliance/api/knowledge.py::seed_regulations / ingest_regulation`（inline `articles` 路径仍走 `_ingest_articles_json`，只写 DB 行，不调 `add_regulations_to_store`）

**问题**：`_enrich_references_with_rag` 已接入审查链路，但 `compliance_regulations` collection 仍恒空 → `RagSkill` 检索零命中 → `citation_verifier` 对**所有**引用标记 `verified=False + 需人工核实`。防幻觉机制"有门无库"。

**建议**：`seed_regulations` 与 inline 录入路径调用 `knowledge_ingestion.ingest_regulation`（幂等+向量化）；或复用 `ingest_from_file` 的分块→嵌入逻辑，把 `articles` 数组也向量化。

### 问题 3（重要・正确性）：HITL resume 依赖进程内 checkpointer，重启后无法恢复
**位置**：`app/compliance/harness/runtime.py::resume_review`（`self.checkpointer.get_tuple(config)`）；`harness/checkpointer.py`（InMemorySaver 回退）

**问题**：流程正确（终态等待 + resume），但默认/无 PG 时用 InMemorySaver，**进程重启即丢失 state** → resume 返回 "state not found ... please re-initiate review"。对"人工确认"这种可能隔天发生的操作，恢复能力是关键。代码已做错误提示（不会崩），但业务上审查会作废重来。

**建议**：生产环境强制 PostgresSaver（checkpointer 已支持三级回退，确认 `VECTOR_STORE_URL`/PG 配置在部署时必填）；把 checkpointer 类型写入 review 行便于诊断。

### 问题 4（重要・正确性）：法规引用校验只"校验已有引用"，不"主动检索补充依据"
**位置**：`app/compliance/harness/runtime.py::_enrich_references_with_rag`（第 375-376 行：`if not refs or not query: continue`）

**问题**：仅对 LLM/Playbook **已经产出** `legal_references` 的风险做校验；风险项若没有引用，则不会去法规库检索补充。而 openai 模式 reviewer 的 `regulation_hits` 仍为空（reviewer_prompt 的"法规引用候选"从未被注入）→ 大多数风险的 refs 为空 → RAG 校验形同"空转"。同时 `ResearcherAgent` 依然未被使用（定义存在、能力闲置）。

**建议**：对无引用的风险，用 `risk["description"]` 主动检索 top-k 法规作为候选注入并校验（RagSkill 已支持 `references` 为空时仅检索返回 hits）；或正式把 `ResearcherAgent` 接入为独立节点。

### 问题 5（重要・可靠性）：SSE 异常落库仍走 BackgroundTasks，客户端断连场景依旧丢失
**位置**：`app/api/conversations.py:329-340`（except 分支 `background_tasks.add_task(_save_messages_background, ...)`）

**问题**：服务端异常时能落库了（比上轮好），但客户端断连（生成器被 close）时 BackgroundTasks 仍不执行 → 对话历史残缺问题在断连场景依旧存在。

**建议**：except 分支内**同步**落库（独立 SessionLocal + add_message + commit），不依赖 BackgroundTasks 执行时机。

### 问题 6（一般・质量）：测试为"影子实现"，且未覆盖关键安全/落库路径
**位置**：`tests/compliance/test_core_logic.py::TestReflectQualityScoring._compute_quality`（第 27-44 行，在测试里**复制**了 reflect 的公式而非 import 真实逻辑）

**问题**：若 `runtime.py::reflect` 后续改动公式，测试**不会失败**（影子实现），无法守护真实行为。且无 create_review 权限、_persist_results 落库、export_word 字段对齐的测试——本轮 P0 安全回归正是因为没有这类测试而漏网。

**建议**：`_compute_quality` 改为 import `ComplianceHarness.reflect` 或抽出纯函数供两端共用；补 3 条关键测试：create_review 归属（403/404/200+shared）、_persist_results 后 get_review 数据回填、export_word 产物文本断言。

### 问题 7（一般・正确性）：`human_action` 的 action 白名单仍在循环内抛异常
**位置**：`app/compliance/services/review_service.py::human_action`（第 348-350 行）

**问题**：未知 action 在 risk 循环内 `raise ValueError`，若前面已有部分 risk 处理则残留未 commit 的变更（虽有 `db.commit()` 在循环后，异常直接跳出 → FastAPI 依赖的 get_db 关闭时回滚，行为正确但依赖隐式回滚）。

**建议**：action 白名单在循环外校验。

### 问题 8（一般・产品）：种子法规仍为占位条文，且无真实法条来源
**位置**：`app/compliance/knowledge/seed_data/labor_contract/*.json`

**建议**：标注"演示占位"；上线前法务导入真实法规全文（与问题 2 一并解决，向量化 + 真数据一次到位）。

### 问题 9（一般・体验）：前端页面无角色控制，viewer 用户可见 admin 操作入口
**位置**：`app/streamlit_app/views/playbooks.py / knowledge.py`（页面无 `require_roles` 判断，操作时后端 403）

**建议**：前端按当前用户角色隐藏"新建规则/录入法规/删除"入口（`app/streamlit_app/auth.py` 已有 roles 信息）。

---

## 三、已确认修复项（本轮验收通过）

| 上轮问题 | 修复情况 | 证据 |
|---------|---------|------|
| P0 create_review 404 回归 | ⚠️ 功能恢复（删除校验），**但引入安全回归**（问题 1） | `api/reviews.py` 移除 `_assert_document_access` |
| 法规引用校验未接入链路 | ✅ **已接入** | `runtime.py::_enrich_references_with_rag` 在 review_clauses 内对 risks 引用做 RAG 检索+校验，空库/异常降级不阻断 |
| HITL 不阻塞 | ✅ **真落地** | `review_graph.py` human_review→END（终态）；`POST /{review_id}/resume` + `runtime.resume_review` 从 checkpointer 恢复 state 后 generate_report |
| 审查长任务无队列 | ✅ **Celery 可配置** | `compliance/tasks.py`（run/resume 双任务，autoretry_for 网络/OpenAI 错误，acks_late + reject_on_worker_lost）；`create_review` 按 `use_celery_task` 分流 |
| COMPLIANCE_ENABLED 开关失效 | ✅ **已修复** | `router.py` + `streamlit_app/app.py` 双端门控 |
| Word 报告字段错位 | ✅ **已修复** | `word_exporter.py` 改读 `doc_info.doc_type`/`risk_counts`；`generator.py` 注入 `review_id`/`completed_at` |
| 删法规不清理向量 | ✅ **已修复** | `knowledge.py::delete_regulation` 调 `delete_regulations_from_store`（失败仅记日志） |
| 前端合同类型不生效 | ✅ **已修复** | `views/compliance.py::_start_review` 改传 `contract_type_override` |
| SSE 异常不落库 | ⚠️ **部分修复** | except 分支注册 BackgroundTasks 落 partial/失败文案（服务端异常场景 OK，断连仍丢） |
| 规则跨类型兜底 | ✅ **已修复** | `review_service.py` 改为 warning + LLM-only 模式 |
| reflect 无风险误伤 | ✅ **已修复** | `runtime.py::reflect`：无风险有条款时 coverage=1.0 / avg_conf=0.9 |
| 测试缺失 | ⚠️ **开始补齐** | `tests/compliance/test_core_logic.py`（reflect 评分 + citation verifier 纯逻辑） |

---
