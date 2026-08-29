# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Enterprise RAG 文档问答系统 — FastAPI 后端 + LangChain RAG + Streamlit 前端。中文文档为主。

## 运行时必须遵守的原则

> **只管找出问题，告诉我怎么操作，我自己来运行验证。**
>
> 你不应该运行任何命令。你只负责分析代码、定位问题、给出修复方案和验证步骤，所有命令都由我来手动执行。

## 注意事项

- 所有需要 `curl`、`python`、`uv`、`pip`、`sqlite3`、`.venv/` 等命令的操作，都只写出命令，由我执行。
- 你可以阅读文件、搜索代码、分析逻辑，但不要用 Bash 运行任何东西。
- 文件修改（Edit/Write）可以进行，但修改后告诉我需要重启什么服务、执行什么命令来验证。

---

## 常用命令

```bash
# 安装依赖
uv sync                     # 基础依赖
uv sync --extra ollama      # 需要本地 Ollama 时
uv sync --extra postgres    # 需要 PostgreSQL 时
uv add rank_bm25            # Hybrid RAG 需要（BM25 稀疏检索）

# 启动服务（开发模式，一键启动后端 + 前端）
bash start.sh               # 后端 8001 + Streamlit 前端 8002
# 或分别启动：
uvicorn app.main:app --reload --port 8001                      # 后端
streamlit run app/streamlit_app/app.py --server.port 8002      # 前端

# 测试模式启动（无外部 LLM API，用本地 mock）
LLM_PROVIDER=test EMBEDDING_PROVIDER=test uvicorn app.main:app --port 8001

# 生产/服务器后台启动
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/rag.log 2>&1 &
nohup .venv/bin/streamlit run app/streamlit_app/app.py --server.port 8002 --server.headless true > /tmp/streamlit.log 2>&1 &
# 验证：curl http://localhost:8001/api/health
# 看日志：tail -f /tmp/rag.log ；停止：pgrep -af uvicorn 后 kill <PID>

# lint / 类型检查
ruff check .                # 已配置：--target-version=py311 --line-length=100

# 测试
python test_e2e.py          # 端到端测试：先以 test 模式启动服务，再运行
pytest                      # dev extra 下的单元测试
```

### 端口约定（重要）

| 端口 | 用途 |
|------|------|
| `8001` | **后端（FastAPI）**，`start.sh` 和所有 API 调用都指向此端口 |
| `8002` | **前端（Streamlit）**，`start.sh` 同时启动此端口 |

`BASE_URL` 的单一事实来源是 `app/streamlit_app/api_client.py`（指向 `http://localhost:8001`）。

### 环境/配置要点

- 配置全部走 `.env`（`app/config.py` 用 pydantic-settings 读取），改动配置后需重启服务。
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` 默认 30 分钟，用户已在 `.env` 中改为 525600（1 年）避免长空闲后 401。**不要改回 30 分钟**。
- LLM/Embedding 供应商（`openai`/`ollama`/`test`）分别在 `app/rag/llms.py` 和 `app/rag/embeddings.py` 中按 `settings` 选择。
- 检索算法通过 `RAG_SEARCH_TYPE` 切换：`similarity`（纯向量）/ `mmr`（多样性）/ `hybrid`（BM25+向量+RRF融合，默认）。

---

## 架构

```
app/
├── api/             # FastAPI 路由层（auth/users/documents/conversations/workflows）
├── models/          # SQLAlchemy ORM（user/document/conversation/workflow）+ __init__ 中统一导出
├── schemas/         # Pydantic 请求/响应模型
├── services/        # 业务逻辑（document/conversation/auth/user/workflow）
├── rag/             # RAG 核心
│   ├── chain.py         # RAG 链：检索→格式→生成，支持流式 SSE
│   ├── pipeline.py      # 文档处理管线：加载→分块→索引
│   ├── vector_store.py  # Chroma 封装（单 collection "documents"）
│   ├── retrievers.py    # Hybrid RAG：BM25 + RRF 融合 + 可选 reranker（新增）
│   ├── llms.py          # LLM 供应商选择
│   ├── embeddings.py    # Embedding 供应商选择
│   └── splitters.py     # 文本分片策略
├── workflows/       # 业务流程引擎（文档审批、问题升级示例）
├── streamlit_app/   # Streamlit 前端
│   ├── app.py           # 主入口（两页：Chat + Documents）
│   ├── api_client.py    # httpx 同步客户端，与后端通信
│   ├── auth.py          # 登录/注册/Token 管理
│   └── views/
│       ├── chat.py      # 对话页（SSE 流式输出）
│       └── documents.py # 文档管理页（上传/列出/删除）
└── main.py          # FastAPI 入口，挂载 API 路由
```

### 关键依赖链（改代码前先看这条链路）

```
上传文件 → app/api/documents.py
        → app/rag/pipeline.process_document()   # 解析→分块→向量化索引
        → app/rag/vector_store.add_documents_to_store()  # 写入 Chroma
        → app/rag/retrievers.refresh_bm25_index_from_chroma()  # 同步 BM25 索引

