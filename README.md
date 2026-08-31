# Enterprise RAG System

> **企业级 RAG 文档问答系统** — 基于 FastAPI + LangChain + Streamlit + PostgreSQL(PGVector) 构建

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-1.3+-orange.svg)](https://python.langchain.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.40+-red.svg)](https://streamlit.io/)

## 📋 项目简介

企业级 RAG（Retrieval-Augmented Generation）文档问答系统，支持**多用户多角色**的文档管理、**文档处理管线**（PDF/TXT/MD/DOCX → 分块 → 向量化索引 + 稀疏关键词索引）、以及三种检索模式（纯向量 / MMR 多样性 / Hybrid 双路召回 + RRF 融合）的**智能问答**。内置可扩展的业务流程引擎，支持未来升级到 LangGraph 复杂工作流编排。

### 核心功能

- 🔐 **多用户认证**：JWT 无状态认证 + RBAC 三级角色（admin/editor/viewer）
- 📄 **文档管理**：PDF/TXT/Markdown/DOCX 上传 → 自动解析 → 分块 → 向量化索引，所有文档统一检索
- 💬 **RAG 问答**：基于全部文档上下文的智能问答，附带来源引用
- 🔄 **业务流程**：内置文档审批、问题升级等示例流程
- 🎨 **Streamlit UI**：现代化 Web 界面，对话页支持 SSE 流式输出
- 🔌 **多 LLM 支持**：OpenAI 兼容 API / Ollama 本地部署 / 测试模式

---

## 🚀 快速开始

### 前置要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)（推荐包管理器）

### 1. 安装

```bash
# 克隆项目
git clone <repo-url> zz-demand-system
cd zz-demand-system

# 安装依赖
uv sync
```

### 2. 配置

```bash
# 创建环境变量文件
cp .env.example .env
```

编辑 `.env`，根据你的 LLM 供应商配置：

**使用 OpenAI 兼容 API（推荐）**：
```ini
LLM_PROVIDER=openai
LLM_API_KEY=sk-your-api-key-here
LLM_API_BASE=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=BAAI/bge-m3
```

**使用 Ollama 本地部署**：
```ini
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=BAAI/bge-m3
```

**使用测试模式（无需外部 API）**：
```bash
# 通过环境变量启动
LLM_PROVIDER=test EMBEDDING_PROVIDER=test .venv/bin/uvicorn app.main:app --port 8001
```

### 3. 启动服务

```bash
# 开发模式：后端 + 前端
bash start.sh
```

> `start.sh` 会同时启动：
> - FastAPI 后端（端口 8001）
> - Streamlit 前端（端口 8002）
>
> 按 `Ctrl+C` 同时停止两个进程。

### 4. 访问应用

| 地址 | 用途 |
|------|------|
| http://localhost:8002 | **Streamlit Web 界面**（主入口） |
| http://localhost:8001/docs | **API 文档**（Swagger UI） |
| http://localhost:8001/redoc | API 文档（ReDoc） |

> 也可以单独启动后端：
> ```bash
> .venv/bin/uvicorn app.main:app --reload --port 8001
> ```

---

## ⚙️ 配置说明

全部配置通过 `.env` 文件设置，由 `pydantic-settings` 管理。

### 核心配置项

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DATABASE_URL` | 否 | `sqlite:///./data/app.db` | 数据库连接（生产建议 PostgreSQL） |
| `JWT_SECRET_KEY` | **生产必填** | `dev-secret-key-...` | JWT 签名密钥（生产环境请更换为随机字符串） |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 否 | `30`（建议设 `525600`） | Access Token 有效期（分钟，长空闲场景建议 1 年） |
| `LLM_PROVIDER` | 否 | `openai` | LLM 供应商：`openai` / `ollama` / `test` |
| `LLM_API_KEY` | OpenAI 时必填 | — | API Key |
| `LLM_API_BASE` | 否 | `https://api.openai.com/v1` | API 地址（支持 DeepSeek/通义千问等兼容服务） |
| `LLM_MODEL` | 否 | `gpt-4o-mini` | LLM 模型名 |
| `EMBEDDING_PROVIDER` | 否 | `openai` | Embedding 供应商：`openai` / `ollama` / `test` |
| `EMBEDDING_MODEL` | 否 | `BAAI/bge-m3` | Embedding 模型（1024 维，cosine 距离） |
| `OLLAMA_BASE_URL` | Ollama 时必填 | `http://localhost:11434` | Ollama 服务地址 |
| `OLLAMA_MODEL` | 否 | `qwen2.5:7b` | Ollama 使用的 LLM 模型 |
| `OLLAMA_EMBEDDING_MODEL` | 否 | `BAAI/bge-m3` | Ollama 使用的 Embedding 模型 |
| `VECTOR_STORE_URL` | 否 | ` ` | PGVector数据库 |
| `CHUNK_SIZE` | 否 | `800` | 文本分块大小（字符数） |
| `CHUNK_OVERLAP` | 否 | `150` | 分块重叠（字符数） |
| `MAX_UPLOAD_SIZE_MB` | 否 | `50` | 单文件最大上传大小 |
| `RAG_SEARCH_TYPE` | 否 | `hybrid` | 检索模式：`similarity`（纯向量）/ `mmr`（多样性）/ `hybrid`（BM25+向量+RRF 融合） |
| `RAG_HYBRID_ALPHA` | 否 | `0.5` | Hybrid 中稠密 vs 稀疏权重（0=纯 BM25，1=纯向量） |
| `RAG_SPARSE_BACKEND` | 否 | `pg_tsvector` | 稀疏检索后端：`pg_tsvector`（PG 原生 tsvector+GIN，增量零内存，默认）/ `bm25_memory`（进程内 BM25，回退） |
| `RAG_SPARSE_MIN_RANK` | 否 | `0.1` | pg_tsvector 稀疏命中的 ts_rank 下限：低于此值视为弱命中，在 hybrid 稀疏分支被过滤 |
| `RAG_HYBRID_MIN_SPREAD` | 否 | `0.015` | Hybrid 模式的分数离散度阈值：top1-top2 低于此值回退自由聊天 |
| `RAG_MIN_SCORE` | 否 | `0.4` | 纯向量模式的相关性分数阈值（低于此值回退自由聊天） |
| `RAG_RERANK_ENABLED` | 否 | `false` | 是否启用 bge-reranker 交叉编码器重排（需 transformers + torch） |
| `RAG_RERANK_MODEL` | 否 | `BAAI/bge-reranker-v2-m3` | 重排器模型名 |
| `RAG_RERANK_TOP_N` | 否 | `5` | 重排后保留的 top-N 结果 |

完整配置项见 [`app/config.py`](app/config.py)。

---

## 📚 使用指南

### 角色权限

| 角色 | 权限 |
|------|------|
| **admin** | 全部权限：管理用户、管理文档、管理流程 |
| **editor** | 上传/删除文档、发起对话 |
| **viewer** | 查看文档、发起对话（只读） |

> 新注册用户自动分配 `viewer` 角色，由 admin 在"管理"页面调整。

### 首次登录 & 首个 admin

前端默认展示**登录/注册页**，不再硬编码自动登录。注册用户默认 `viewer`；首个 `admin` 需手动种出：

> 项目默认 `SEED_DEMO_USER=false`，**不会自动创建任何 admin 账号**（杜绝硬编码超管凭据入库）。全新库拿第一个 admin 的方式：

```bash
# 临时开启种子开关，启动一次种出 admin/admin123
SEED_DEMO_USER=true bash start.sh

# → 用 admin / admin123 登录
# → 立即改密：POST /api/auth/change-password（见下）或在 UI 引导（若已提供）
curl -X POST http://localhost:8001/api/auth/change-password \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password":"admin123","new_password":"<强密码>"}'

# → 重启（不带 SEED_DEMO_USER），开关关闭后不再重建，后续凭新密码登录
bash start.sh
```

> 生产环境无论开关如何都**不**种 admin（`main.py::_seed_demo_user` 有生产守卫）。

### 工作流程

```mermaid
flowchart LR
    A[注册 / 登录] --> B[上传文档]
    B --> C[等待索引完成]
    C --> D[新建对话]
    D --> E[提问]
    E --> F[查看回答与来源]
```

### 支持的文件格式

| 格式 | 扩展名 | MIME 类型 |
|------|--------|-----------|
| PDF | `.pdf` | `application/pdf` |
| 纯文本 | `.txt` | `text/plain` |
| Markdown | `.md` | `text/markdown` |
| Word 文档 | `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` |
| CSV | `.csv` | `text/csv` |
| HTML | `.html` | `text/html` |
| Excel | `.xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| PowerPoint | `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| TOML | `.toml` | `application/toml` |

---

## 🔎 RAG 检索流程

系统采用 **Hybrid RAG**：稠密向量（PGVector + bge-m3）与稀疏关键词（PG tsvector + jieba 中文分词，旧版/回退用内存 BM25）双路召回，RRF 融合排序，可选交叉编码器重排。

```mermaid
flowchart TD
    Q["用户提问"] --> H["组装对话历史（最近 20 轮 + 更早摘要）"]
    H --> D{"检索模式 RAG_SEARCH_TYPE"}
    D -->|hybrid| DH["PGVector 稠密检索<br/>bge-m3 cosine"]
    D -->|hybrid| SH["稀疏检索<br/>PG tsvector + jieba 分词<br/>或内存 BM25"]
    DH --> F["RRF 融合"]
    SH --> F
    F --> R["可选 bge-reranker 重排"]
    D -->|similarity| SV["纯向量检索<br/>分数阈值过滤 ≥0.4"]
    D -->|mmr| MM["MMR 多样性检索 λ=0.7"]
    R --> G{"相关性判定"}
    SV --> G
    MM --> G
    G -->|命中| C["format_context + RAG_PROMPT"]
    C --> L["LLM 生成"]
    L --> A["SSE 流式输出 + 来源引用"]
    G -->|未命中| FC["自由聊天<br/>纯 LLM 自身知识回答"]
    FC --> A
```

**核心设计要点**：
- **三种检索模式**：`hybrid`（默认）/ `similarity` / `mmr`，通过 `RAG_SEARCH_TYPE` 切换。
- **稀疏后端可切换**：默认 `RAG_SPARSE_BACKEND=pg_tsvector`（PG 原生全文检索，增量、零内存驻留，适合大文档量）；旧版 `bm25_memory`（进程内全量 BM25）作为回退保留。
- **无命中回退**：hybrid 用「绝对分数 + 分数离散度」双判据；三者检索为空或判定不相关时，回退到`自由聊天`（前缀标注 *「当前已有文档中找不到答案…」*，不附带来源）。
- **文档生命周期**：上传时对每个 chunk 用 jieba 分词写入 `search_text`，删除时随行删除——增量维护，无需全量重建索引；旧库启动时由 `ensure_fts_index()` 自动补列 + 建 GIN 索引。

> 📄 **详细流程**：完整的多路检索结构、RRF 融合公式、重排器配置、FAQ 见 [`docs/RAG.md`](docs/RAG.md)；端到端架构见 [`docs/architecture.md`](docs/architecture.md)。

---

## 🔌 API 概览

### 认证

```bash
# 注册
curl -X POST http://localhost:8001/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"Secure@123","email":"alice@example.com","full_name":"Alice"}'

# 登录
curl -X POST http://localhost:8001/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"Secure@123"}'
# → 返回 {access_token, refresh_token}
```

### 文档

```bash
# 上传文档（multipart）
curl -X POST http://localhost:8001/api/documents/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@report.pdf"
# → 返回 {id, filename, status: "pending"}

# 查看文档状态
curl http://localhost:8001/api/documents/<doc-id> \
  -H "Authorization: Bearer <token>"
# → status: "indexed" 时表示处理完成

# 列出所有文档
curl http://localhost:8001/api/documents \
  -H "Authorization: Bearer <token>"
```

### RAG 问答

```bash
# 创建对话
curl -X POST http://localhost:8001/api/conversations \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"title":"产品咨询"}'

# 提问（RAG 查询）
curl -X POST http://localhost:8001/api/conversations/<conv-id>/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"query":"产品的核心功能有哪些？"}'
# → 返回 {answer, sources: [{filename, page}]}
```

完整 API 端点清单见 [docs/architecture.md](docs/architecture.md#6-api-端点设计) 或启动后访问 `/docs`。

---

## 🐳 生产部署

### 1. 更换密钥

生产环境必须更换 `JWT_SECRET_KEY`，可以使用以下命令生成：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 2. 启动服务（后台运行）

**方式一：使用 start.sh（推荐）**
```bash
bash start.sh
```
内部使用 `nohup` 同时启动后端和前端，日志写入 `/tmp/rag.log`。

**方式二：分别启动**

```bash
# 后端
nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 > /tmp/rag.log 2>&1 &

# 前端
nohup .venv/bin/streamlit run app/streamlit_app/app.py --server.port 8002 --server.headless true > /tmp/streamlit.log 2>&1 &

# 验证
curl http://localhost:8001/api/health
# → {"status":"ok","version":"0.1.0"}

# 看日志
tail -f /tmp/rag.log
# 停止（用 pgrep 查到的 PID）
pgrep -af uvicorn
pgrep -af streamlit
kill <PID>
```

> 说明：多进程建议用 `gunicorn`（`gunicorn app.main:app -k uvicorn.workers.UvicornWorker ...`）；
> 需要开机自启/崩了自动重启，见下方 systemd 一节。若 gunicorn 引导 worker 报 `Worker failed to boot / code 3`，
> 多为 gunicorn 与 uvicorn 版本不兼容，直接用上面的单进程 uvicorn 最稳妥。

### 3. 迁移到 PostgreSQL

```bash
# 安装 PostgreSQL 依赖
uv sync --extra postgres

# 更新 .env
DATABASE_URL=postgresql://user:password@host:5432/rag_db

# 运行迁移
alembic upgrade head
```

### 5. 使用 Nginx 反向代理

```nginx
server {
    listen 443 ssl;
    server_name rag.example.com;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### 4. 使用 systemd 管理服务

**后端服务** `/etc/systemd/system/rag-backend.service`：

```ini
[Unit]
Description=RAG System Backend
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/zz-demand-system
ExecStart=/opt/zz-demand-system/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=5
Environment=APP_DEBUG=false

[Install]
WantedBy=multi-user.target
```

**前端服务** `/etc/systemd/system/rag-frontend.service`：

```ini
[Unit]
Description=RAG System Frontend (Streamlit)
After=network.target
Requires=rag-backend.service

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/zz-demand-system
ExecStart=/opt/zz-demand-system/.venv/bin/streamlit run app/streamlit_app/app.py --server.port 8002 --server.headless true
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## 🧪 测试

### 端到端测试

```bash
# 1. 启动服务（测试模式）
LLM_PROVIDER=test EMBEDDING_PROVIDER=test .venv/bin/uvicorn app.main:app --port 8001 &

# 2. 运行测试
python test_e2e.py
```

测试覆盖以下场景：
- 用户注册 → 登录 → 获取个人信息
- 上传文档 → 索引完成
- 创建对话 → RAG 查询 → 获取消息
- 健康检查

---

## 🏗️ 项目架构

```
app/
├── api/             # FastAPI 路由层
├── models/          # SQLAlchemy ORM 模型
├── schemas/         # Pydantic 请求/响应模型
├── services/        # 业务逻辑层
├── rag/             # RAG 核心（嵌入/分块/向量库/管线/链/Hybrid 检索）
├── workflows/       # 业务流程引擎
└── streamlit_app/   # Streamlit 前端

docs/               # 详细架构文档
├── architecture.md  # 技术方案文档
└── RAG.md           # RAG 检索流程详解（含 mermaid 图）
```

详细架构设计见 [docs/architecture.md](docs/architecture.md)。

---

## 🔧 技术栈

| 类别 | 技术 |
|------|------|
| **Web 框架** | FastAPI 0.115+ |
| **UI 框架** | Streamlit 1.40+ |
| **ORM** | SQLAlchemy 2.0+ |
| **数据库迁移** | Alembic |
| **向量数据库** | PGVector（bge-m3 1024d cosine） |
| **RAG 框架** | LangChain 1.3+ |
| **混合检索** | PG tsvector（默认稀疏后端）/ rank_bm25（内存 BM25 回退）+ jieba（中文分词） |
| **重排器** | BAAI/bge-reranker-v2-m3（可选，需 transformers + torch） |
| **认证** | JWT（python-jose）+ bcrypt |
| **文档解析** | PyPDF, python-docx（docx2txt）, markdown |
| **包管理** | uv |

---

## 📈 未来规划

- [x] 流式输出（SSE）：提升 RAG 回答的用户体验（已基于 Streamlit 实现）
- [x] OpenAPI 兼容第 3 方供应商（DeepSeek、通义千问、智谱等）预置配置（通过 `LLM_API_BASE` 自由切换）
- [ ] LangGraph 工作流引擎：支持复杂 DAG 流程编排
- [ ] 批量文档导入（文件夹上传）
- [ ] 文档预览与在线编辑
- [ ] 检索测试工具：查看给定问题的原始检索结果
- [ ] 更多测试覆盖（单元测试 / 集成测试）
- [ ] 国际化（i18n）支持

---

## 📝 License

MIT