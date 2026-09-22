

# 复评报告

## 一、问题清单（按严重性排序）

### 问题 1（重要・正确性）：spread 双判据"造好了但没用在主路径"——hybrid 仍只有绝对阈值
**位置**：`app/rag/retrievers.py::_dense_only_search`（L375-407，用 `top1 < rag_min_score or spread < rag_hybrid_min_spread` 双条件）vs `_hybrid_fusion`（L429-443，**只判 `top1 < rag_min_score`**）

**机理**：`rag_search_type` 默认是 `hybrid`，生产流量几乎全走 `_hybrid_fusion`。而 bge-m3 的分数被压缩在窄区间（不相关 query 也能打出 0.44~0.50，这正是上轮分析过的现象）——**绝对阈值 0.4 在 hybrid 主路径上依然可能误杀"稀疏真命中但稠密低分"的合法检索**（上轮问题 2 的遗留）。`rag_hybrid_min_spread`（0.015）这个专门为解决"分数平带"设计的机制，只在纯稠密回退分支生效。

**修复**（2 行）：`_hybrid_fusion` 的最终把关改为与 `_dense_only_search` 一致的双条件：
```python
top1 = scored[0][1]
spread = scored[0][1] - scored[1][1] if len(scored) >= 2 else 1.0
if top1 < settings.rag_min_score or spread < settings.rag_hybrid_min_spread:
    ...
```
或至少用 RAGAS 跑一组 hybrid 路径对比验证 0.4 阈值的误杀率。

### 问题 2（重要・架构决策）：`rag_rerank_enabled` 默认 True→False 反转，无数据支撑、无文档说明
**位置**：`app/config.py`（`rag_rerank_enabled: bool = False`）；`docs/RAG.md` L321/L337 仍写"可选（需安装 transformers）"

**机理**：上上轮（292bda3）把 rerank 默认打开并投入开发（双 provider+冷却降级），本轮**静默改回默认关闭**，且：
- config 无注释说明原因；
- docs/RAG.md 无决策记录；
- commit message 无关联。

**如果这是基于 RAGAS 数据**（k10 组 context_precision 0.659→0.607 的下降，或 bge-reranker-v2-m3 在中文法规场景效果差），那**这是合理的工程决策，但必须记录依据**（写进 docs/RAG.md 的"决策记录"节），否则下一个人会困惑于"为什么投入开发的 rerank 被默认禁用"。**如果只是怕 transformers 依赖**，那应该保留 True + 失败降级（`get_reranker` 的冷却机制本来就会处理）。

**修复**：config 加注释 + docs/RAG.md 加一段"rerank 决策记录"（何时默认关、为什么、何时可重开、用什么数据判断）。

### 问题 3（一般・正确性）：docs/RAG.md 与代码实现多处不一致（文档滞后于重构）
**位置**：`docs/RAG.md` L339-344 vs `app/rag/retrievers.py::_hybrid_fusion`

**不一致清单**（我逐条比对）：
- 文档："稀疏命中 → 融合后**直接返回**，仅当稠密侧完全零相关才回退 free chat" → 代码实际：融合后 `top1 < rag_min_score` 也走 free chat；
- 文档：rerank 描述为"可选（需安装 transformers）" → 代码默认 `False` 且有硅基 API provider；
- 文档 L568-570 表格与最新 config 字段（`rag_bm25_cache_bypass`、`rag_embedding_dim`）未同步。

**影响**：文档是团队理解系统的第一入口，L339 的"直接返回"描述会让后续开发者误判 free-chat 触发条件，掩盖问题 1。建议本轮把 `docs/RAG.md` 的检索流程段与代码逐句对齐。

### 问题 4（一般・架构）：`get_reranker` 锁竞争问题已标注 TODO 但未修
**位置**：`app/rag/rerankers.py::get_reranker`（L109 上方 `# TODO：冷却期锁竞争问题，需要优化`）

**机理**：`_reranker_lock` 同时保护"构建"与"冷却判定"两个临界区。HuggingFace 模型加载（local provider）耗时数秒，期间所有请求阻塞在同一把锁上；并发失败时 `mark_reranker_failed` 也拿同一把锁。这是正确性无害、但延迟有感的并发问题。

**修复建议**：用 `sync.Once` 管构建、用独立的轻量锁（或 atomic int64）管冷却时间戳，两把锁职责分离。

### 问题 5（一般・质量）：faithfulness 优化仍未落地（连续三轮）——RAGAS 已给出靶子，但没开枪
**位置**：`app/rag/chain.py::generate_answer` / `sanitize_citations`；基线 `scripts/eval_results_*.csv`

**现状**：faithfulness 0.61~0.66（约 1/3 答案陈述无上下文支撑）仍是最大质量问题。本轮重构了加载/检索/架构，**唯独生成侧没动**。建议的 prompt 硬约束（"仅依据给定材料回答，未提及的信息明确说不知道"）是 2 行改动，收益立即可测。

### 问题 6（提示・工程）：`rag_bm25_cache_bypass` 暴露的多进程一致性——Redis 未配置时的降级路径需确认
**位置**：`app/rag/retrievers.py::_set_redis_ts/_get_redis_ts/_check_bm25_cache`

**机理**：用 Redis 时间戳做跨进程 BM25 失效协调是正确方向 ✅；但 `_set_redis_ts` 在 Redis 不可用时如何降级（异常吞掉？回退本地时间戳？）需要确认——若静默失败，多 worker 下 BM25 缓存依然各自为政，`rag_bm25_cache_bypass=True` 才真正正确。建议给 Redis 失败加 warning 日志。

---