提问(SSE流) → app/api/conversations.py: query_conversation_stream
            → app/rag/chain.query_rag_stream()  # 检索 + 生成
            → _retrieve_relevant_docs()          # 按 rag_search_type 分发
                ├─ rag_search_type="hybrid"  → retrievers.hybrid_search()   # BM25 + Chroma + RRF
                ├─ rag_search_type="mmr"     → vector_store.mmr_search()
                └─ rag_search_type="similarity" → vector_store.similarity_search_with_relevance()
            → app/rag/llms.get_llm()           # OpenAI/Ollama/test

删除文档 → app/services/document_service.py: delete_document()
         → app/rag/vector_store.delete_documents_from_store()  # Chroma 按 document_id 删除
         → app/rag/retrievers.refresh_bm25_index_from_chroma() # 同步 BM25 索引
```

### 两点最容易踩的坑

1. **前后端端口/地址依赖**：Streamlit 前端通过 httpx 调 FastAPI，`BASE_URL` 在 `app/streamlit_app/api_client.py` 唯一定义（`http://localhost:8001`）。改动端口要同步。

2. **对话状态的持久化方式**：
   - 普通查询 `POST /api/conversations/{id}/query`：同步保存消息。
   - 流式查询 `.../query/stream`：SSE (`text/event-stream`) 逐 token 返回，消息靠 `BackgroundTasks` 在流结束后异步入库。Streamlit 前端在 `app/streamlit_app/views/chat.py` 中通过 `resp.iter_lines()` 解析 SSE 事件。

### 会话摘要机制（多轮对话）

`app/api/conversations.py` 里 `RECENT_ROUNDS=5`：长对话只保留最近 5 轮消息进 prompt，更早的用 LLM 生成的 `summary` 代替；每满 `SUMMARY_INTERVAL=10` 条消息后台重新生成摘要。

### 认证

JWT（python-jose）+ bcrypt，RBAC 三角色 `admin`/`editor`/`viewer`（启动时 `app/database.py::_seed_roles` 播种）。`app/dependencies.py` 提供 `get_current_user` / `require_roles(*roles)` / `optional_current_user`。Streamlit 侧 `app/streamlit_app/auth.py` 自动登录并管理 Token（`st.session_state`），`ApiClient`（`app/streamlit_app/api_client.py`）自动注入 Bearer token 并在 401 时用 refresh token 重试一次。

### Hybrid RAG 检索流程

```
query
  ↓
  ├─▶ [Chroma dense retriever]  — bge-m3 稠密向量 + cosine 搜索
  │
  └─▶ [BM25 sparse retriever]   — rank_bm25 关键词精确匹配
  │
  └─▶ EnsembleRetriever (RRF 融合，c=60，alpha 可配)
         ↓
  └─▶ (可选) bge-reranker-v2-m3 交叉编码器重排 (需 transformers + torch)
         ↓
  └─▶ format_context + RAG_PROMPT + LLM generate
```

配置通过 `.env` 控制：
```
RAG_SEARCH_TYPE=hybrid         # similarity / mmr / hybrid（默认）
RAG_HYBRID_ALPHA=0.5          # 稠密 vs 稀疏权重（0=纯BM25, 1=纯向量）
RAG_SPARSE_BACKEND=pg_tsvector # 稀疏后端：pg_tsvector（默认，PG tsvector+GIN，增量零内存）/ bm25_memory（进程内BM25，全量载内存，SQLite回退）
RAG_RERANK_ENABLED=false       # 是否启用重排器（需额外安装依赖）
RAG_RERANK_MODEL=BAAI/bge-reranker-v2-m3
RAG_RERANK_TOP_N=5
```

### 已知技术债 / 未完成项

- **生产部署注意**：gunicorn（`-k uvicorn.workers.UvicornWorker`）在部分 gunicorn/uvicorn 版本组合下会 `Worker failed to boot / code 3`，已验证**用单进程 `uvicorn` 后台跑最稳**（命令见上「常用命令」）；多进程/自启用 systemd 前需先核对 gunicorn 与 uvicorn 版本。
- `app/rag/llms.py`、`embeddings.py` 里 `provider == "local"` 分支实际未启用（配置枚举只允许 `openai`/`ollama`/`test`）。
- 稀疏检索默认走 **PG tsvector**（`RAG_SPARSE_BACKEND=pg_tsvector`，增量、零内存）；仅当配置回退 `bm25_memory` 时，内存 BM25 才是**全量重建（非增量）**，文档量大时可能成为性能瓶颈。tsvector 后端已规避该问题。