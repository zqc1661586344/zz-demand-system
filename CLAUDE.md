# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Enterprise RAG 文档问答系统 — FastAPI 后端 + LangChain RAG + Chainlit 前端。中文文档为主。

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
uv sync                 # 基础依赖
uv sync --extra ollama  # 需要本地 Ollama 时

# 启动服务（开发模式，单个进程挂载了 Chainlit 前端）
uvicorn app.main:app --reload --port 8001

# 测试模式启动（无外部 LLM API，用本地 mock）
LLM_PROVIDER=test EMBEDDING_PROVIDER=test uvicorn app.main:app --port 8001

# 生产/服务器后台启动
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/rag.log 2>&1 &
# 验证：curl http://localhost:8001/api/health
# 看日志：tail -f /tmp/rag.log ；停止：pgrep -af uvicorn 后 kill <PID>

# lint / 类型检查
ruff check .            # 已配置：--target-version=py311 --line-length=100

# 测试
python test_e2e.py      # 端到端测试：先以 test 模式启动服务，再运行
pytest                  # dev extra 下的单元测试
```

### 端口约定（重要）

| 端口 | 用途 |
|------|------|
| `8001` | **后端 + Chainlit 前端挂载在同一进程**（`app/chainlit_app/api_client.py` 中 `BASE_URL`） |
| `8002` | 保留（仅当需要独立运行 Chainlit 时使用） |

改动涉及前后端调用时，务必确认端口一致。`BASE_URL` 的单一事实来源是 `app/chainlit_app/api_client.py`。

### 环境/配置要点

- 配置全部走 `.env`（`app/config.py` 用 pydantic-settings 读取），改动配置后需重启服务。
- `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` 默认 30 分钟，用户已在 `.env` 中改为 525600（1 年）避免长空闲后 401。**不要改回 30 分钟**。
- LLM/Embedding 供应商（`openai`/`ollama`/`test`）分别在 `app/rag/llms.py` 和 `app/rag/embeddings.py` 中按 `settings` 选择。

---

## 架构

经典分层 + 单一 Chroma collection（KB 层已移除，所有文档扁平存放）：

```
app/
├── api/          # FastAPI 路由层（auth/users/documents/conversations/workflows）
├── models/       # SQLAlchemy ORM（user/document/conversation/workflow）+ __init__ 中统一导出
├── schemas/      # Pydantic 请求/响应模型
├── services/     # 业务逻辑（document/conversation/auth/user/workflow）
├── rag/          # RAG 核心：chain / pipeline / vector_store / llms / embeddings / splitters
├── workflows/    # 业务流程引擎（文档审批、问题升级示例）
├── chainlit_app/ # Chainlit 前端：app.py 主入口 + api_client.py + auth.py + chat_handler.py + document_handler.py + chainlit.md
└── main.py       # FastAPI 入口，挂载 API 路由
```

### 关键依赖链（改代码前先看这条链路）

```
上传文件 → app/api/documents.py
        → app/rag/pipeline.process_document()   # 解析→分块→向量化索引
        → app/rag/vector_store.add_documents_to_store()  # 写入单一 Chroma collection

提问(SSE流) → app/api/conversations.py: query_conversation_stream
            → app/rag/chain.query_rag_stream()  # 检索 + 生成
            → app/rag/vector_store.similarity_search()   # 从 Chroma 检索
            → app/rag/llms.get_llm()            # OpenAI/Ollama/test
```

### 两点最容易踩的坑

1. **前后端端口/地址依赖**：Chainlit 前端通过 httpx 调 FastAPI，`BASE_URL` 在 `app/chainlit_app/api_client.py` 唯一定义。改动端口要同步。

2. **对话状态的持久化方式**：
   - 普通查询 `POST /api/conversations/{id}/query`：同步保存消息。
   - 流式查询 `.../query/stream`：SSE (`text/event-stream`) 逐 token 返回，消息靠 `BackgroundTasks` 在流结束后异步入库。SSE 协议在 `app/chainlit_app/chat_handler.py` 的 `handle_query` 中实现（`resp.aiter_lines()` 解析 `data: ` 前缀事件）。

### 会话摘要机制（多轮对话）

`app/api/conversations.py` 里 `RECENT_ROUNDS=5`：长对话只保留最近 5 轮消息进 prompt，更早的用 LLM 生成的 `summary` 代替；每满 `SUMMARY_INTERVAL=10` 条消息后台重新生成摘要。

### 认证

JWT（python-jose）+ bcrypt，RBAC 三角色 `admin`/`editor`/`viewer`（启动时 `app/database.py::_seed_roles` 播种）。`app/dependencies.py` 提供 `get_current_user` / `require_roles(*roles)` / `optional_current_user`。Chainlit 侧 `app/chainlit_app/auth.py` 自动登录，`ApiClient`（`app/chainlit_app/api_client.py`）自动注入 Bearer token 并在 401 时用 refresh token 重试一次。

### 已知技术债 / 未完成项

- **生产部署注意**：gunicorn（`-k uvicorn.workers.UvicornWorker`）在部分 gunicorn/uvicorn 版本组合下会 `Worker failed to boot / code 3`，已验证**用单进程 `uvicorn` 后台跑最稳**（命令见上「常用命令」）；多进程/自启用 systemd 前需先核对 gunicorn 与 uvicorn 版本。
- `app/rag/llms.py`、`embeddings.py` 里 `provider == "local"` 分支实际未启用（配置枚举只允许 `openai`/`ollama`/`test`）。
