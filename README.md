# 企业 RAG 智能问答 & 文档合规审查系统

> **版本**: 0.4.0
> **最后更新**: 2026-09-02
> **项目状态**: RAG 核心功能 + 合规审查全链路已上线

一套面向企业的 **双引擎** 智能知识系统：

- 💬 **RAG 智能问答** — 上传内部文档，Hybrid RAG（稠密 PGVector + 稀疏 PG tsvector/BM25 + RRF 融合 + 可选重排）精准检索，SSE 流式回答
- ⚖️ **文档合规审查** — LangGraph 多 Agent 协作（Supervisor → Extractor → Reviewer → Researcher → Reporter），自动识别合同/制度风险点、检索法规依据、生成 HTML/Word/PDF 三路报告

**前端** Streamlit（5 页面）| **后端** FastAPI（8001）| **数据** SQLite / PostgreSQL | **包管理** uv

---

## 🚀 快速开始

### 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | ≥ 3.11 | |
| uv | 最新 | 包管理 |
| PostgreSQL | ≥ 15（可选） | 生产推荐，开发可用 SQLite |

### 一键启动

```bash
# 1. 克隆 & 进入
git clone <repo> && cd zz-demand-system

# 2. 安装依赖（含合规审查可选组）
uv sync --all-extras

# 3. 复制并填写配置
cp .env.example .env
# 至少需设置：LLM_API_KEY / EMBEDDING_API_KEY / JWT_SECRET_KEY
# 合规模块可选设置：COMPLIANCE_REPORT_DIR / COMPLIANCE_ENABLE_HITL

# 4. 启动（后端 8001 + Streamlit 前端 8002）
bash start.sh
# → 后端 http://localhost:8001
# → 前端 http://localhost:8002
# → API 文档 http://localhost:8001/docs
```

### 全新库初始化 admin

首次启动自动建表（`Base.metadata.create_all` + Alembic）。生产环境不会自动种 admin，需要手动启用一次：

```bash
SEED_DEMO_USER=true bash start.sh
# → 用 admin / admin123 登录后立即改密
curl -X POST http://localhost:8001/api/auth/change-password \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"old_password":"admin123","new_password":"<强密码>"}'
# → 重启（不带开关）
bash start.sh
```

### 合规审查种子数据（可选）

```bash
# 一键初始化 Playbook 默认规则 + 4 部核心法规种子（劳动合同方向）
curl -X POST http://localhost:8001/api/compliance/knowledge/seed \
  -H "Authorization: Bearer <token>"
# 或在 Streamlit → 📚 法规库 → 点击「🌱 初始化种子数据」按钮
```

---

## 🏗️ 系统架构

```mermaid
flowchart TD
    subgraph 用户层
        SL["Streamlit 前端 (8002)"]
        API_CLI["API 客户端 (curl / SDK)"]
    end

    subgraph "FastAPI 网关 (8001)"
        ROUTER["路由层 (app/api/router.py)"]
        JWT["JWT 鉴权 + 限流中间件"]
    end

    subgraph "基础 RAG 子系统"
        AUTH["Auth / Users"]
        DOCS["Documents 管理 + 解析管线"]
        CONV["Conversations + RAG 查询"]
        RAG_ENGINE["RAG Engine<br/>Hybrid Retriever<br/>(PGVector + tsvector/BM25 + RRF)"]
    end

    subgraph "合规审查子系统 (app/compliance)"
        REVIEW["Reviews API<br/>(CRUD + 启动 + 报告下载)"]
        PB["Playbooks API / Service<br/>(规则库 CRUD)"]
        KB["Knowledge API / Service<br/>(法规 CRUD + 种子 + 检索)"]
        HARNESS["ReviewHarness<br/>(LangGraph 运行时)"]
        SKILLS["Skills<br/>ParseSkill / PlaybookSkill / RiskSkill / RagSkill / ReportSkill"]
        AGENTS["Agents<br/>Supervisor / Extractor / Reviewer / Researcher / Reporter"]
        REPORT["Reporting<br/>HTML + Word + PDF 三路生成"]
    end

    subgraph "数据层"
        DB["SQLite / PostgreSQL<br/>Alembic 迁移"]
        VEC_DOC["PGVector: documents 集合<br/>(bge-m3 1024d)"]
        VEC_REG["PGVector: compliance_regulations 集合"]
        FILES["文件存储<br/>(原始文档 + 报告落盘)"]
    end

    SL --> ROUTER
    API_CLI --> ROUTER
    ROUTER --> JWT
    JWT --> AUTH & DOCS & CONV & REVIEW & PB & KB

    CONV --> RAG_ENGINE
    RAG_ENGINE --> VEC_DOC
    RAG_ENGINE --> DB

    REVIEW --> HARNESS
    HARNESS --> SKILLS & AGENTS
    HARNESS --> DB
    HARNESS --> REPORT
    SKILLS --> KB
    KB --> VEC_REG & DB
    REPORT --> FILES

    DOCS --> FILES
    DOCS --> VEC_DOC & DB
```

