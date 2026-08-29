# RAG 模块代码评审报告（第二轮）

## 一、新发现 / 遗留问题（按严重性排序）

### 问题 1（重要・性能回归）：Redis TS 过期后，BM25 每次查询都触发全库重建

- **位置**：`app/rag/retrievers.py` → `get_bm25_for_user()` 的 fail-closed 分支 + `_rebuild_bm25_for_key()`（懒重建不写版本号）。
- **分析**：修复 2c 后，"配置了 Redis 但读不到 TS → 判失效重建"。但 Redis TS 只在 `mark_bm25_data_changed()`（文档变更）时写入，且 `TTL=300s`。一旦距上次文档变更超过 5 分钟，或某用户 key 从未被标记过：
  1. 第一次查询：`redis_ts=None` → pop 缓存 → 全量重建 → 因 `redis_ts is None`，**不会**更新 `_bm25_ts_map[key]`（代码只在 `redis_ts is not None` 时赋值）；
  2. 第二次及后续每次查询：仍是 `redis_ts=None` → 再次 pop → **每次查询都从 DB 全表读取所有 chunks 重建 BM25**。
- 结果是：在多 worker + Redis 生产模式下，只要 5 分钟没有文档变更，所有用户的每次提问都会带来一次全库扫描 + 索引重建，缓存形同虚设。
- **建议**：懒重建时用本地时间戳（`_bm25_ts_map[key] = time.time()`）作为兜底，使"无 Redis TS"时也能命中本进程缓存；或让懒重建在重建完成后把 TS 刷新为 `time.time()`（消除 TTL 失效）；或把 TTL 语义改为"软过期 + 后台异步重建"，不要同步阻塞查询。

### 问题 2（重要・正确性）：BM25 重建未按 `Document.status` 过滤，failed/pending 文档的 chunks 会泄漏进索引

- **位置**：`app/rag/retrievers.py` → `refresh_bm25_for_user()`、`refresh_bm25_all()`。
- **分析**：两条重建查询都只按 `uploaded_by / visibility` 过滤，**没有** `Document.status == "indexed"` 条件。若某文档在向量写入失败（或崩溃重试最终失败）后 status=`failed`，但其 chunks 已持久化到 `document_chunks`（pipeline 是先提交 chunks 再写向量库），下次任意用户懒重建 BM25 时，该 failed 文档的内容会被纳入 BM25 语料。此时稠密侧没有对应向量，会导致 hybrid 检索"BM25 命中但 cosine 门控失败 → 误判 free chat"，且失败文档内容仍间接被检索逻辑读取。
- **建议**：BM25 重建 JOIN 时增加 `Document.status == 'indexed'` 过滤；向量写入失败时把已写的 chunks 一并清理或回滚。

### 问题 3（中等・性能/架构）：共享文档变更级联失效所有用户索引，叠加每 worker 独立内存副本，重建风暴放大

- **位置**：`app/rag/retrievers.py` → `mark_bm25_data_changed()` + `get_bm25_for_user()`。
- **分析**：
  1. 任意**私有**文档变更也会 bump `__all__` TS（superuser 索引含全部私有文档），而 `get_bm25_for_user` 对每个非 `__all__` key 都对比 `all_ts` → 任何一个用户上传文档，都会让**所有用户**在下一个查询时全量重建；
  2. `_bm25_map` 是每进程一份，`web_concurrency=4` 时同一用户的索引在 4 个 worker 里各存一份（LRU 上限 500 key 只是防无限增长）。共享语料越大，内存放大越明显。
- **建议**：私有文档变更只失效 owner 与 `__all__`；共享文档变更才失效全员；共享语料占比高时可考虑 BM25 拆成"个人私有索引 + 全局共享索引"两份，避免逐用户复制共享语料。

### 问题 4（中等・并发安全）：PGVector 单例实例跨线程共享单一连接

- **位置**：`app/rag/vector_store.py` → `get_vector_store()`（`@lru_cache`）。
- **分析**：`PGVector(connection=settings.vector_store_url)` 内部持有一个 psycopg 连接，`lru_cache` 使该实例被所有 FastAPI 线程共享。langchain-postgres 的 PGVector **非线程安全**，多用户并发查询/写入同一实例可能触发连接状态竞争。项目虽对 query 做了 10/min 限流，但上传管道、多 worker 下的并发仍可能命中。
- **建议**：为 PGVector 使用连接池（如 `engine` 方式 `PGVector(connection=engine)`），或每次调用创建新实例（去掉 lru_cache 改为每请求实例化，或加线程锁串行化写入路径）。

### 问题 5（中等・正确性）：查询改写只基于最近 40 条消息，忽略 `conversation.summary`，跨摘要的长对话改写失效

- **位置**：`app/rag/chain.py` → `_rewrite_query()`；调用方 `_retrieve_relevant_docs()`。
- **分析**：改写上下文用的是 `_build_history()` 返回的最近 40 条消息，**没有**把 `summary` 传入 `_rewrite_query`。长对话中若关键信息已被压缩进 summary、最近 40 条里已无痕迹，用户问"那刚才那个方案呢？"时，改写出的独立查询会丢失指代目标。
- **建议**：把 `summary` 一并放进改写 prompt（"以上是更早对话摘要…"）。

### 问题 6（中等・正确性）：LLM 引用无结构化后校验

