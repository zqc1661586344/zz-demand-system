# 企业级 RAG 系统 — 技术方案文档

> **版本**: 0.1.0  
> **最后更新**: 2026-08-14  
> **项目状态**: Phase 1–4 核心功能已完成，E2E 验证通过

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术选型](#2-技术选型)
3. [系统架构](#3-系统架构)
4. [项目目录结构](#4-项目目录结构)
5. [数据库 Schema 设计](#5-数据库-schema-设计)
6. [API 端点设计](#6-api-端点设计)
7. [认证流程](#7-认证流程)
8. [文档处理管线](#8-文档处理管线)
9. [RAG 问答流程](#9-rag-问答流程)
10. [Gradio 前端设计](#10-gradio-前端设计)
11. [业务流程引擎](#11-业务流程引擎)
12. [迁移路径](#12-迁移路径)
13. [部署与运维](#13-部署与运维)

---

## 1. 项目概述

### 1.1 目标

构建一套用于企业内部文档的 RAG（Retrieval-Augmented Generation）问答系统，支持：

- **多用户多角色**：基于角色的访问控制（RBAC），支持管理员、编辑者、查看者三级角色
- **文档管理**：统一文档池管理，所有文档共享一个向量索引
- **文档生命周期**：上传 → 解析 → 分块 → 向量化 → 索引的完整管线
- **RAG 问答**：基于全部文档上下文的高质量 LLM 问答，附带来源引用
- **业务流程**：内置可扩展的业务流程引擎，支持文档审批、问题升级等场景
- **可扩展架构**：LLM 供应商可配置（OpenAI 兼容 API / Ollama 本地部署），数据库可升级（SQLite → PostgreSQL）

### 1.2 适用范围

| 维度 | 范围 |
|------|------|
| 文档规模 | 1K–10K 份文档（中型文档库） |
| 用户规模 | 数十至数百名内部用户 |
| 部署环境 | 内网服务器或云主机 |
| 文档格式 | PDF、TXT、Markdown、DOCX |

---

## 2. 技术选型

| 决策项 | 选型 | 理由 |
|--------|------|------|
| **Web 框架** | FastAPI 0.115+ | 高性能异步支持、自动 OpenAPI 文档、依赖注入体系 |
| **前端框架** | Gradio 5.x | 纯 Python 快速构建 UI，后端开发者零前端成本 |
| **ORM** | SQLAlchemy 2.0+ | 成熟稳定、支持异步、迁移友好 |
| **数据库迁移** | Alembic 1.13+ | 与 SQLAlchemy 原生集成 |
| **数据库（开发）** | SQLite | 零配置，适合开发阶段 |
| **数据库（生产）** | PostgreSQL（可选依赖） | 生产环境推荐，通过 `DATABASE_URL` 一键切换 |
| **认证** | JWT（python-jose） | 无状态认证，适合 API + 前端分离架构 |
| **密码哈希** | bcrypt | 业界标准密码哈希算法 |
| **向量数据库** | Chroma（langchain-chroma） | 轻量级、零额外部署、文件持久化 |
| **RAG 框架** | LangChain 1.3+ | 生态丰富，支持多种 LLM 和向量库 |
| **LLM 供应商** | OpenAI 兼容 API / Ollama | 配置化切换，支持国内厂商（DeepSeek、通义千问等） |
| **文档解析** | PyPDF / python-docx | 覆盖主流办公文档格式 |
| **包管理** | uv | 现代 Python 包管理，速度极快 |
| **异步任务** | FastAPI BackgroundTasks | 轻量级异步任务，适合文档处理场景 |

---

## 3. 系统架构

### 3.1 整体架构图

```
┌───────────────────────────────────────────────────────────┐
│  uvicorn (单进程, 端口 8000)                               │
│                                                           │
│  ┌────────────────────────────────────────────────────┐   │
│  │  Gradio UI (mount 到 FastAPI, 路径 /ui)             │   │
│  │  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────┐  │   │
│  │  │ 问答页   │ │ 文档页 │ │ 流程页 │ │ 管理页  │  │   │
│  │  └────┬─────┘ └────┬─────┘ └───┬────┘ └───┬────┘  │   │
│  │       └──────────┬──┴───────────┴───────────┘       │   │
│  │                  │ api_client.py                     │   │
│  │                  │ (httpx + JWT)                     │   │
│  └──────────────────┼──────────────────────────────────┘   │
│                     │ HTTP 内部调用 (localhost:8000)        │
│  ┌──────────────────┼──────────────────────────────────┐   │
│  │  FastAPI 路由层   │                                  │   │
│  │  ┌───────────────┴──────────────┐                   │   │
│  │  │ JWT 中间件 (验证所有请求)      │                   │   │
│  │  └───────────────┬──────────────┘                   │   │
│  │  ┌───────────────┴──────────────┐                   │   │
│  │  │ 路由: /api/auth/*            │                   │   │
│  │  │       /api/documents/* │                   │   │
│  │  │       /api/conversations/* │                   │   │
│  │  │       /api/documents/*       │                   │   │
│  │  │       /api/conversations/*   │                   │   │
│  │  └───────────────┬──────────────┘                   │   │
│  │  ┌───────────────┴──────────────┐                   │   │
│  │  │  服务层 (services/*)          │                   │   │
│  │  └───┬──────┬───────┬──────────┘                   │   │
│  └──────┼──────┼───────┼──────────────────────────────┘   │
│         │      │       │                                   │
│  ┌──────┴┐ ┌───┴───┐ ┌┴───────────┐                      │
│  │ RAG   │ │Workfl │ │ SQLAlchemy │                      │
│  │ Engine│ │Engine │ │ + Chroma   │                      │
│  └───────┘ └───────┘ └────────────┘                      │
└───────────────────────────────────────────────────────────┘
```

### 3.2 架构原则

1. **分层架构**：API 路由层 → 服务层 → 数据访问层（ORM/RAG），层间通过依赖注入解耦
2. **同进程部署**：Gradio 作为子应用挂载在 FastAPI 中（`app.mount("/ui", gr_app)`），避免跨进程通信开销
3. **配置驱动**：所有环境敏感参数通过 `.env` + `pydantic-settings` 管理，支持 `test` 模式用于测试
4. **异步处理**：文档解析和向量化使用 `BackgroundTasks` 异步执行，不阻塞 API 响应
5. **测试友好**：内置 `FakeEmbeddings` 和 `FakeLLM`（RunnableLambda），支持无外部依赖的 E2E 测试

### 3.3 关键架构决策

| 决策 | 说明 |
|------|------|
| Gradio 挂载方式 | Gradio 作为 FastAPI 子应用同进程部署，通过 httpx 内部调用自身 API |
| Chroma 持久化 | 统一使用一个 Chroma collection（`documents`），所有文档共享检索 |
| Token 管理 | Gradio 前端用 `gr.State()` 存储 JWT，支持自动刷新 |
| 测试模式 | `LLM_PROVIDER=test` 和 `EMBEDDING_PROVIDER=test` 启用 Mock 实现 |

---

## 4. 项目目录结构

```
zz-demand-system/
│
├── .env                        # 环境变量（不提交 Git）
├── .env.example                # 环境变量模板
├── .gitignore
├── README.md                   # 项目说明文档
├── pyproject.toml              # uv 项目配置与依赖
├── alembic.ini                 # Alembic 迁移配置
├── test_e2e.py                 # 端到端冒烟测试
│
├── app/                        # 应用核心代码
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口 + Gradio 挂载
│   ├── config.py               # pydantic-settings 配置类
│   ├── database.py             # SQLAlchemy 引擎、会话、种子数据
│   ├── dependencies.py         # FastAPI 依赖注入（当前用户、角色检查）
│   │
│   ├── models/                 # SQLAlchemy ORM 模型
│   │   ├── user.py             # User, Role, UserRole
│   │   ├── document.py         # Document, DocumentChunk
│   │   ├── conversation.py     # Conversation, Message
│   │   └── workflow.py         # WorkflowDefinition, Instance, Step
│   │
│   ├── schemas/                # Pydantic 请求/响应模型
│   │   ├── auth.py             # 认证相关 schema
│   │   ├── user.py             # 用户管理 schema
│   │   ├── document.py         # 文档 schema
│   │   ├── conversation.py     # 对话 schema
│   │   └── workflow.py         # 工作流 schema
│   │
│   ├── api/                    # FastAPI 路由层
│   │   ├── router.py           # 聚合所有路由
│   │   ├── auth.py             # 注册/登录/刷新/个人信息
│   │   ├── users.py            # 用户管理（管理员）
│   │   ├── documents.py        # 文档上传/列表/删除/重新处理
│   │   ├── conversations.py    # 对话管理 + RAG 查询
│   │   └── workflows.py        # 工作流定义与实例管理
│   │
│   ├── services/               # 业务逻辑层
│   │   ├── auth_service.py     # JWT 签发/验证、密码哈希
│   │   ├── user_service.py     # 用户 CRUD
│   │   ├── knowledge_base_service.py   # [已移除]
│   │   ├── document_service.py
│   │   ├── conversation_service.py
│   │   └── workflow_service.py
│   │
│   ├── rag/                    # RAG 核心逻辑
│   │   ├── embeddings.py       # 嵌入模型配置（OpenAI/Ollama/Test）
│   │   ├── splitters.py        # 文本分块策略
│   │   ├── vector_store.py     # Chroma 向量库封装
│   │   ├── pipeline.py         # 文档处理管线（解析→分块→索引）
│   │   └── chain.py            # RAG 问答链
│   │
│   ├── workflows/              # 业务流程引擎
│   │   ├── base.py             # 抽象基类
│   │   ├── registry.py         # 流程注册表
│   │   └── examples/           # 示例流程
│   │       ├── doc_review.py   # 文档审批流程
│   │       └── qa_escalation.py # 问题升级流程
│   │
│   └── ui/                     # Gradio 前端
│       ├── app.py              # Gradio 应用入口
│       ├── api_client.py       # 封装 HTTP 调用
│       ├── auth_helpers.py     # JWT 状态管理
│       ├── components/         # 公共组件
│       │   └── layout.py       # 侧边栏/导航布局
│       └── pages/              # 页面模块
│           ├── login.py        # 登录/注册页
│           ├── chat.py         # RAG 问答页
│           ├── knowledge_bases.py   # [已移除]
│           ├── documents.py
│           ├── admin.py        # 用户管理（管理员）
│           └── workflows.py    # 业务流程页
│
├── alembic/                    # 数据库迁移
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 012acc6c2410_init.py
│
├── data/                       # 运行时数据（gitignored）
│   ├── chroma/                 # Chroma 向量库持久化
│   ├── uploads/                # 上传文档存储
│   └── app.db                  # SQLite 数据库
│
└── tests/                      # 测试（待完善）
    ├── test_api/
    ├── test_rag/
    └── test_services/
```

---

## 5. 数据库 Schema 设计

### 5.1 ER 图（文本描述）

```
users ──── user_roles ──── roles
  │
  ├─── documents（上传者关系）
  ├─── conversations（创建者关系）
  └─── workflow_definitions（创建者关系）
        └─── workflow_instances
              └─── workflow_steps

documents ──── document_chunks（元数据记录）
```

### 5.2 表结构

#### users（用户表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID |
| username | VARCHAR(100) | UNIQUE, NOT NULL, INDEX | 用户名 |
| email | VARCHAR(255) | UNIQUE, NOT NULL | 邮箱 |
| hashed_password | VARCHAR(255) | NOT NULL | bcrypt 哈希密码 |
| full_name | VARCHAR(255) | nullable | 全名 |
| is_active | BOOLEAN | DEFAULT TRUE | 是否激活 |
| is_superuser | BOOLEAN | DEFAULT FALSE | 是否超级用户 |
| created_at | DATETIME | | 创建时间 |
| updated_at | DATETIME | | 更新时间 |

#### roles（角色表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID |
| name | VARCHAR(50) | UNIQUE, NOT NULL, INDEX | admin / editor / viewer |
| description | TEXT | nullable | 角色描述 |
| created_at | DATETIME | | 创建时间 |

**预置角色**：`admin`（系统管理员）、`editor`（编辑者）、`viewer`（查看者），应用启动时自动通过 `_seed_roles()` 创建。

#### user_roles（用户-角色关联表）

| 字段 | 类型 | 约束 |
|------|------|------|
| user_id | VARCHAR(36) | PK, FK→users.id (CASCADE) |
| role_id | VARCHAR(36) | PK, FK→roles.id (CASCADE) |

#### documents（文档表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID |
| filename | VARCHAR(255) | NOT NULL | 存储文件名 |
| original_filename | VARCHAR(255) | NOT NULL | 原始文件名 |
| file_path | VARCHAR(500) | NOT NULL | 存储路径 |
| file_size | INTEGER | nullable | 文件大小（字节） |
| mime_type | VARCHAR(100) | nullable | MIME 类型 |
| status | VARCHAR(50) | NOT NULL, INDEX | pending/processing/indexed/failed |
| chunk_count | INTEGER | DEFAULT 0 | 分块数量 |
| error_message | TEXT | nullable | 失败原因 |
| uploaded_by | VARCHAR(36) | FK→users.id | 上传者 |
| created_at | DATETIME | | |
| updated_at | DATETIME | | |
#### conversations（对话表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID |
| title | VARCHAR(255) | nullable | 对话标题 |
| created_by | VARCHAR(36) | FK→users.id | 创建者 |
| created_at | DATETIME | | |
| updated_at | DATETIME | | |

#### messages（消息表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID |
| conversation_id | VARCHAR(36) | FK→conversations.id (CASCADE) | |
| role | VARCHAR(50) | NOT NULL | user / assistant |
| content | TEXT | NOT NULL | 消息内容 |
| sources | TEXT | nullable | JSON 格式的引用来源 |
| created_at | DATETIME | | |

#### workflow_definitions（工作流定义表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID |
| name | VARCHAR(255) | NOT NULL, INDEX | 流程名称 |
| description | TEXT | nullable | 描述 |
| config | TEXT | nullable | JSON 格式配置 |
| version | INTEGER | DEFAULT 1 | 版本号 |
| is_active | VARCHAR(5) | DEFAULT 'true' | 是否启用 |
| created_by | VARCHAR(36) | FK→users.id | 创建者 |
| created_at | DATETIME | | |
| updated_at | DATETIME | | |

#### workflow_instances（工作流实例表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID |
| definition_id | VARCHAR(36) | FK→workflow_definitions.id (CASCADE) | |
| status | VARCHAR(50) | NOT NULL, INDEX | pending/running/completed/failed |
| input_data | TEXT | nullable | JSON 格式输入数据 |
| output_data | TEXT | nullable | JSON 格式输出数据 |
| initiated_by | VARCHAR(36) | FK→users.id | 发起者 |
| created_at | DATETIME | | |
| completed_at | DATETIME | nullable | |

#### workflow_steps（工作流步骤表）

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| id | VARCHAR(36) | PK | UUID |
| instance_id | VARCHAR(36) | FK→workflow_instances.id (CASCADE) | |
| step_name | VARCHAR(255) | NOT NULL | 步骤名称 |
| status | VARCHAR(50) | DEFAULT 'pending' | |
| input_data | TEXT | nullable | JSON |
| output_data | TEXT | nullable | JSON |
| error_message | TEXT | nullable | |
| started_at | DATETIME | nullable | |
| completed_at | DATETIME | nullable | |

### 5.3 Chroma Collection 设计

| 属性 | 说明 |
|------|------|
| Collection 命名 | `documents`，所有文档共享一个 Collection |
| Document metadata | `document_id`, `chunk_index`, `filename`, `page` |
| 嵌入维度 | text-embedding-ada-002 (1536) 或 nomic-embed-text (768) |
| 相似度算法 | Cosine similarity（Chroma 默认） |

---

## 6. API 端点设计

### 6.1 认证

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/auth/register` | 公开 | 用户注册，自动分配 viewer 角色 |
| POST | `/api/auth/login` | 公开 | 登录，返回 access + refresh token |
| POST | `/api/auth/refresh` | 公开 | 刷新 token |
| GET | `/api/auth/me` | 已登录 | 获取当前用户信息（含角色） |
| POST | `/api/auth/change-password` | 已登录 | 修改密码 |

### 6.2 用户管理（管理员）

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/users` | admin | 用户列表（分页） |
| GET | `/api/users/{id}` | admin | 用户详情 |
| PUT | `/api/users/{id}` | admin | 编辑用户 |
| DELETE | `/api/users/{id}` | admin | 删除用户 |
| PUT | `/api/users/{id}/roles` | admin | 设置用户角色 |

### 6.3 文档

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/documents/upload` | editor+ | 上传文档（multipart） |
| GET | `/api/documents` | 已登录 | 文档列表 |
| GET | `/api/documents/{id}` | 已登录 | 详情 |
| DELETE | `/api/documents/{id}` | editor+ | 删除 |
| POST | `/api/documents/{id}/reprocess` | editor+ | 重新处理 |

### 6.4 对话

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/conversations` | 已登录 | 创建对话 |
| GET | `/api/conversations` | 已登录 | 对话列表 |
| GET | `/api/conversations/{id}` | 所有者 | 详情 |
| DELETE | `/api/conversations/{id}` | 所有者 | 删除 |
| POST | `/api/conversations/{id}/query` | 所有者 | RAG 问答 |
| GET | `/api/conversations/{id}/messages` | 所有者 | 消息列表 |

### 6.5 工作流

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| POST | `/api/workflows/definitions` | admin | 创建流程定义 |
| GET | `/api/workflows/definitions` | 已登录 | 流程定义列表 |
| GET | `/api/workflows/definitions/{id}` | 已登录 | 详情 |
| POST | `/api/workflows/instances` | 已登录 | 创建实例 |
| GET | `/api/workflows/instances` | 已登录 | 实例列表 |
| GET | `/api/workflows/instances/{id}` | 已登录 | 实例详情 |
| GET | `/api/workflows/instances/{id}/steps` | 已登录 | 步骤详情 |

### 6.6 健康检查

| 方法 | 路径 | 权限 |
|------|------|------|
| GET | `/api/health` | 公开 |

---

## 7. 认证流程

### 7.1 登录流程

```
┌─────────────┐         ┌─────────────┐         ┌──────────┐
│  Gradio UI  │         │   FastAPI   │         │ SQLite   │
└──────┬──────┘         └──────┬──────┘         └────┬─────┘
       │                       │                      │
       │  POST /api/auth/login │                      │
       │  {username, password} │                      │
       ├──────────────────────►│  查询用户 + 验证密码    │
       │                       ├─────────────────────►│
       │                       │◄─────────────────────┤
       │  {access_token,       │                      │
       │   refresh_token,      │                      │
       │   user}               │                      │
       │◄──────────────────────┤                      │
       │                       │                      │
       │  令牌存入 gr.State()   │                      │
       │                       │                      │
       │  POST /api/documents/upload                  │
       │  Authorization: Bearer <token>                │
       ├──────────────────────►│  JWT 中间件验证        │
       │                       │  从中提取 user_id      │
       │                       │  查询用户角色/权限      │
       │                       ├─────────────────────►│
       │                       │◄─────────────────────┤
       │  {documents: [...]}   │                      │
       │◄──────────────────────┤                      │
```

### 7.2 Token 管理

- **Access Token**：有效期 30 分钟（可配置），用于 API 请求认证
- **Refresh Token**：有效期 7 天（可配置），用于自动刷新 Access Token
- **Gradio 端**：`gr.State()` 存储 `access_token`、`refresh_token`、`user_info`
- **自动刷新**：检测 401 响应时自动用 Refresh Token 换取新 Token 对
- **退出登录**：清空 `gr.State()`，浏览器关闭也丢失状态

### 7.3 密码安全

- 使用 **bcrypt** 直接哈希（`bcrypt.hashpw`/`bcrypt.checkpw`），而非 passlib 封装
- bcrypt 版本要求 **5.0.0+**（passlib 1.7.4 不兼容 bcrypt 5.0.0，已弃用 passlib）

---

## 8. 文档处理管线

### 8.1 处理流程

```
                    ┌──────────────────┐
                    │  上传文档         │
                    │  (FastAPI 接收)   │
                    └────────┬─────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  保存到磁盘       │
                    │  data/uploads/   │
                    │  DB status=pending│
                    └────────┬─────────┘
                             │ BackgroundTask
                             ▼
                    ┌──────────────────┐
                    │  ① 文档解析      │
                    │  PyPDFLoader     │ ← PDF
                    │  TextLoader      │ ← TXT/MD
                    │  Docx2txtLoader  │ ← DOCX
                    └────────┬─────────┘
                             │ raw documents
                             ▼
                    ┌──────────────────┐
                    │  ② 文本分块      │
                    │  RecursiveChar   │
                    │  TextSplitter    │
                    │  chunk_size=800  │
                    │  chunk_overlap=150│
                    └────────┬─────────┘
                             │ LangChain chunks
                             ▼
                    ┌──────────────────┐
                    │  ③ 向量嵌入      │
                    │  OpenAI / Ollama │
                    │  / FakeEmbeddings│
                    └────────┬─────────┘
                             │ vectors
                             ▼
                    ┌──────────────────┐
                    │  ④ 存储到 Chroma │
                    │  documents 集合  │
                    │  status=indexed  │
                    │  chunk_count=N   │
                    └──────────────────┘
```

### 8.2 文档格式支持

| 格式 | MIME Type | 解析器 | 特性 |
|------|-----------|--------|------|
| PDF | application/pdf | PyPDFLoader | 逐页加载，支持 page 元数据 |
| TXT | text/plain | TextLoader | UTF-8 编码纯文本 |
| Markdown | text/markdown | TextLoader | 按纯文本处理 |
| DOCX | application/vnd.openxmlformats-officedocument.wordprocessingml.document | Docx2txtLoader | 转换为纯文本 |

### 8.3 分块策略

- **算法**：`RecursiveCharacterTextSplitter`
- **分块大小**：800 字符（可通过 `CHUNK_SIZE` 配置）
- **重叠**：150 字符（可通过 `CHUNK_OVERLAP` 配置）
- **分隔符优先级**：`\n\n` > `\n` > `。` > `.` > `（空格）` > `""`
  - 中英文混合优化：中文句号 `。` 优先级高于英文句号 `.`

---

## 9. RAG 问答流程

### 9.1 完整流程

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│  用户    │   │ Gradio   │   │ FastAPI  │   │ Chroma   │   │  LLM     │
│  浏览器  │   │   UI     │   │ 服务端   │   │ 向量库   │   │ (OpenAI/ │
└────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   │ Ollama)  │
     │              │              │              │         └────┬─────┘
     │  输入问题     │              │              │              │
     ├─────────────►│              │              │              │
     │              │ POST /query  │              │              │
     │              │ {query}      │              │              │
     │              ├────────────►│              │              │
     │              │              │ embed(问题)   │              │
     │              │              ├─────────────►│              │
     │              │              │  相似度搜索   │              │
     │              │              │  (top_k=5)   │              │
     │              │              │◄─────────────┤              │
     │              │              │  相关 chunks  │              │
     │              │              │              │              │
     │              │              │  构建 Prompt  │              │
     │              │              │  (context+Q) │              │
     │              │              ├──────────────┼────────────►│
     │              │              │  LLM 生成回答  │             │
     │              │              │◄──────────────┼─────────────┤
     │              │              │              │              │
     │              │  返回结果    │              │              │
     │              │  {answer,   │              │              │
     │              │   sources,  │              │              │
     │              │   chunks}   │              │              │
     │              │◄────────────┤              │              │
     │  展示答案    │              │              │              │
     │  + 来源     │              │              │              │
     │◄─────────────┤              │              │              │
```

### 9.2 提示词模板

```
系统: You are a helpful assistant for internal knowledge base queries.
      Use the following context to answer the user's question.
      If you don't know the answer based on the context, say so clearly.
      Always cite the source document names in your answer.

      Context:
      [Source 1: 文档名称.pdf]
      (文档内容片段...)

      [Source 2: 报告.docx]
      (文档内容片段...)

用户: {question}
```

### 9.3 LangChain Chain 结构

```
RunnablePassthrough()
    │
    ▼
ChatPromptTemplate (RAG_PROMPT)
    │
    ▼
LLM (OpenAI / Ollama / Fake)
    │
    ▼
StrOutputParser()
    │
    ▼
回答字符串
```

### 9.4 LLM 供应商配置

| 供应商 | 配置值 | 默认模型 | 说明 |
|--------|--------|---------|------|
| OpenAI 兼容 | `openai` | gpt-4o-mini | 支持 OpenAI、DeepSeek、通义千问、智谱等 |
| Ollama 本地 | `ollama` | qwen2.5:7b | 本地部署，无需 API Key |
| 测试模式 | `test` / `local` | — | 返回 Mock 回答，用于测试 |

---

## 10. Gradio 前端设计

### 10.1 页面布局

```
┌───────────────────────────────────────────────────┐
│  [Logo]  企业 RAG 文档问答系统                        │
│  ────────────────────────────────────────────────  │
│  [👤 用户名]  [角色标签]  [退出]                    │
├───────────┬───────────────────────────────────────┤
│           │                                       │
│  📄 导航   │   主内容区域                            │
│           │                                       │
│  💬 问答   │   （根据选中菜单切换）                   │
│           │                                       │
│  📁 文档   │                                       │
│           │                                       │
│  🔄 流程   │                                       │
│           │                                       │
│  ⚙ 管理   │                                       │
│  （admin） │                                       │
│           │                                       │
└───────────┴───────────────────────────────────────┘
```

### 10.2 页面功能

| 页面 | 功能 | 访问权限 |
|------|------|---------|
| **问答页** | 发起 RAG 对话、查看带来源的回答、对话历史管理 | 已登录用户 |
| **文档页** | 上传文档、查看处理状态、删除文档、重新处理 | 编辑者可上传/删除 |
| **流程页** | 查看工作流定义、创建并执行实例、查看步骤状态 | 已登录用户 |
| **管理页** | 用户列表、编辑用户、分配角色、删除用户 | admin 角色 |

### 10.3 认证状态管理

```python
# auth_helpers.py
class AuthState:
    access_token: str
    refresh_token: str
    username: str
    roles: list[str]
    is_authenticated: bool
    is_admin: bool
```

- 状态以 JSON 字符串形式存储在 `gr.State()` 中
- `api_client.py` 封装所有 API 调用，自动注入 `Authorization` 头
- 401 响应时自动尝试 Refresh Token，刷新失败跳转登录页

---

## 11. 业务流程引擎

### 11.1 架构

```
┌──────────────────────────────────────────────────┐
│  WorkflowRegistry                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ doc_     │  │ qa_      │  │ (自定义) │       │
│  │ review   │  │escalation│  │          │       │
│  └──────────┘  └──────────┘  └──────────┘      │
│                                                  │
│  注册表 (dict[str, type[BaseWorkflow]])          │
└──────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────┐
│  BaseWorkflow (抽象基类)                          │
│  - validate_input()                              │
│  - execute_step()                                │
│  - get_next_step()                               │
│  - on_complete()                                 │
└──────────────────────────────────────────────────┘
```

### 11.2 内置示例

| 流程 | 说明 | 步骤 |
|------|------|------|
| **文档审批** | 提交文档 → 审核 → 发布 | submit → review → approve/reject → publish |
| **问题升级** | 用户提问 → AI 回答 → 人工介入 | auto_response → check_confidence → escalate or close |

### 11.3 扩展方式

1. 继承 `BaseWorkflow` 实现自定义流程类
2. 通过 `WorkflowRegistry.register()` 注册
3. 在 API 层通过配置选择使用哪个流程实现
4. **未来可升级为 LangGraph**：通过 `WorkflowRegistry.get_engine(use_langgraph=True)` 切换

---

## 12. 迁移路径

### 12.1 SQLite → PostgreSQL（生产环境）

```python
# app/database.py（修改前）
engine = create_engine("sqlite:///./data/app.db")

# app/database.py（修改后）
engine = create_engine("postgresql://user:pass@host:5432/rag_db")
```

**步骤**：
1. 安装 PostgreSQL 依赖：`uv sync --extra postgres` 或 `uv add asyncpg psycopg2-binary`
2. 修改 `.env` 中 `DATABASE_URL` 为 PostgreSQL 连接字符串
3. 运行 `alembic upgrade head` 创建表结构
4. 注意：SQLite 不支持 `ALTER` 等操作，迁移前需确认 Schema 一致性

### 12.2 Gradio → Vue/React（前端重写）

- API 接口层完全不变，前端只需复用 REST API
- JWT 认证机制不变，新前端适配 token 存储策略即可
- 后端代码零改动，API 接口是契约边界

### 12.3 简单流程 → LangGraph（复杂编排）

```
WorkflowEngine (接口)
  ├── SimpleWorkflowEngine (当前实现)
  └── LangGraphWorkflowEngine (未来)
```

- 通过工厂方法 `WorkflowRegistry.get_engine()` 切换
- LangGraph 为可选依赖 `[langgraph]`

---

## 13. 部署与运维

### 13.1 开发环境启动

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 LLM API Key

# 3. 启动服务（开发模式）
uvicorn app.main:app --reload --port 8000

# 4. 访问
# API 文档: http://localhost:8000/docs
# Gradio UI: http://localhost:8000/ui
```

### 13.2 测试模式启动

```bash
# 使用内置 Mock 模型，无需外部 API
LLM_PROVIDER=test EMBEDDING_PROVIDER=test uvicorn app.main:app --port 8000
```

### 13.3 生产部署建议

| 组件 | 建议 |
|------|------|
| 进程管理 | systemd / supervisor / Docker |
| 反向代理 | Nginx（处理 SSL、静态文件、负载均衡） |
| 数据库 | PostgreSQL（`.env` 中切换 `DATABASE_URL`） |
| 向量库 | Chroma 持久化到持久卷 |
| 文件存储 | 挂载持久卷到 `data/uploads/` |
| LLM | 生产环境建议使用 OpenAI 兼容 API |

### 13.4 环境变量参考

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DATABASE_URL` | 否 | `sqlite:///./data/app.db` | 数据库连接 |
| `JWT_SECRET_KEY` | **是**（生产） | dev-secret-key | JWT 签名密钥 |
| `LLM_PROVIDER` | 否 | `openai` | openai/ollama/test |
| `LLM_API_KEY` | **是**（OpenAI） | | OpenAI 兼容 API Key |
| `LLM_API_BASE` | 否 | https://api.openai.com/v1 | API 地址 |
| `LLM_MODEL` | 否 | gpt-4o-mini | 模型名称 |
| `OLLAMA_BASE_URL` | **是**（Ollama） | http://localhost:11434 | Ollama 地址 |
| `EMBEDDING_PROVIDER` | 否 | `openai` | 嵌入模型供应商 |
| `CHROMA_PERSIST_DIR` | 否 | ./data/chroma | 向量库持久化路径 |

### 13.5 Chroma 数据迁移

Chroma 数据存储在 `data/chroma/` 目录，直接复制该目录即可迁移。注意版本兼容性——Chroma 数据格式在版本间可能不兼容。

### 13.6 日志

- 通过 `APP_DEBUG` 和 `LOG_LEVEL` 控制日志级别
- 建议生产环境配置日志轮转或集成结构化日志系统（如 structlog）

---

## 附录 A：依赖清单

### 核心依赖

| 包 | 版本 | 用途 |
|----|------|------|
| fastapi | ≥0.115.0 | Web 框架 |
| uvicorn | ≥0.32.0 | ASGI 服务器 |
| sqlalchemy | ≥2.0.0 | ORM |
| alembic | ≥1.13.0 | 数据库迁移 |
| pydantic | ≥2.0.0 | 数据校验 |
| pydantic-settings | ≥2.0.0 | 配置管理 |
| python-jose | ≥3.3.0 | JWT |
| bcrypt | ≥5.0.0 | 密码哈希 |
| python-multipart | ≥0.0.12 | 文件上传 |
| gradio | ≥5.0, <6.0 | UI 框架 |
| httpx | ≥0.27.0 | HTTP 客户端 |
| langchain | ≥1.3.0, <2.0.0 | RAG 框架 |
| langchain-community | ≥0.4.0 | 文档加载器 |
| langchain-openai | ≥1.4.0 | OpenAI 集成 |
| langchain-chroma | ≥0.2.0 | 向量库集成 |
| chromadb | ≥0.5.0 | 向量数据库 |
| pypdf | ≥5.0.0 | PDF 解析 |
| python-docx | ≥1.1.0 | DOCX 解析 |

### 可选依赖

| extra | 包 | 用途 |
|-------|----|------|
| postgres | asyncpg, psycopg2-binary | PostgreSQL 驱动 |
| ollama | langchain-ollama | Ollama 集成 |
| langgraph | langgraph | LangGraph 流程引擎 |
| dev | pytest, ruff, mypy | 开发工具 |

---

## 附录 B：验证方案

| 阶段 | 验证方法 | 预期结果 |
|------|---------|---------|
| 基础设施 | `uvicorn app.main:app` | http://localhost:8000/docs 显示 Swagger |
| 认证系统 | 注册 → 登录 → /me | 收到 JWT，返回用户信息含角色 |
| RAG 管线 | 上传文档 → 轮询 → 状态 indexed | 文档状态变为 indexed，chunk_count > 0 |
| RAG 问答 | 对话中发送 query | 收到带来源引用的回答 |
| 业务流程 | 创建流程实例 → 执行 | 步骤状态可查询 |
| 端到端 | `python test_e2e.py` | 10 个步骤全部通过 |