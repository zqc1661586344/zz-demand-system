# 代码评审报告


### 问题 1（重要・正确性）：hybrid 稀疏命中路径失去绝对相关性阈值，门控实际失效

- **位置**：`app/rag/retrievers.py` → `hybrid_search()`（`# 2. 稀疏有命中 → 无需 spread…` 分支末尾）
- **分析**：本轮把门控从 `if not scored or scored[0][1] < settings.rag_min_score` 放宽为 `if not scored`，注释理由是 "避免误杀关键词命中"。但 `similarity_search_with_score`（`vector_store.py::similarity_search_with_relevance`）**只要向量库非空就必然返回 ≥1 条**（取最近邻，无阈值），所以 `not scored` 只在 "向量库为空 /embedding 失败" 时才成立。结论：**只要用户有任何可见文档，稀疏一有关键词命中，就无条件进 RAG 并带 sources，不再做任何分数把关**。
- **后果**：① 与 `mmr`/`similarity` 路径不一致（那两个仍用 `rag_min_score=0.4` 阈值）；② 关键词 AND 命中但语义无关的 query（词碰巧共现）会被强行判为 "命中"，来源引用指向无关片段；③ 整个 hybrid 模式的 free-chat 回退完全取决于 "稀疏是否空"，自由聊天回退机制在默认路径上被架空。
- **建议**（任选其一，推荐前两个）：
  1. `sparse_search.search` 的 SQL 已算出 `ts_rank r`，把它写入 Document metadata（如 `meta["sparse_score"]=r`），在 `hybrid_search` 里对稀疏命中设一个 `r` 的下限（如 `r > 0.1`）作为稀疏侧把关；
  2. 对融合后 docs 的 top-1 做 cosine 佐证，但阈值放宽到比 `rag_min_score` 更宽松（如 0.3），并保留 `not scored → free chat`；

### 问题 3（中等・正确性 / 一致性）：`sanitize_citations` 只在非流式路径生效，前端实际用的流式路径未清理

- **位置**：`app/rag/chain.py` → `query_rag()` 第 281 行调用了 `sanitize_citations`；而 `query_rag_stream()`（第 290-323 行）拼完 `full_answer` 后直接 `yield {"type":"sources","data":...,"full_answer":full_answer}`，**未调用**。
- **分析**：前端（`streamlit_app/views/chat.py`）走的是 `query/stream` 接口，所以越界 `[来源 N]` 的清理实际没生效。且即便在流式末尾清理，前端已把未清理的 token 逐个渲染过了，`full_answer` 与屏幕显示会不一致。
- **建议**：二选一 ——① 接受流式不清理，但把清理放到 `conversations.py::_save_messages_background` 落库前（保证存库与前端展示一致即可，前端渲染已定型）；② 若想根治，需在流式生成前对引用做约束（在 RAG_PROMPT 里限定 "仅引用 [Source N] 且 N≤实际来源数"）并让前端按最终 `sources` 渲染。另注意 `_CITATION_RE` 只匹配 `[来源 N]`，而 `format_context` 给模型的是 `[Source N: …]` 格式，模型若按后者引用则清理不到 —— 两种格式建议都覆盖。

### 问题 4（中等・正确性）：`_rewrite_query` 在 `summary=None` 时仍注入 `"[更早对话摘要（仅参考）]\nNone"`

- **位置**：`app/rag/chain.py` → `_rewrite_query()`（`messages` 构造处）
- **分析**：短对话（消息 ≤40 条）时 `_build_history` 返回 `summary=None`，但代码无条件追加 `("system", f"[更早对话摘要（仅参考）]\n{summary}")`，prompt 里会出现字面量 `None`，浪费 token 且可能干扰改写。
- **建议**：加守卫，`messages = [("system", CONTEXTUALIZE_Q_SYSTEM)]`，`if summary: messages.append(("system", ...))`。

### 问题 5（中等・性能 / 成本）：自包含判定只覆盖 "含疑问词" 的问题，无疑问词的命令 / 陈述句仍每轮无条件改写

- **位置**：`app/rag/chain.py` → `_is_self_contained()` / `_rewrite_query()`
- **分析**：`_is_self_contained` 要求 `any(疑问词)` 才可能返回 True，于是 "介绍一下 bge-m3 的维度"" 列出所有部门 " 这类自包含命令句会**每次都触发一次额外 LLM 改写**（且 `_rewrite_query` 里 `get_llm()` 每次新建实例，未走 `lru_cache`）。同时 `_REFERENTIAL_MARKERS` 里单字 "那" 过于敏感（"那我们的产品怎么装？" 会被误判需改写），`_QUESTION_WORDS` 里 "哪 / 哪些" 重复。
- **建议**：自包含判定放宽为 "含实体 / 完整主题 + 无疑问词也可跳过"，或加一个 "无指代词且长度 > 阈值即自包含" 的快速规则；`_rewrite_query` 复用缓存的 LLM 实例；微调 marker 列表（去掉单字 "那"）。

