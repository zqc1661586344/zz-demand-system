# zz-demand-system RAG 模块逐流程评审

## 一、索引管道逐流程评价

### 流程 1：文档加载（`pipeline.py` → `load_document`）

**实现**：PDF→PyPDFLoader，TXT/MD→TextLoader，DOCX→Docx2txtLoader

**合理性评价：基本合理，解析能力偏弱（未变）**

- ✅ 格式覆盖、编码处理正确
- ⚠️ PyPDFLoader 对表格/扫描件处理差，Markdown 用 TextLoader 丢失标题结构
- 📌 代码中有 TODO，作者已意识到但未实现

---

### 流程 2：文本分块（`splitters.py`）

**实现**：`RecursiveCharacterTextSplitter(chunk_size=800, overlap=150)`，含中文句号分隔符

**合理性评价：参数合理，策略仍单一（未变）**

- ✅ 中文句号分隔符、overlap 比例合理
- ❌ 所有文档类型一刀切，无 PDF 表格/Markdown 标题/代码文件适配
- 📌 `pipeline.py` 中有 TODO 注释

---

### 流程 3：嵌入模型配置（`embeddings.py`）

**合理性评价：合理（未变）**

- ✅ 多供应商、bge-m3 中文友好、测试模式、懒加载、`@lru_cache`

---

### 流程 4：向量库存储与用户过滤（`vector_store.py`）

**实现**：Chroma 单 collection，cosine 度量，`_user_where(user_id)` 过滤（私有+共享）

**合理性评价：多租户隔离完善（未变）**

- ✅ 普通用户只能检索自己的私有文档 + 所有共享文档
- ✅ superuser 传 None 全量可见
- ✅ 所有检索函数都带 user_id 参数
- ✅ 遥测关闭、cosine 显式指定
- 📌 提供了迁移脚本 `scripts/migrate_chroma_tenant.py` 给旧数据补 metadata

---

### 流程 5：BM25 索引构建与缓存管理（`retrievers.py`）

**实现**：`_bm25_map[user_id]` 按用户隔离，从 `DocumentChunk` 表重建，懒加载 + sentinel 防并发，新增 `invalidate_other_users_bm25()`

**合理性评价：共享文档缓存一致性已修复，但引入了 superuser 全量索引的回归 bug**

**已修复的部分**：
- ✅ 共享文档上传后：`refresh_bm25_for_user(uploader)` + `invalidate_other_users_bm25(except_user_id=uploader)`
- ✅ 共享文档删除后：同样刷新上传者 + 失效其他用户
- ✅ 失效后其他用户下次查询通过 `get_bm25_for_user` 懒加载重建最新索引
- ✅ `invalidate_other_users_bm25` 线程安全，遍历 keys 时用 `list()` 快照

**🔴 新发现的回归 bug — superuser 全量索引 `"__all__"` 未失效**：

```python
def invalidate_other_users_bm25(except_user_id=None):
    with _bm25_lock:
        for key in list(_bm25_map.keys()):
            if key == "__all__":
                continue   # ❌ 跳过了 superuser 全量索引！
            if except_user_id and key == except_user_id:
                continue
            _bm25_map.pop(key, None)
```

- 共享文档变更时，`"__all__"`（superuser 全量 BM25 索引）被保留，不会失效
- 结果：superuser 查询时，BM25 索引仍是旧的——新上传的共享文档搜不到，已删除的共享文档还能搜到
- 上一轮代码中删除共享文档时是手动 `_bm25_map.pop("__all__", None)`，这一轮改用 `invalidate_other_users_bm25` 后，这个手动清空逻辑被替换掉了，但新函数内部跳过了 `__all__`，导致回归

**修复建议**：`invalidate_other_users_bm25` 中不要跳过 `"__all__"`，共享文档变更时 superuser 全量索引也应该失效，让其下次查询懒加载重建。

**仍存在的架构性问题**：
- ❌ `_bm25_map` 仍是内存全局状态，多进程部署时各 worker 独立副本，不一致
- `invalidate_other_users_bm25` 只解决了**单进程内**的缓存一致性，跨进程仍不行

---

### 流程 6：文档处理管线编排（`pipeline.py` → `process_document`）