---

## 📋 使用流程

### 基础 RAG 问答流程

```mermaid
flowchart LR
    A["注册 / 登录"] --> B["上传文档<br/>支持 PDF/DOCX/MD 等"]
    B --> C["等待索引完成<br/>解析 → 分块 → 向量化"]
    C --> D["新建对话"]
    D --> E["提问"]
    E --> F["Hybrid RAG 检索"]
    F --> G["LLM 生成回答<br/>带来源引用"]
```
### 合规审查流程

```mermaid
flowchart LR
    A1["登录"] --> A2["上传待审文档<br/>劳动合同 / 制度文件等"]
    A2 --> A3["合规审查页<br/>选择 Playbook"]
    A3 --> A4["启动审查<br/>LangGraph 异步运行"]
    A4 --> A5["查看风险清单<br/>高中低三级"]
    A5 --> A6{"高风险?"}
    A6 -->|是| A7["人工审核 HITL<br/>确认 / 修改"]
    A6 -->|否| A8["自动生成报告"]
    A7 --> A8
    A8 --> A9["下载报告<br/>HTML / Word / PDF"]
```

### Streamlit 页面结构

| 页面 | 功能 |
|------|------|
| 💬 对话 | RAG 问答 + 流式回答 + 多轮历史 |
| 📁 文档管理 | 上传 / 删除 / 索引状态查看 |
| ⚖️ 合规审查 | 发起审查 + 风险明细 + HITL + 三路报告下载 |
| 📋 Playbook | 规则库 CRUD（企业红线条款 + 关键词 + 风险等级） |
| 📚 法规库 | 种子初始化 + 法规录入 + 语义检索 |

---

## 📄 支持的文件格式

RAG 问答和合规审查共用同一套文档解析管线，支持以下格式：

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

## ⚖️ 文档合规审查

面向企业法务 / 合规 / 风控团队的 **智能文档审查副驾驶**。上传合同、政策文件、内部制度，Agent 自动识别风险点、检索法规依据、生成审查报告、标注修改建议。

### LangGraph 工作流

```mermaid
flowchart TD
    A([START]) --> B[ParseSkill<br/>解析文档 + 条款切分]
    B --> C[SupervisorAgent<br/>制定审查计划]
    C --> D[ExtractorAgent<br/>条款分类 + 关键信息抽取]
    D --> E[PlaybookSkill<br/>关键词/规则匹配]
    E --> F[RiskSkill<br/>风险识别 + 三级评级]
    F --> G[RagSkill<br/>法规条文检索 + 引用校验]
    G --> H[ReviewerAgent<br/>条款级审查意见]
    H --> I[质量自评 reflect<br/>覆盖率 + 置信度 + 重试衰减]
    I --> J{should_retry?}
    J -->|质量不足 + 未超上限| E
    J -->|有高风险 + HITL 开启| K[HitlManager<br/>人工审核]
    J -->|模板比对启用| L[compare_template<br/>模板偏离检测]
    J -->|否则| M[ReporterAgent + generator.py<br/>组装报告 + 三路落盘]
    K --> M
    L --> M
    M --> N([END])
    style A fill:#6ee7b7
    style N fill:#fca5a5
    style J fill:#fde68a
    style E fill:#c4b5fd
    style M fill:#93c5fd
```

### 状态流转

```mermaid
stateDiagram-v2
    [*] --> pending: POST /reviews
    pending --> parsing: ParseSkill
    parsing --> planning: Supervisor + Extractor
    planning --> reviewing: PlaybookSkill + RiskSkill
    reviewing --> reflecting: 质量自评
    reflecting --> reviewing: should_retry → 重试
    reflecting --> pending_human: should_retry → HITL
    reflecting --> comparing: compare_template
    pending_human --> generating: HITL 通过
    comparing --> generating: 比对完成
    generating --> completed: HTML/Word/PDF 落盘
    parsing --> failed
    planning --> failed
    reviewing --> failed
    generating --> failed
    failed --> [*]
    completed --> [*]
```