### 问题 6（中等・并发安全）：PGVector 单例实例跨线程共享，线程安全存疑（上轮遗留）

- **位置**：`app/rag/vector_store.py` → `get_vector_store()`（`@lru_cache` 返回单例，`connection=settings.vector_store_url`）
- **分析**：langchain-postgres 的 `PGVector` 内部管理连接，`lru_cache` 使单实例被 FastAPI 线程池所有请求共享；多用户并发查询 / 上传写入可能命中连接状态竞争（查询虽有 10/min 限流，但上传管道与多 worker 并发仍可能触发）。
- **建议**：改用 `PGVector(connection=<sqlalchemy engine>)` 走连接池，或去掉 `lru_cache` 改为按请求实例化 / 加锁串行化写路径；补一个并发读写冒烟测试。


### 问题 8（中等・一致性 / 性能）：共享文档变更级联失效所有用户索引 + 每 worker 独立内存副本（上轮遗留，tsvector 下缓解）

- **位置**：`app/rag/retrievers.py` → `mark_bm25_data_changed()`（`shared=True` 时 `keys.update(_bm25_map.keys())`）；`get_bm25_for_user()`（每 key 对比 `__all__` 的 TS）
- **分析**：默认 tsvector 后端下内存 BM25 不参与查询，该问题休眠；但 `bm25_memory`/SQLite 下，任意私有文档变更会 bump `__all__` TS 并连带让所有用户下次查询全量重建，且 `web_concurrency=4` 时同一用户索引每进程一份。
- **建议**：私有变更只失效 owner 与 `__all__`；共享语料占比高时把 BM25 拆成 "个人私有 + 全局共享" 两份索引，避免逐用户复制共享语料。

### 问题 9（低・一致性）：多存储写入非原子，缺对账机制（上轮遗留）

- **位置**：`app/rag/pipeline.py` → `process_document()`（chunk 先 commit → 写 PGVector → 置 indexed）；`app/services/document_service.py` → `delete_document()`
- **分析**：任一步崩溃都产生中间态（孤儿 chunk / 孤儿向量 / DB 与磁盘不一致），重试能自愈一部分，但 "最终失败 + 崩溃后无人重试" 无兜底。
- **建议**：补周期对账任务（按 `document_id` 比对 `document_chunks` 与 `langchain_pg_embedding` 计数），或改成 "向量写成功才置 indexed、失败即清理 chunk"。

### 问题 10（低・正确性）：Celery 任务在非重试失败时误报 `indexed`（上轮遗留）

- **位置**：`app/rag/tasks.py` → `process_document_task()`；`app/rag/pipeline.py` → `process_document()`（`FileNotFoundError/ValueError` 被内部吞掉置 failed 后正常返回）
- **分析**：任务返回 `{"status":"indexed"}` 与 DB 实际 `failed` 矛盾，监控 / 告警失真。
- **建议**：`process_document` 返回状态值，或 `process_document_task` 调用后回查一次 DB 状态作为返回值。

### 问题 11（低・健壮性）：`query`/`top_k` 无边界校验；上传文件整体读入内存（上轮遗留）

- **位置**：`app/schemas/conversation.py` → `QueryRequest`；`app/api/documents.py` → `upload_document()`
- **建议**：`query` 加 `min_length/max_length`，`top_k` 加 `ge=1, le=20`；上传改流式落盘或降低单文件上限。

### 问题 12（低・调优）：`plainto_tsquery` AND 语义 + 'simple' 配置无停用词，长 query 稀疏召回偏低

- **位置**：`app/rag/sparse_search.py` → `search()` / `tokenize_query()`
- **分析**："产品的核心功能有哪些" 被切成 6 个词（含 "的 / 有 / 哪些"），`plainto_tsquery` 要求**全部**出现在同一 chunk 才 `@@` 命中，长句召回偏紧；空 / 纯标点 query 已安全返回空（已验证）。
- **建议**：`tokenize_query` 里过滤 "的 / 了 / 有 / 哪些" 等泛词或只保留前 N 个高频词；可考虑用 `websearch_to_tsquery` 的 OR / 短语语法做可选词匹配（注意对 token 加引号防操作符注入）。

### 问题 13（低・架构）：LangChain 1.3 与 `langchain_classic` / `langchain_community` 混用（上轮遗留）

- **位置**：`app/rag/retrievers.py`（`BM25Retriever`/`HuggingFaceCrossEncoder` 来自 community，`CrossEncoderReranker` 来自 classic）
- **建议**：固定版本、CI 锁 `uv.lock`，为 hybrid/rerank 两条路径补集成测试（`tests/test_rag/` 目前为空）。
