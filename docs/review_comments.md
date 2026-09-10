
# 合规审查 + RAG 模块代码评审 · 第八轮


## 一、问题清单（按严重性排序）


### 问题 1（严重・正确性/上线阻断）：`compliance_human_actions.note` 迁移缺失——第四轮指出，连续四轮未修
**位置**：`alembic/versions/b2d3c4e5f6a7_add_review_id_fk_fixes.py`（无 note 列）；`app/compliance/models/report.py:47`（模型有 `note`）；`review_service.py::human_action` 写 note

**验证**：本轮 `git diff -- alembic/` 为空；两个迁移文件均无 note 列。**存量库人工审核写 note 必崩 `Unknown column`**。

**修复**（3 行，已第三次给出）：
```python
op.execute(sa.text("ALTER TABLE compliance_human_actions ADD COLUMN IF NOT EXISTS note TEXT"))
```
**根治建议**：接入 `alembic check`（模型 vs 迁移 diff 校验）进 CI——本次问题本质是"模型加列→忘迁移"反复发生，机制缺失。

### 问题 2（重要・正确性）：RAG 生成幻觉仍是当前最大短板——faithfulness 平均仅 0.61~0.66
**位置**：`scripts/eval_results_labor_rag.csv` / `eval_results_labor_k10_rag.csv`（实测数据）

**证据**（我直接统计了结果 CSV）：
| 指标 | top_k=5 | top_k=10 |
|---|---|---|
| faithfulness | 0.606 | 0.663 |
| answer_relevancy | 0.762 | 0.797 |
| context_recall | 0.698 | 0.743 |
| context_precision | 0.659 | 0.607 |

**问题**：忠实度低于 0.7，意味着约 1/3 的答案陈述无法被检索上下文支撑——**幻觉是当前系统最大的质量问题**；而 top_k=10 提升 recall 但 precision 下降（0.607），说明**只调 top_k 是拿精度换召回**，根因可能在于：(1) LLM 未被强制约束到上下文（`sanitize_citations` 只删引用不改内容）；(2) 检索到不相关内容混入（precision 低 → 生成器被带偏）。**这是 RAGAS 落地后的核心价值——现在有客观数字了，下一步优化有了靶子。**

**建议**：优先优化 faithfulness：(1) prompt 增加"只依据给定材料回答，材料没有的明确说不知道"；(2) 提高 `rag_rerank_top_n` 相关度过滤（rerank 已默认开）；(3) 把这两组结果作为回归基线，每周重跑对比。

### 问题 3（重要・正确性）：`_rag_hits_to_references` 键名 bug——连续五轮未修
**位置**：`app/compliance/harness/runtime.py:602`（`h.get("title")`，检索 hit 键名是 `regulation_title`）

**问题**：主动补充的法规引用在报告里法规名显示为 UUID。一行修复：`h.get("regulation_title") or h.get("title") or ""`。**这个 bug 已经在合规报告里持续存在五轮**，直接影响法务阅读，请本轮务必修。

### 问题 4（一般・正确性）：`RemoteAPIRerank.score()` 无重试/退避、无响应 schema 校验 → 生产环境下 rerank API 瞬时故障直接降级

- **位置**：`ragrerankers.py` `RemoteAPIRerank.score()` L42–58
- **机理**：远端 Rerank API 调用只有 `timeout=self.timeout`（默认 10s），**无任何重试机制**。硅基流动等国内 API 在网络抖动、限流、服务不稳定时返回 429/500/502 的概率不低，`resp.raise_for_status()` 直接抛异常 → `_maybe_rerank` 的 broad except 捕获 → 回退无 rerank 的原始排序。功能上不会崩溃，但：
  1. 一次瞬时失败就永久降级到无 rerank（`_built_reranker` 被设为 `False`，**后续所有请求都跳过 rerank**，直到进程重启）；
  2. 响应 JSON 无 schema 校验——如果 API 返回非标准格式（如 `{"error": "..."}` 而非 `{"results": [...]}`），`data.get("results", [])` 返回空列表，scores 全 0.0，**rerank 把所有文档排到末尾**而非降级。
