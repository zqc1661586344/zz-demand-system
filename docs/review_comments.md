# 代码评审报告

### 问题 1（中等・正确性 / 一致性）：`rag_sparse_min_rank` 阈值在 SQL 与 hybrid 里用了两套不同的 `ts_rank` 尺度

- **位置**：`app/rag/sparse_search.py` → `search()`（WHERE 用 `ts_rank(to_tsvector(...), q.ts) > :min_rank` **无归一化**；SELECT 用 `ts_rank(..., 1) AS r` **归一化**）；`app/rag/retrievers.py` → `hybrid_search()`（`d.metadata["sparse_score"] >= settings.rag_sparse_min_rank`，sparse_score 是**归一化后**的 r）
- **分析**：同一个 `rag_sparse_min_rank=0.1` 被施加到两种尺度上：无归一化的 `ts_rank` 值域大（800 字 chunk 可到 0.x~ 几），归一化后 ≈ 原始值 /(1+log (len))（800 字 ≈ 除以 7.7）。结果是 SQL 的 `>0.1` 几乎形同虚设，真正的把关其实是 hybrid 里 `sparse_score≥0.1`（等价于无归一化 rank≈0.77），**有效阈值比配置值严了约一个数量级**，可能把 "关键词真命中但覆盖度中等" 的弱相关 chunk 误杀，回退到稠密路径后再被 `top1≥0.4` 拒掉 → 该走 RAG 的问题走了 free chat。同时同一文档被过滤两次（SQL 一次、hybrid 一次），语义混乱。
- **建议**：统一只在一处把关、用同一种 `ts_rank` 表达式。推荐：SQL 里就用归一化 `ts_rank(...,1) > :min_rank` 过滤并把 `r` 透出，`hybrid_search` 对 pg_tsvector 后端**不再重复过滤**（bm25_memory 无 sparse_score，保持不加下限）；或反过来，SQL 去掉 `> :min_rank`、只保留 hybrid 的 metadata 过滤。


### 问题 2（低・架构・遗留）：LangChain 1.3 与 `langchain_classic`/`langchain_community` 混用

- **位置**：`app/rag/retrievers.py`（`BM25Retriever`/`HuggingFaceCrossEncoder` 来自 community，`CrossEncoderReranker` 来自 classic）
- **建议**：固定版本、CI 锁 `uv.lock`，补 hybrid/rerank 集成测试（`tests/test_rag/` 目前仍为空）。