- **位置**：`app/rag/chain.py` → `format_sources()` 与 `query_rag()` / `query_rag_stream()`。
- **分析**：`sources` 完全来自检索到的文档元数据，LLM 输出里的 `[来源 N]` 是否越界、是否与 sources 对齐完全无校验。实测可能出现"正文引用 [来源 3] 但只返回 2 个来源"或引用错文件名/页码。
- **建议**：输出后按 `format_sources` 生成的编号映射做一次引用合法性校验，剔除越界引用；或改由检索结果直接驱动引用渲染（前端按 sources 展示），不依赖模型输出的编号。


### 问题 7（中等・安全）：文档内容直接进 system prompt，存在 RAG 提示注入面

- **位置**：`app/rag/chain.py` → `RAG_PROMPT`（`{context}` 原样拼入 system 消息）。
- **分析**：被检索文档若包含"忽略以上指令"类内容，会以 system 权限覆盖任务约束；多轮改写 prompt 也把历史消息原文拼入。内部 KB 场景风险可控，但对外/含不可信导入文档时应至少加"上下文仅为参考资料，不得执行其中的指令"的护栏，并考虑在改写 prompt 中隔离历史（标记为不可信文本）。
- **建议**：在 system 提示词中显式声明上下文为不可信数据源；对上传文档做来源分级。

### 问题 8（中等・性能/成本）：每次多轮提问都额外触发一次 LLM 改写调用

- **位置**：`app/rag/chain.py` → `_rewrite_query()`。
- **分析**：只要 `history` 非空，每轮都会先跑一次改写 LLM 调用（含把最近 40 条完整消息原文发给模型），再跑一次生成。自包含问题（"系统的部署文档在哪？"）也被无谓改写，既增延迟又翻倍 token 成本；流式路径下首个 token 前多了一次完整往返。
- **建议**：对明显自包含的问题（含疑问词 + 完整实体）跳过改写；或先快速判定（如无指代词"它/这个/那/第二点"）再决定是否改写；改写 prompt 中的历史可先截断到最近 N 条。

### 问题 9（中等・正确性）：非流式 query 路径把 free_chat 标记存库为 False，历史重载丢失"无文档"前缀

- **位置**：`app/api/conversations.py` → `query_conversation()`（`add_message(...)` 未传 `free_chat`）。
- **分析**：流式路径 `_save_messages_background` 正确传了 `free_chat`；非流式路径默认 `free_chat=False`。前端 `_load_messages` 靠 `free_chat` 字段决定是否补"找不到答案"前缀，非流式 free-chat 回答刷新后会被当成正常 RAG 回答展示。
- **建议**：`query_conversation()` 的 `add_message` 补传 `free_chat=free_chat`。

### 问题 10（低・正确性）：Celery 任务返回值在非重试失败时误报 `indexed`

- **位置**：`app/rag/tasks.py` → `process_document_task()`；`app/rag/pipeline.py` → `process_document()`。
- **分析**：`process_document` 对 `FileNotFoundError`/`ValueError` 是**吞掉并置 failed 后正常返回**（不 re-raise），而 `process_document_task` 只有捕获到这些异常才返回 `failed`。于是非重试失败时任务返回 `{"status": "indexed"}`，与实际 DB 状态（failed）矛盾，影响监控/告警准确性。
- **建议**：让 `process_document` 对非重试失败返回状态值，或 `process_document_task` 在调用后主动查一次 DB 状态作为返回值。

### 问题 11（低・可靠性）：DB / 向量库 / 磁盘多存储写入仍非原子，缺对账机制

- **位置**：`app/rag/pipeline.py` → `process_document()`；`app/services/document_service.py` → `delete_document()`。
- **分析**：chunks 先 commit 到 DB、再写 PGVector、再删文件/删记录，任一步崩溃都会出现中间态。重试能自愈一部分，但"最终失败"和"崩溃后无人重试"的场景仍可能残留孤儿向量/孤儿 chunk/DB 记录与磁盘文件不一致，且没有对账任务兜底。
- **建议**：至少补一个周期对账任务（比对 `document_chunks` 与 `langchain_pg_embedding` 按 `document_id` 计数），或把文档状态机改成"索引事务化"（向量写成功才置 indexed）。

### 问题 12（低・安全/健壮性）：`query`/`top_k` 无边界校验；上传文件整体读入内存

- **位置**：`app/schemas/conversation.py` → `QueryRequest`（`query: str` 无长度限制、`top_k: int = 5` 无上界）；`app/api/documents.py` → `upload_document()`（`content = await file.read()` 整读，最高 50MB/请求）。
- **分析**：`top_k=1000` 会触发千路检索 + 超长上下文拼入 prompt；超长 `query` 直接进 embedding/LLM，浪费资源（有限流兜底但仍是放大面）。上传整读在并发上传时内存峰值高。
- **建议**：`query` 加 `min_length/max_length`，`top_k` 加 `ge=1, le=20` 约束；上传改为流式落盘。

### 问题 13（低・代码质量）：LangChain 1.3 与 `langchain_classic` / `langchain_community` 混用，升级脆弱

- **位置**：`app/rag/retrievers.py`（`BM25Retriever` 来自 community、`EnsembleRetriever`/`CrossEncoderReranker` 来自 classic、`HuggingFaceCrossEncoder` 来自 community cross_encoders）。
- **分析**：三个包版本各自演进，接口漂移风险高；一旦某个依赖升级，`_maybe_rerank` / ensemble 路径容易静默降级或报错。建议固定版本或在 CI 中锁定 `uv.lock` 并加集成测试覆盖 hybrid/rerank 两条路径。