- **修复方向**：
  1. `score()` 内加 `httpx` 重试（`transport=httpx.HTTPTransport(retries=2)` 或手动 try+sleep 一次）；
  2. `_built_reranker = False` 应改为**临时降级**（如设一个 `_reranker_fail_ts = time.time()`，5 分钟后重试），而非永久性标记；
  3. 响应校验：`if not data.get("results"): logger.warning(...); return [0.0] * len(documents)` 保持原序。
- **评分影响**：Reranker 6.5/10。

### 问题 5（一般・正确性）：BM25 检索数量绑定 `rag_rerank_top_n` → rerank 关闭时 BM25 只取 5 条但 RRF 融合期望更多候选

- **位置**：`ragretrievers.py` `_rebuild_bm25_for_key()` L292–294
- **机理**：
  ```python
  k=settings.rag_rerank_top_n if settings.rag_rerank_enabled else 5,
  ```
  当 `rag_rerank_enabled=True` 时，BM25 retriever 的 `k` 被设为 `rag_rerank_top_n`（默认 5）。但 `hybrid_search` 调用 `_sparse_docs(query, top_k, user_id)` 时传入的 `top_k` 是请求级的 top_k（默认也是 5），然后 BM25 的 `get_relevant_documents(query)[:top_k]` 再做截断。**问题在于**：RRF 融合的质量依赖于两路都提供足够多的候选——如果 BM25 只返回 5 条、dense 也只返回 5 条，融合后最多 10 条候选再 rerank 到 5 条。这在 `top_k=5` 时没问题，但如果用户请求 `top_k=10`，BM25 仍只返回 5 条（因为 BM25Retriever 的 `k` 在构建时已固定为 5），**稀疏侧的召回量被截断**。
- **修复方向**：BM25 的 `k` 应设为 `max(settings.rag_rerank_top_n, 10)` 或动态取 `max(top_k, rerank_top_n)`（需要把 k 从构建时移到查询时）。
- **评分影响**：混合检索 7/10。

### 问题 6（一般・正确性）：`sparse_search.py` SQL 中引用 `c.meta` 列，但 `pipeline.py` 写入的列名是 `meta_json`

- **位置**：`ragsparse_search.py` `search()` L128（`c.meta`）vs `ragpipeline.py` L234（`meta_json=json.dumps(...)`）
- **机理**：`sparse_search.py` 的 SQL 查询 `SELECT c.id AS chunk_id, c.content, c.meta, ...`，但 `pipeline.py` 写入 DocumentChunk 时用的字段名是 `meta_json`。如果 ORM 模型中列名确实是 `meta_json`（而非 `meta`），这条 SQL 在 PG 上会报 `column c.meta does not exist`。如果 ORM 定义中有 `Column("meta", ...)` 映射则没问题，但需要确认。
- **影响**：如果列名不匹配，PG tsvector 后端的稀疏检索**每次查询都失败**→回退 BM25 内存后端（`except` 捕获后返回空列表），但日志只记 warning 不报错，用户无感知。
- **修复**：确认 DocumentChunk ORM 的 meta 列名；如果是 `meta_json`，SQL 改为 `c.meta_json AS meta`。

### 问题 7（一般・架构）：`rerankers.py` 有 3 个未使用的 import

- **位置**：`ragrerankers.py` L3–4
  - `from functools import lru_cache` — 未使用（`get_reranker` 用的是手动 global + lock 模式）
  - `from typing import Any` — 未使用
  - `BaseCrossEncoder` 的导入用于 `RemoteAPIRerank` 的基类声明，这个是正确的
- **修复**：删除 `lru_cache` 和 `Any` 两个 import。

### 问题 8（一般・正确性）：`_recover_stuck_reviews` 的 error_message bug 仍未修复（上轮问题 1 未修）

- **位置**：`appmain.py` `_recover_stuck_reviews()`
- **机理**：先 `review.status = "failed"` 再读 `review.status` 拼 error_message → 永远打印 "stuck in 'failed'"。
- **修复**：一行修复——`original_status = review.status` 在覆盖之前保存。

