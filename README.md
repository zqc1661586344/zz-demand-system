# Enterprise RAG System

> **企业级 RAG 文档问答系统** — 基于 FastAPI + LangChain + Gradio 构建

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com/)
[![LangChain](https://img.shields.io/badge/LangChain-1.3+-orange.svg)](https://python.langchain.com/)

## 📋 项目简介

企业级 RAG（Retrieval-Augmented Generation）文档问答系统，支持**多用户多角色**的文档管理、**文档处理管线**（PDF/TXT/MD/DOCX → 分块 → 向量化索引）、以及基于 **RAG 的智能问答**。内置可扩展的业务流程引擎，支持未来升级到 LangGraph 复杂工作流编排。

### 核心功能

- 🔐 **多用户认证**：JWT 无状态认证 + RBAC 三级角色（admin/editor/viewer）
- 📄 **文档管理**：PDF/TXT/Markdown/DOCX 上传 → 自动解析 → 分块 → 向量化索引，所有文档统一检索
- 💬 **RAG 问答**：基于全部文档上下文的智能问答，附带来源引用
- 🔄 **业务流程**：内置文档审批、问题升级等示例流程
- 🎨 **Gradio UI**：纯 Python 构建的现代化 Web 界面
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
EMBEDDING_MODEL=text-embedding-3-small
```

**使用 Ollama 本地部署**：
```ini
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
EMBEDDING_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
```

**使用测试模式（无需外部 API）**：
```bash
# 通过环境变量启动
LLM_PROVIDER=test EMBEDDING_PROVIDER=test uvicorn app.main:app --port 8001
```

### 3. 启动服务

```bash
# 开发模式
uvicorn app.main:app --reload --port 8001

# 生产模式
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

### 4. 访问应用

| 地址 | 用途 |
|------|------|
| http://localhost:8001/ui | **Gradio Web 界面**（主入口） |
| http://localhost:8001/docs | **API 文档**（Swagger UI） |
| http://localhost:8001/redoc | API 文档（ReDoc） |

---

## ⚙️ 配置说明

全部配置通过 `.env` 文件设置，由 `pydantic-settings` 管理。

### 核心配置项

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DATABASE_URL` | 否 | `sqlite:///./data/app.db` | 数据库连接（生产建议 PostgreSQL） |
| `JWT_SECRET_KEY` | **生产必填** | `dev-secret-key-...` | JWT 签名密钥（生产环境请更换为随机字符串） |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | 否 | `30` | Access Token 有效期（分钟） |
| `LLM_PROVIDER` | 否 | `openai` | LLM 供应商：`openai` / `ollama` / `test` |
| `LLM_API_KEY` | OpenAI 时必填 | — | API Key |
| `LLM_API_BASE` | 否 | `https://api.openai.com/v1` | API 地址（支持 DeepSeek/通义千问等兼容服务） |
| `CHROMA_PERSIST_DIR` | 否 | `./data/chroma` | 向量数据库持久化路径 |
| `CHUNK_SIZE` | 否 | `800` | 文本分块大小（字符数） |
| `CHUNK_OVERLAP` | 否 | `150` | 分块重叠（字符数） |
| `MAX_UPLOAD_SIZE_MB` | 否 | `50` | 单文件最大上传大小 |

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

### 2. 迁移到 PostgreSQL

```bash
# 安装 PostgreSQL 依赖
uv sync --extra postgres

# 更新 .env
DATABASE_URL=postgresql://user:password@host:5432/rag_db

# 运行迁移
alembic upgrade head
```

### 3. 使用 Nginx 反向代理

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

```ini
[Unit]
Description=Enterprise RAG System
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

---

## 🧪 测试

### 端到端测试

```bash
# 1. 启动服务（测试模式）
LLM_PROVIDER=test EMBEDDING_PROVIDER=test uvicorn app.main:app --port 8001 &

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
├── api/           # FastAPI 路由层
├── models/        # SQLAlchemy ORM 模型
├── schemas/       # Pydantic 请求/响应模型
├── services/      # 业务逻辑层
├── rag/           # RAG 核心（嵌入/分块/向量库/管线/链）
├── workflows/     # 业务流程引擎
└── ui/            # Gradio 前端

docs/              # 详细架构文档
├── architecture.md # 技术方案文档
```

详细架构设计见 [docs/architecture.md](docs/architecture.md)。

---

## 🔧 技术栈

| 类别 | 技术 |
|------|------|
| **Web 框架** | FastAPI 0.115+ |
| **UI 框架** | Gradio 5.x |
| **ORM** | SQLAlchemy 2.0+ |
| **数据库迁移** | Alembic |
| **向量数据库** | Chroma |
| **RAG 框架** | LangChain 1.3+ |
| **认证** | JWT（python-jose）+ bcrypt |
| **文档解析** | PyPDF, python-docx |
| **包管理** | uv |

---

## 📈 未来规划

- [ ] 流式输出（SSE）：提升 RAG 回答的用户体验
- [ ] OpenAPI 兼容第 3 方供应商（DeepSeek、通义千问、智谱等）预置配置
- [ ] LangGraph 工作流引擎：支持复杂 DAG 流程编排
- [ ] 批量文档导入（文件夹上传）
- [ ] 文档预览与在线编辑
- [ ] 检索测试工具：查看给定问题的原始检索结果
- [ ] 更多测试覆盖（单元测试 / 集成测试）
- [ ] 国际化（i18n）支持

---

## 📝 License

MIT