### 关键概念

| 概念 | 说明 | 管理入口 |
|------|------|----------|
| **Playbook** | 企业红线条款、标准措辞、风险阈值的可配置规则集 | 📋 Playbook 页面 CRUD |
| **法规知识库** | 审查的法规依据来源（PGVector 独立 collection `compliance_regulations`） | 📚 法规库页面（种子初始化 + 录入 + 语义检索） |
| **风险等级** | high（高）/ medium（中）/ low（低） | 审查结果页直观展示 |
| **报告格式** | HTML（自包含 + 内嵌样式）/ Word（python-docx 生成封面+目录）/ PDF（weasyprint） | 审查完成后一键三路下载 |
| **HITL** | 高风险条款留痕，人工可确认/修改（MVP 默认不阻塞自动流程） | 审查结果页「人工审核」按钮 |

### 审查报告示例产出

```
data/compliance/reports/
├── review-abc123-20260901-143022.html
├── review-abc123-20260901-143022.docx
└── review-abc123-20260901-143022.pdf
```

> 📄 **详细设计**：完整的技术方案、数据库 ER 图、Agent / Skill 接口定义见 [`docs/拓展功能2期.md`](docs/拓展功能2期.md)；审查链路时序图见 [`docs/review_chain.md`](docs/review_chain.md)。

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
# 上传文档
curl -X POST http://localhost:8001/api/documents/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@report.pdf"

# 查看文档状态
curl http://localhost:8001/api/documents/<doc-id> \
  -H "Authorization: Bearer <token>"
# → status: "indexed" 表示索引完成

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

### 合规审查

```bash
# 发起审查（基于已上传并 indexed 的文档）
curl -X POST http://localhost:8001/api/compliance/reviews \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "<已上传文档 id>",
    "playbook_id": "<可选，指定规则集>",
    "template_id": "<可选，模板比对参考>"
  }'
# → 返回 {review_id, status: "pending"}，后台 LangGraph 异步运行

# 查询审查详情 + 风险清单
curl http://localhost:8001/api/compliance/reviews/<review-id> \
  -H "Authorization: Bearer <token>"
# → 返回 {status, risks: [{clause_number, risk_level, description, suggestion, legal_references}], ...}

# 人工审核（HITL）
curl -X POST http://localhost:8001/api/compliance/reviews/<review-id>/human \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"risk_id": "<高风险条款 id>", "action": "confirm", "note": "法务已确认"}'

# 下载报告（三路格式）
curl http://localhost:8001/api/compliance/reviews/<review-id>/report/html \
  -H "Authorization: Bearer <token>"
curl http://localhost:8001/api/compliance/reviews/<review-id>/report/word \
  -H "Authorization: Bearer <token>"
curl http://localhost:8001/api/compliance/reviews/<review-id>/report/pdf \
  -H "Authorization: Bearer <token>"
```

### Playbook 规则库

```bash
# 列出 / 创建 / 更新 / 删除 Playbook
curl http://localhost:8001/api/compliance/playbooks \
  -H "Authorization: Bearer <token>"

curl -X POST http://localhost:8001/api/compliance/playbooks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "劳动合同默认规则",
    "contract_type": "labor_contract",
    "rules": [
      {"rule_id": "R001", "rule_type": "keyword", "risk_level": "high",
       "keywords": ["违约金过高", "竞业限制超过2年"], "legal_basis_ref": "劳动合同法第23条"},
      {"rule_id": "R002", "rule_type": "threshold", "risk_level": "medium",
       "keywords": ["试用期"], "match_threshold": 0.8}
    ]
  }'
```

### 法规知识库

```bash
# 种子初始化（一键种 4 部核心法规）
curl -X POST http://localhost:8001/api/compliance/knowledge/seed \
  -H "Authorization: Bearer <token>"

# 手动录入法规
curl -X POST http://localhost:8001/api/compliance/knowledge/regulations \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "中华人民共和国劳动合同法",
    "regulation_type": "law",
    "effective_date": "2008-01-01",
    "clauses": [
      {"article": "第19条", "content": "劳动合同期限三个月以上不满一年的，试用期不得超过一个月..."},
      {"article": "第23条", "content": "用人单位与劳动者可以在劳动合同中约定保守..."},
      {"article": "第25条", "content": "除本法第二十二条和第二十三条规定的情形外..."},
      {"article": "第47条", "content": "经济补偿按劳动者在本单位工作的年限..."}
    ]
  }'

# 法规语义检索
curl -X POST http://localhost:8001/api/compliance/knowledge/search \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "试用期最长不得超过多长时间？", "top_k": 5}'
```

