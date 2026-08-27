### 问题 1（重要・正确性）：多轮对话无查询改写（standalone question），指代/省略类问题检索失效

**文件**：app/rag/chain.py，_retrieve_relevant_docs 函数

**现状**：检索函数直接接收用户的当前 query 字符串送入 retriever，完全忽略了对话历史上下文。多轮对话历史仅在生成阶段被拍平进 system prompt，检索阶段对历史"视而不见"。

**影响**：用户问"那第二点呢？""它的价格是多少？""这个怎么配置？"这类依赖上文指代或省略的问题时，检索器拿到的是一个语义不完整的片段，向量召回和 BM25 关键词匹配都会失效，导致返回无关文档甚至空结果。这是多轮 RAG 系统中对实际使用体验影响最大的单点缺失。

**建议**：在检索前增加一轮轻量 LLM 调用，将对话历史 + 当前问题改写为独立完整的检索查询（standalone question）。LangChain 提供了 create_history_aware_retriever 可直接使用，也可手写 prompt：

建议新增的改写步骤（放在 _retrieve_relevant_docs 之前）
```
contextualize_q_prompt = ChatPromptTemplate.from_messages([
    ("system", "给定对话历史和用户问题，生成一个独立的检索查询。"),
    MessagesPlaceholder("chat_history"),
    ("human", "{input}"),
])
history_aware_retriever = create_history_aware_retriever(
    llm, retriever, contextualize_q_prompt
)
```

### 问题 2（重要・正确性）：Redis 时间戳一致性机制存在三个实打实的缺陷

**文件**：app/rag/retrievers.py，refresh_bm25_for_user 函数末尾及 _is_bm25_fresh 判定函数

**缺陷 2a**：懒加载重建也会更新 Redis 时间戳，多 worker 互相"投毒"

**现状**：refresh_bm25_for_user 末尾无条件调用 _update_redis_ts，而懒加载重建（由 _get_or_build_bm25 触发）也走这个函数。

**影响**：worker A 重建后写入 ts=t1 → worker B 发现本地 ts < t1 也重建并写入 ts=t2 → worker A 又发现自己落后……在 ≥2 个 API worker 时，每次查询都可能触发一次全量重建，缓存收益归零甚至负收益。

**建议**：Redis 时间戳只在数据变更时（文档上传/删除的 pipeline 末尾）更新，重建本身只更新进程内本地时间戳，不碰 Redis。

**缺陷 2b**：共享文档变更的失效范围以"本进程内存里的 key"为界

**文件**：app/rag/retrievers.py，invalidate_other_users_bm25 函数

**现状**：该函数遍历当前进程的 _bm25_map 删除其他用户的 key。但该函数在 Celery worker 进程中执行，而 Celery worker 的 map 里通常只有刚重建的上传者一个 key（还被 except 跳过）——其他用户的 Redis 时间戳根本不会被删除。

**影响**：共享文档上传后，其他用户的 BM25 缓存会一直陈旧，直到 ts 的 TTL 自然过期。

**建议**：改用一个全局"脏标记"键（如 bm25:dirty 置为当前时间），所有用户的 _is_bm25_fresh 同时对比该全局键，而非逐用户删 key。

**缺陷 2c**：失效语义 fail-open，删 key 反而让其他 worker 失去感知失效的能力

**文件**：app/rag/retrievers.py，_is_bm25_fresh 函数中 redis_ts is None 的分支

**现状**：当 Redis 中的 ts 键被删除或 TTL 过期后，redis_ts=None，代码回退到"纯本地缓存模式"，直接信任本地旧索引。

**影响**：失效化操作（删除 ts 键）恰恰会让其他 worker 失去感知失效的能力，静默返回陈旧数据。正确语义应是：配置了 Redis 但读不到 ts → 视为已失效，强制重建（fail-closed）。

**建议**：
修改 _is_bm25_fresh 中的逻辑
if redis_ts is None:
    if settings.celery_broker_url:  # 说明配置了 Redis
        return False  # fail-closed：强制重建
    return True  # 未配置 Redis 时才回退本地



### 问题 3（重要・可靠性）：LLM / Embedding 调用无超时与重试配置

**文件**：
app/rag/llms.py，get_chat_model 函数中 ChatOpenAI(...) 实例化处
app/rag/embeddings.py，get_embeddings 函数中 OpenAIEmbeddings(...) 实例化处

**现状**：ChatOpenAI 和 OpenAIEmbeddings 均未配置 timeout、max_retries、request_timeout 等参数。

**影响**：API 抖动或网络超时时，调用会无限挂起，导致 Celery worker 被阻塞、API 请求超时。在 OpenAI API 限流（429）或服务端 5xx 时也不会自动重试。