**实现**：加载→注入元数据→分块→清理旧 Chroma→清理旧 DocumentChunk→写入 DocumentChunk→写入 Chroma→更新状态→刷新上传者 BM25→共享文档则失效其他用户缓存

**合理性评价：流程完整，共享文档缓存传播已修复，但任务可靠性和数据一致性仍有风险**

**改进点**：
- ✅ 共享文档上传后调用 `invalidate_other_users_bm25`，其他用户缓存失效
- ✅ DocumentChunk 表持久化，重处理前清理旧记录
- ✅ 元数据注入 `uploaded_by` 和 `visibility`

**仍存在的问题**：
- ❌ **仍用 BackgroundTasks**，服务重启时 pending/processing 文档永久丢失，无重试
- ⚠️ **Chroma 与 DB 非事务一致性**：先写 DocumentChunk（DB 事务），再写 Chroma（外部系统），如果 Chroma 写入失败，DB 已有 chunk 记录但 Chroma 没有向量，稠密检索缺失；反之 DB 提交失败但 Chroma 已写入，也会不一致
- ⚠️ 大文档处理在主进程后台执行，高并发上传时可能拖慢查询

---

## 二、查询管道逐流程评价

### 流程 7：对话历史组装（`conversations.py` → `_build_history`）

**实现**：最近 20 轮完整 + 更早摘要，`RECENT_ROUNDS=20`

**合理性评价：合理（未变）**

- ✅ 滑动窗口 + 摘要压缩策略标准
- ⚠️ `docs/RAG.md` 可能仍写的是 5 轮，需确认文档是否同步更新

---

### 流程 8：混合检索（`retrievers.py` → `hybrid_search`）

**实现**：
- BM25 无数据 → 纯稠密 + spread 双判据
- BM25 有数据 → EnsembleRetriever（稠密+BM25+RRF）→ **新增 k=1 cosine 分数门控** → 可选重排

**合理性评价：相关性门控已加回，但重复检索问题再次出现**

**已修复的部分**：
- ✅ 上一轮"BM25 有数据时完全跳过相关性判定偏激进"的问题已修复
- ✅ ensemble 后增加 `similarity_search_with_relevance(query, k=1, user_id=user_id)`，低于 `rag_min_score` 回退 free chat
- ✅ 注释说明"只多一次轻量查询，不做 spread"

**新问题 — 重复检索再次出现**：
- hybrid 模式 BM25 有数据时，现在做了**两次稠密检索**：
  1. `dense_retriever = vs.as_retriever(...)` → EnsembleRetriever 内部调用一次
  2. `similarity_search_with_relevance(query, k=1, ...)` → 相关性门控又一次
- 上一轮作者为了消除重复检索把 spread 判定移出了 hybrid_search，这一轮为了加回安全检查又引入了一次
- 不过这次 k=1 比之前的 k=2 更轻量，且是必要的安全检查，**可以接受**
- 更优方案：让 dense_retriever 返回分数，或用 Chroma 原生 `similarity_search_with_relevance_scores` 一次查询同时拿到 top_k 文档和分数，既用于 ensemble 又用于门控。但 LangChain 的 `as_retriever` 不返回分数，这是 API 限制

**仍存在的策略争议**：
- BM25 有数据时只做 k=1 绝对分数检查，不做 spread。如果 top-1 分数刚好过阈值但 top-2 分数几乎一样（平带），仍可能返回不相关文档。不过 k=1 门控已经能拦住大部分无关查询，比完全不检查好很多

---

### 流程 9：RRF 融合（`EnsembleRetriever`）

**合理性评价：合理（未变）**

- ✅ RRF 而非分数相加，`weights=[alpha, 1-alpha]`，`c=60`

---

### 流程 10：可选重排（`_maybe_rerank`）

**合理性评价：合理，实现仍有迂回（未变）**

- ✅ 可选开关、优雅降级、线程安全缓存
- ⚠️ 用 InMemoryVectorStore 包装交叉编码器，多了一层不必要的向量存储

---

### 流程 11：相关性判定与回退（`chain.py` → `_retrieve_relevant_docs`）