完整 API 端点清单见启动后访问 `/docs`，或 [`docs/architecture.md`](docs/architecture.md)。

---

## 🐳 生产部署

### 1. 更换密钥

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

将生成的密钥填入 `.env` 的 `JWT_SECRET_KEY`。**生产禁止使用默认值**。

### 2. 启动服务

**方式一：start.sh（推荐）**

```bash
bash start.sh
# 后端 8001 + Streamlit 8002 同时启动
```

**方式二：分别后台启动**

```bash
nohup .venv/bin/uvicorn app.main:app \
  --host 0.0.0.0 --port 8001 > /tmp/rag.log 2>&1 &

nohup .venv/bin/streamlit run app/streamlit_app/app.py \
  --server.port 8002 --server.headless true > /tmp/streamlit.log 2>&1 &

# 验证
curl http://localhost:8001/api/health
# → {"status":"ok","version":"0.4.0"}
```

### 3. 迁移到 PostgreSQL

```bash
uv sync --extra postgres
# .env
DATABASE_URL=postgresql://user:password@host:5432/rag_db
alembic upgrade head
```

### 4. 报告目录持久化

合规审查报告落盘到 `COMPLIANCE_REPORT_DIR`（默认 `data/compliance/reports/`）。生产环境建议：

```bash
# 迁移到大容量存储
mkdir -p /data/compliance-reports
ln -s /data/compliance-reports /opt/zz-demand-system/data/compliance/reports
```

### 5. Nginx 反向代理

```nginx
server {
    listen 443 ssl;
    server_name rag.example.com;

    # 后端 API + SSE
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Streamlit 前端（WebSocket 必需）
    location /streamlit/ {
        proxy_pass http://127.0.0.1:8002/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

### 6. systemd 服务管理

**后端** `/etc/systemd/system/rag-backend.service`：

```ini
[Unit]
Description=RAG + Compliance Backend
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

**前端** `/etc/systemd/system/rag-frontend.service`：

```ini
[Unit]
Description=Streamlit Frontend
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

### 端到端

```bash
LLM_PROVIDER=test EMBEDDING_PROVIDER=test .venv/bin/uvicorn app.main:app --port 8001 &
python test_e2e.py
```

覆盖场景：
- 用户注册 → 登录 → 获取个人信息
- 上传文档 → 索引完成
- 创建对话 → RAG 查询 → 获取消息
- 健康检查

### 合规审查链路

```bash
# 1. 确保法规种子已初始化
curl -X POST http://localhost:8001/api/compliance/knowledge/seed \
  -H "Authorization: Bearer <token>"

# 2. 上传一个测试合同
curl -X POST http://localhost:8001/api/documents/upload \
  -H "Authorization: Bearer <token>" -F "file=@test_labor_contract.docx"

# 3. 发起审查并等待完成
curl -X POST http://localhost:8001/api/compliance/reviews \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"document_id": "<doc-id>"}'

# 4. 下载报告
curl http://localhost:8001/api/compliance/reviews/<review-id>/report/html \
  -H "Authorization: Bearer <token>" -o report.html