### 问题 9（一般・架构）：`ReviewState` TypedDict 仍未声明 `review_hints` 和 `llm_error_count`（上轮问题 2 未修）

- **位置**：`compliancestate.py` `ReviewState`
- **影响**：功能不受影响（LangGraph `total=False` 模式），但 IDE 无法检测拼写错误、可读性下降。


### 问题 10（提示・架构）：4 个死配置项仍声明但未使用（上轮问题 7 未修）

- **位置**：`appconfig.py`
  - `compliance_citation_similarity_threshold`（L197，citation_verifier 硬编码 0.8/0.5）
  - `compliance_playbook_semantic_threshold`（L195，semantic 引擎未使用）
  - `compliance_hitl_auto_confirm_low`（L203，"低风险自动确认"未实现）
  - `compliance_default_contract_type`（L209，review_service 硬编码 "labor_contract"）

### 问题 11（提示・正确性）：Extractor 伪正则仍存在（上轮问题 8 未修）

- **位置**：`complianceextractor.py`（`"自.*起至"` 走 `kw in haystack` 字面子串匹配）

### 问题 12（提示・安全）：`RemoteAPIRerank` 的 `api_key` 在异常日志中可能泄露

- **位置**：`ragrerankers.py` `get_reranker()` L101
- **机理**：当 siliconflow 配置不完整时，日志打印 `api_url=%r, api_key_set=%s`，这里只打印了 `bool(api_key)` 而非 key 本身——**这点做得好**。但 `RemoteAPIRerank` 的 Pydantic 模型中 `api_key` 是普通字符串字段，如果该对象被意外 `repr()` 或序列化到日志/错误报告，key 会明文泄露。
- **修复**：给 `api_key` 字段加 `Field(repr=False)` 或在 `__repr__` 中遮盖。

### 问题 13（提示・架构）：`compare_template` 函数体仍保留

- **位置**：`complianceruntime.py` `compare_template()`；`should_compare()` 恒返回 `"skip"`
- **建议**：函数体开头加 `raise NotImplementedError`。


### 问题 14（一般・质量）：RAGAS 脚本的 monkey-patch 是脆弱兼容层，需固化版本
**位置**：`scripts/eval_ragas.py::_patch_langchain_community`（注入空 `ChatVertexAI` 模块绕过 ragas 0.2.x 与 langchain-community 0.4.x 冲突）

**问题**：patch 绕过方式在依赖升级后会静默失效（评估结果错误而非报错）。文档已说明，但建议**用 uv 锁定 ragas/langchain 版本组合**（`pyproject.toml` 已加依赖，确认加 `constraint`）并在 eval 入口做版本断言。

### 问题 15（一般・流程）：评测已落地但未接 CI/回归门禁
**位置**：`scripts/eval_ragas.py`（CLI 可跑）+ `docs/ragas_evaluation.md`

**问题**：119 样本、双场景的评测目前是**手工运行**。既然基线数字已产出，下一步自然是接 CI（或至少 cron）：每次检索/生成逻辑改动后自动跑评测，分数回退即失败——否则"有评测"和"每轮改动都跑评测"之间还有距离。

**建议**：CI 步骤：`python scripts/eval_ragas.py rag --metrics faithfulness,answer_relevancy,context_recall,context_precision` + 与基线对比（阈值如 faithfulness < 0.6 即红）。

### 问题 16（一般・工程）：eval 结果 CSV 提交入库，建议只留基线快照
**位置**：`scripts/eval_results_labor_rag.csv`（624 行）+ `eval_results_labor_k10_rag.csv`（663 行）

**问题**：结果 CSV 含 `retrieved_contexts` 字段（119 个问题的完整检索上下文），文件较大且每次重跑都会变。可接受作为"基线快照"（本轮性质），但**不应成为持续提交物**——建议 gitignore 掉 `eval_results_*.csv`，只保留一份基线快照 + 在文档中记录基线数字（已有）。