建议：
```
ChatOpenAI(
    model=settings.llm_model,
    timeout=60,        # 60 秒超时
    max_retries=3,     # 自动重试 3 次
    ...
)
OpenAIEmbeddings(
    model=settings.embedding_model,
    timeout=30,
    max_retries=3,
    ...
)
```

同时 app/rag/tasks.py 中 autoretry_for=(ConnectionError, TimeoutError, OSError) 覆盖不到 OpenAI SDK 自身的瞬时 API 异常（如 openai.RateLimitError），建议补充。

### 问题 4（中等・正确性）：DB + Chroma 双写非原子，崩溃窗口期数据不一致

**文件**：app/rag/pipeline.py，ingest_document 函数

**现状**：chunks 先写入 PostgreSQL 的 DocumentChunk 表（db.session.commit()），再写入 Chroma 向量库。两步之间无跨存储事务。

**影响**：若在 db.session.commit() 之后、Chroma 写入之前进程崩溃，会出现"BM25 索引有数据（从 DB 重建）、向量库没有"的不一致状态，检索结果偏斜。

**建议**：
增加周期性对账任务：比对 DocumentChunk 表行数与 Chroma 按 document_id 的计数，发现差异自动补偿
或在 Chroma 写入失败时将文档状态回滚为 failed，而非保持 indexed

### 问题 5（中等・性能）：重排实现绕路，多余的重新向量化浪费算力

**文件**：app/rag/retrievers.py，_maybe_rerank 函数

**现状**：将已召回的文档先塞进 InMemoryVectorStore 重新做一遍 embedding，再经 ContextualCompressionRetriever 检索一次才交给重排器。

**影响**：多余的向量化调用既浪费 API 调用/算力（每次查询额外 N 次 embedding），又可能因重新向量化改变候选集顺序。

建议：直接调用 reranker.compress_documents(query, docs)，跳过不必要的重新嵌入：
替换当前的 InMemoryVectorStore + ContextualCompressionRetriever 绕路实现
reranked_docs = reranker.compress_documents(
    query=query, documents=docs
)

### 问题 6（中等・质量）：提示词全英文，面向中文文档中文问答场景不够优化

文件：app/rag/chain.py，RAG_PROMPT 常量定义处

现状：system prompt 全英文（"You are an assistant for question-answering tasks..."），而目标场景是中文文档、中文问答。

影响：虽然主流 LLM 跨语言能力尚可，但中文 system prompt 对输出语言稳定性（避免夹杂英文）和引用格式约束更好，尤其是要求"始终引用来源"时，中文 prompt 的指令遵循度更高。

建议：将 RAG_PROMPT 改为中文版本，例如：
RAG_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一个知识库问答助手。请根据以下检索到的上下文回答用户问题。"
               "如果上下文中没有答案，请如实说明。回答时始终标注信息来源。"),
    ("human", "上下文：n{context}nn问题：{question}"),
])

### 问题 7（中等・正确性）：引用无结构化后校验，LLM 可能引用不存在的来源

**文件**：app/rag/chain.py，_format_sources 函数及链的输出处理部分（

**现状**：引用完全靠 prompt 要求 "Always cite the source"，无后处理校验。来源列表是从检索到的文档元数据中提取的，但 LLM 输出中引用的来源编号可能与实际 sources 列表不匹配。

**影响**：LLM 可能引用 [来源3] 但实际只提供了 2 个来源，或引用了错误的文件名/页码。

**建议**：在链的输出端增加后处理步骤，校验 LLM 输出中的引用标记是否在 sources 列表范围内，剔除无效引用。

### 问题 8（低・代码质量）：文件存在性检查为死代码

**文件**：app/rag/pipeline.py，ingest_document 函数中

**现状**：先调用 load_document(doc.file_path) 加载文件，之后再检查 Path(doc.file_path).exists()。

**影响**：文件不存在时，loader 会先抛出 FileNotFoundError，Path.exists() 检查永远不会被执行到，是死代码。

**建议**：将 Path.exists() 检查前置到 load_document 调用之前。

### 问题 9（低・并发安全）：sparse.k = top_k 直接修改全局缓存对象属性

**文件**：app/rag/retrievers.py，BM25 检索调用处

**现状**：在查询时直接修改 BM25 索引对象的 k 属性：sparse.k = top_k。

**影响**：该对象是全局缓存的，不同 top_k 的并发查询会互相干扰（一个查询改了 k 值，另一个查询读到了被修改的值）。

**建议**：不要在缓存对象上直接修改属性，改为在调用时传参或使用局部包装，不修改全局对象，而是创建局部视图
```
results = sparse.get_scores(tokenized_query)
top_indices = np.argsort(results)[-top_k:][::-1]
```