```

---

## 📁 项目结构

```
zz-demand-system/
├── app/
│   ├── api/                        # FastAPI 路由层
│   ├── middleware/                  # CORS / 限流 / Tracing
│   ├── models/                     # SQLAlchemy ORM（auth/doc/conversation 等）
│   ├── schemas/                    # Pydantic 请求/响应模型
│   ├── services/                   # RAG + Workflow 业务逻辑
│   ├── rag/                        # RAG 核心（Hybrid Retriever / LLM / Embedding / Pipeline）
│   ├── workflows/                  # 业务流程引擎
│   ├── cache/                      # 缓存层
│   ├── streamlit_app/              # Streamlit 前端
│   │   ├── app.py                  #   入口 + 页面路由
│   │   ├── api_client.py           #   后端 HTTP 客户端（含 token 管理）
│   │   ├── auth.py                 #   登录态管理
│   │   └── views/
│   │       ├── chat.py              #     💬 对话
│   │       ├── documents.py        #     📁 文档管理
│   │       ├── compliance.py       #     ⚖️ 合规审查（三路报告下载）
│   │       ├── playbooks.py        #     📋 Playbook 规则库
│   │       ├── knowledge.py        #     📚 法规知识库
│   │       └── login.py            #     登录页
│   └── compliance/                  # 合规审查模块
│       ├── api/                    #   Reviews / Playbooks / Knowledge 路由
│       ├── services/               #   ReviewService / PlaybookService / RegulationService
│       ├── models/                  #   SQLAlchemy（review/playbook/regulation 等）
│       ├── schemas/                 #   Pydantic 请求/响应
│       ├── workflows/              #   LangGraph 图构建 + 状态定义
│       ├── harness/                #   LangGraph 运行时 + Checkpointer + HITL
│       ├── agents/                 #   Supervisor / Extractor / Reviewer / Researcher / Reporter
│       │   └── prompts/             #     Prompt 模板（extractor_prompt 等）
│       ├── skills/                  #   ParseSkill / PlaybookSkill / RiskSkill / RagSkill / ReportSkill
│       ├── knowledge/               #   法规向量库 / 检索 / 种子数据 / 引用校验
│       ├── parsing/                 #   文档解析 + 条款切分
│       ├── playbook/                #   Playbook 规则引擎
│       ├── reporting/               #   报告生成
│       │   └── exporters/          #     Word (python-docx) / PDF (weasyprint)
│       └── scripts/                 #   种子脚本
├── alembic/                        # 数据库迁移
├── docs/                           # 设计文档
│   ├── architecture.md             #   端到端技术方案
│   ├── RAG.md                      #   Hybrid RAG 详解
│   ├── 拓展功能2期.md               #   合规审查技术设计
│   └── review_chain.md             #   审查链路时序图
├── data/                           # 运行时落盘
│   └── compliance/reports/         #   审查报告（HTML/Word/PDF）
├── start.sh                        # 一键启动脚本
├── pyproject.toml                  # uv + 依赖管理
└── alembic.ini
```

---

## 🔧 技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| **Web 框架** | FastAPI 0.115+ | 异步 + OpenAPI 自动生成 |
| **前端** | Streamlit 1.40+ | 纯 Python UI，5 页面 |
| **ORM** | SQLAlchemy 2.0+ | |
| **迁移** | Alembic | |
| **认证** | JWT（python-jose）+ bcrypt | RBAC 三级角色 |
| **向量库** | PGVector（bge-m3 1024d） | 两个独立 collection：`documents` 和 `compliance_regulations` |
| **混合检索** | PG tsvector（默认）/ rank_bm25（回退）+ jieba | RRF 融合 |
| **可选重排** | bge-reranker-v2-m3 | 需 transformers + torch |
| **LLM / Embedding** | OpenAI 兼容协议 / Ollama / test mock | 配置化切换 |
| **RAG 框架** | LangChain 1.3+ | |
| **Agent 编排** | LangGraph | 多 Agent DAG + 状态管理 + Checkpointer |
| **文档解析** | PyPDF / python-docx / docx2txt / markdown | |
| **报告导出** | python-docx（Word）+ weasyprint（PDF） | 三路并行生成 |
| **包管理** | uv | |
| **中间件** | slowapi（限流）+ Tracing（request_id）+ CORS | |

---

## 📈 未来规划

### RAG 核心
- [x] 流式输出（SSE）
- [x] OpenAPI 兼容第三方供应商（DeepSeek / 通义千问 / Ollama）
- [x] Hybrid RAG（PGVector + tsvector + RRF）
- [x] LangGraph 工作流引擎
- [ ] 批量文档导入（文件夹上传）
- [ ] 文档预览与在线编辑
- [ ] 检索测试工具（给定问题查看原始检索结果）

### 合规审查
- [x] LangGraph 多 Agent 审查链路
- [x] Playbook 规则库 CRUD
- [x] 法规知识库 CRUD + 种子初始化 + 语义检索
- [x] HTML + Word + PDF 三路报告生成
- [x] 报告下载 API + Streamlit 前端
- [x] HITL 人工审核（MVP）
- [ ] 模板比对结果可视化
- [ ] 版本差异审查（合同修改 diff）
- [ ] 判例知识库（裁判文书入库 + 案例匹配）
- [ ] 可观测性指标（审查耗时 / 各节点成功率 / 风险分布看板）
- [ ] 更多测试覆盖（单元测试 / 集成测试）

---

## 📝 License

MIT