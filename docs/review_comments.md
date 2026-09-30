## 一、问题清单（按严重性排序）

### 问题 1（重要・性能）：Multi-Query 串行执行，延迟放大 n 倍

**位置**：`app/rag/retrievers.py::multi_query_search` L591

```
ranked_lists = [hybrid_search(q, top_k=per_query_k, user_id=user_id) for q in variants]
```

**机理**：列表推导式**串行**执行 n 路检索，每路含 2 次 PGVector 查询 + 1 次稀疏 SQL + 1 次 embedding 调用。默认 n=3，单路 P50 若 200ms，则总延迟 ≈ 600ms+，流式问答的 TTFB 放大 3 倍；`query_rag_stream` 的 SSE 首 token 明显变慢。

**修复**（3 行）：`ThreadPoolExecutor(max_workers=min(n, 4))` 并行执行，合并结果按原顺序。注意 embedding 调用是否线程安全（`get_embedding_model()` 的 provider 客户端需确认；OpenAI SDK 线程安全，若用本地 Ollama 需加锁或复用连接）。

### 问题 2（重要・正确性）：GoldenDataset/EvalRun 两张新表无 alembic 迁移

**位置**：`app/models/eval.py`；`alembic/versions/`（无对应迁移文件）；`app/database.py:53` `Base.metadata.create_all(bind=engine)`

**机理**：eval 表只靠启动时 `create_all` 兜底。开发环境 OK，但：① 生产升级路径与 alembic 体系割裂（其余表走迁移，这两张表静默 create_all，无版本记录、无降级脚本）；② **`create_all` 对已存在表不会加新列**——后续给 `EvalRun` 加字段（如 `model_version`、`cost`）时会静默失败且无报错。这正是历史上 `human_actions.note` 四轮无迁移问题的同款模式。

**修复**：补一个迁移 `add_eval_tables`（与 c3d4e5f6g7h8 风格一致，幂等 `CREATE TABLE IF NOT EXISTS`），并考虑把 `create_all` 收敛为纯开发环境行为（`settings.env == "dev"`）。

### 问题 3（一般・正确性）：`POST /api/eval/run` 的 scene 参数无枚举校验

**位置**：`app/api/eval.py::EvalRunRequest.scene`（`str = "rag"`，描述 "rag|compliance|all" 但未约束）

**机理**：任意字符串透传给 `scripts/eval_ragas.py run <scene>`，脏值（如 "rrag"）报错但不致命（无 shell、无注入）。但 API 层应有第一道防线。

**修复**：`scene: Literal["rag", "compliance", "all"] = "rag"`。顺带：`_run_eval_in_subprocess` 的 `timeout=1800`（30 分钟）建议改配置项，且 subprocess 失败时应有结构化反馈给前端（现在只有日志）。

### 问题 4（一般・成本）：Multi-Query 全量开启，无难度路由——每次查询固定 +1 次 LLM 调用 + n 路 embedding

**位置**：`config.py::rag_multi_query_enabled=True`（全局开关，无条件）

**机理**：`_expand_query_variants` 每次查询都调用一次 LLM 扩展视角 + n 路 embedding 检索。对简单问题（"劳动合同期限是几年"）是纯浪费——业界 Adaptive RAG 的做法是**先路由再决策**：简单查询单路、复杂查询才多路。你 `docs/rag_todo.md` 的 P2 #7 Query Routing 已规划，建议把 multi-query 从"全局开关"改为"route 条件分支"（中等复杂及以上才启用），这是从"固定流水线"迈向"自适应 Agentic RAG"的关键一步，也是成本优化的直接落点。

**修复**：`route` 节点（规则版可先行：查询长度/指代词/疑问词组合判断，0 LLM 成本）→ `simple → hybrid_search` / `complex → multi_query_search`。

### 问题 5（提示・可观测性）：multi-query 扩展调用未打 LangSmith tags

**位置**：`retrievers.py::_expand_query_variants` L520 `chain.invoke({"n": n, "query": query})`（未传 `config={"tags": ["multi_query_expand"]}`）

**机理**：其余 LLM 调用全部有 tags（rag/free_chat/query_rewrite/summarize），唯独视角扩展调用是观测盲区——**无法在 LangSmith 里单独看"每路扩展的质量与耗时"**，也就无法评估"多路召回 vs 单路"的收益是否值回成本。

**修复**：补 `config={"tags": ["multi_query_expand"]}`。

### 问题 6（提示・参数）：`rag_multi_query_primary_weight=0.5` 对原问题权重偏低

**位置**：`config.py`（`primary_weight: float = 0.5`）

**机理**：原问题只占 0.5 权重，两个改写视角各 0.25。改写视角由 LLM 生成，质量波动大——若视角偏离原意，会稀释原问题高精度结果的排名。业界 Multi-Query（如 LangChain 官方 pattern）通常不降权首路或首路权重 0.6-0.7。

**修复建议**：默认 0.6 起步，并用你现成的 RAGAS 通道跑一组 `primary_weight ∈ {0.5, 0.6, 0.7}` 的 A/B（context_recall/precision 对比），把结论写进 docs/RAG.md——这正好符合你已经建立的数据驱动决策模式。