**实现**：三模式分发
- hybrid：委托 `hybrid_search`（内部已做判定）
- mmr：先查 MMR，再用稠密 top-1 分数阈值过滤
- similarity：按 `rag_min_score` 阈值过滤

**合理性评价：MMR 模式已加过滤，整体完善（未变）**

- ✅ MMR 模式之前完全不过滤，现在增加了 top-1 分数阈值检查
- ✅ 所有函数签名带 user_id，贯穿全链路

---

### 流程 12：RAG 生成链（`chain.py` → `build_rag_chain` / `query_rag`）

**合理性评价：合理，Prompt 仍有提升空间（未变）**

- ✅ 链缓存线程安全（double-check lock）
- ✅ 上下文格式标注来源
- ⚠️ 英文 Prompt、引用格式不固定、无结构化输出

---

### 流程 13：自由聊天回退（`_build_free_chat_chain`）

**合理性评价：合理（未变）**

- ✅ 回退机制完整，提示语不污染历史
- ⚠️ 自由聊天模式下 LLM 可能输出与企业知识库冲突的信息

---

### 流程 14：流式输出（`query_rag_stream` + API 层）

**合理性评价：仍存在数据库会话生命周期风险（未修复）**

- ✅ SSE 协议完整，用户消息同步写避免竞态
- ❌ `event_stream()` 闭包仍使用外部注入的 `db` 会话：
  ```python
  def event_stream():
      add_message(db, conv_id, ...)  # 用外部 db
      db.commit()
      for event in query_rag_stream(...):
          yield ...
  ```
- FastAPI 在请求返回时关闭 `db` 会话，但 `StreamingResponse` 生成器在响应返回后仍在执行。当前只在开头用了一次 `db`，暂时不触发 bug，但架构上不安全
- **建议**：生成器内部用 `SessionLocal()` 创建独立会话（`_save_messages_background` 已经是这么做的，用户消息保存也应该一致）

---

### 流程 15：对话摘要（`generate_summary`）

**合理性评价：合理（未变）**

- ✅ 线程安全、异常不影响主流程
- ⚠️ 覆盖式更新，超长对话可能丢失早期细节

---

## 三、跨流程架构性问题

### 🔴 P1：单进程限制（全局 BM25 状态）— 未修复

`_bm25_map` 仍是内存中的全局可变状态。多进程（gunicorn 多 worker）部署时：
- 进程 A 为用户 X 重建了 BM25（含新文档），进程 B 的 `_bm25_map[X]` 仍是旧的
- 用户 X 的请求被负载均衡到进程 B 时，BM25 检索不到新文档
- `invalidate_other_users_bm25` 只解决了单进程内的缓存一致性，跨进程无效

**对于中小企业单进程部署（uvicorn 单 worker）不是问题，但 README 仍建议 gunicorn 多 worker，会产生不一致。**

### 🟡 P2：无持久化任务队列 — 未修复

BackgroundTasks 处理文档，服务重启丢任务，无重试机制。

### 🟡 P2：Chroma 与 DocumentChunk 非事务一致性 — 未修复

先写 DB 再写 Chroma，无分布式事务保证，极端情况下两边数据不一致。

### 🟡 P2：流式接口 db 会话生命周期 — 未修复

如上所述。

### 🟢 P3：分块策略单一、无检索监控、无 Prompt 注入防护、SQLite 并发瓶颈 — 未修复

---

## 四、总体评审意见

### 当前剩余问题分级

| 优先级 | 问题 | 影响 |
|--------|------|------|
| **P1（回归 bug）** | `invalidate_other_users_bm25` 跳过 `"__all__"`，superuser 全量索引在共享文档变更后不失效 | superuser BM25 检索结果过时 |
| P1 | 单进程限制（全局 BM25 状态） | 无法多 worker 水平扩展 |
| P2 | 无持久化任务队列 | 服务重启丢文档处理任务 |
| P2 | 流式接口 db 会话生命周期 | 潜在会话已关闭风险 |
| P2 | Chroma 与 DocumentChunk 非事务一致性 | 极端情况下两边数据不一致 |
| P3 | 分块策略单一、无监控、无 Prompt 注入防护、SQLite 并发瓶颈 | 体验和安全优化项 |
