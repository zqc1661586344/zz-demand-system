# RAG 系统完整流程

本文档完整描述本项目的 RAG（Retrieval-Augmented Generation）工作流程。

---

## 架构概述

整个系统由**两条管道**组成：

| 管道 | 方向 | 说明 |
|---|---|---|
| **索引管道** | 文档 → 向量 | 用户上传文件，经处理存入向量数据库 |
| **查询管道** | 问题 → 回答 | 用户提问，检索相关内容，LLM 生成回答 |

---

## 一、索引管道：文档 → 向量

将用户上传的文档处理成向量并存入 Chroma 数据库。

```
用户上传文件 → 保存到磁盘 → 后台处理 → 加载/分块/嵌入 → 存入 Chroma
```

### 第 1 步：上传

- **接口**: `POST /api/documents/upload`（`app/api/documents.py:27`）
- 接收 PDF / TXT / MD / DOCX 文件
- 保存到 `data/uploads/`，生成 UUID 文件名以防重名
- 数据库 `documents` 表写入一条记录，`status = "pending"`
- 通过 FastAPI 的 `BackgroundTasks` 触发异步处理

### 第 2 步：加载

- **函数**: `load_document()`（`app/rag/pipeline.py:18`）

根据 MIME 类型选择加载器：

| 文件类型 | 加载器 | 说明 |
|---|---|---|
| PDF | `PyPDFLoader` | 按页拆成 LangChain Document |
| TXT / MD | `TextLoader` | 读取为纯文本 |
| DOCX | `Docx2txtLoader` | 提取纯文本 |

加载后为每个页面附加元数据：`document_id`、`filename`。

### 第 3 步：分块

- **函数**: `split_documents()`（`app/rag/splitters.py:8`）

```python
RecursiveCharacterTextSplitter(
    chunk_size=800,       # 每块最多 800 字符
    chunk_overlap=150,    # 相邻块重叠 150 字符
    separators=["\n\n", "\n", "。", ".", " ", ""],
)
```

分块策略从粗到细：优先按段落（`\n\n`），再按句子（`。`、`.`），最后按词。保证语义完整性。

### 第 4 步：嵌入 + 索引

- **接口**: `add_documents_to_store()`（`app/rag/vector_store.py:15`）

| 组件 | 详情 |
|---|---|
| 向量数据库 | Chroma，单集合 `"documents"` |
| 持久化路径 | `data/chroma/` |
| 嵌入模型（OpenAI） | `text-embedding-3-small` |
| 嵌入模型（Ollama） | `nomic-embed-text` |
| 测试模式 | 零向量 `FakeEmbeddings` |

### 索引链路全图

```
用户上传文件
       │
       ▼
POST /api/documents/upload
       │
       ├─→ 保存文件到 data/uploads/
       ├─→ 数据库: documents 表 (status = "pending")
       └─→ background_tasks.add(process_document)
                  │
                  ▼
         process_document(doc_id)
                  │
       ┌──────────┼────────────┐
       ▼          ▼            ▼
  PDFLoader   TextLoader   DocxLoader
       │          │            │
       └──────────┴────────────┘
                  │  LangChain Document
                  ▼
    RecursiveCharacterTextSplitter
    (chunk_size=800, overlap=150)
                  │  文档块
                  ▼
    get_embedding_model() → 向量化
                  │
                  ▼
    Chroma (collection="documents")
    data/chroma/
                  │
                  ▼
    数据库: documents (status = "indexed")
```

---

## 二、查询管道：问题 → 回答

用户提问时，系统拉取历史、检索相关文档、构造 prompt，让 LLM 生成带来源的回答。

```
用户输入 → 拉取历史 → 检索文档 → 拼 prompt → LLM 生成
```

### 第 1 步：触发

- **接口**: `POST /api/conversations/{conv_id}/query`（`app/api/conversations.py:120`）

```json
Request Body:
{
  "query": "用户的问题",
  "top_k": 5
}
```

### 第 2 步：构建历史记忆

- **逻辑位置**: `app/api/conversations.py:133`
- 从 SQLite 取出当前对话的所有消息

记忆策略（摘要 + 滑动窗口）：

```
if 消息数量 > 10 条 (超过 5 轮):
    history = 最近 5 轮的完整消息（最近 10 条，保留细节）
    summary = 之前所有轮次的压缩摘要（LLM 生成，保留要点）
else:
    history = 全部消息
    summary = None
```

**摘要自动触发**：每 5 轮（10 条消息）触发一次后台摘要更新，用 LLM 压缩整个对话历史，存入 `conversations.summary` 字段。

### 第 3 步：向量检索

- **函数**: `similarity_search(query, k=5)`（`app/rag/vector_store.py:43`）
- 在 Chroma 单集合 `"documents"` 中按余弦相似度召回 top-5 相关文档块
- 每个文档块附带元数据（来源文件名、页码等）

### 第 4 步：构造 Prompt 并调用 LLM

- **函数**: `query_rag()`（`app/rag/chain.py:85`）

最终发送给 LLM 的 prompt 结构：

```
System:
你是一个内部知识库问答助手。
根据以下文档上下文回答问题，如果不知道就老实说。
始终标注来源文件名。

Context:
[Source 1: xxx.pdf]
...匹配的文档块内容...
[Source 2: xxx.txt]
...匹配的文档块内容...

Conversation history:
[Summary of earlier conversation]
用户介绍了自己是软件工程师张三...

[Recent messages]
User: 刚才提到的方案你觉得怎样？
Assistant: 我认为...

─────────────────────────────────
Human: 用户当前的问题
```

**LangChain 链结构**（`app/rag/chain.py:71`）：

```
RunnablePassthrough → ChatPromptTemplate → LLM → StrOutputParser
```

### 第 5 步：保存与响应

- 用户消息和 LLM 回答写入 `messages` 表
- 如果本轮达到 5 轮的整数倍 → 用 LLM 对整个对话生成一次摘要，更新 `conversations.summary`
- 返回结果：

```json
Response:
{
  "answer": "根据文档 xxx.pdf 的内容，...",
  "sources": [
    {"filename": "xxx.pdf", "page": 3},
    {"filename": "yyy.txt"}
  ]
}
```

### 查询链路全图

```
用户在 UI 输入问题
       │
       ▼
POST /api/conversations/{id}/query
       │
       ├─→ 数据库: 拉取历史消息 (get_messages)
       │   └─→ 摘要 + 最近 5 轮 → 拼 history
       │
       ├─→ Chroma: similarity_search(query, k=5)
       │   └─→ 召回 top-5 相关文档块
       │
       ├─→ 构建 Prompt:
       │   System: 系统指令 + Context(文档块)
       │   History: summary + 最近对话
       │   Human: 当前问题
       │   └─→ LLM 生成回答
       │
       ├─→ 数据库: 保存 user + assistant 消息
       │
       ├─→ 每 5 轮触发:
       │   generate_summary(全部历史)
       │   → 更新 conversations.summary
       │
       └─→ 返回 {answer, sources} → UI 展示
```

---

## 三、核心配置一览

| 配置项 | 值 | 来源 |
|---|---|---|
| 分块大小 | 800 字符 | `app/config.py` / `chunk_size` |
| 分块重叠 | 150 字符 | `app/config.py` / `chunk_overlap` |
| 检索 top-k | 5 | API 请求参数 `top_k` |
| 嵌入模型（OpenAI） | `text-embedding-3-small` | `.env` / `embedding_model` |
| 嵌入模型（Ollama） | `nomic-embed-text` | `.env` / `embedding_model` |
| LLM 模型（OpenAI） | `gpt-4o-mini` | `.env` / `llm_model` |
| LLM 模型（Ollama） | `qwen2.5:7b` | `.env` / `llm_model` |
| LLM 温度 | 0.3 | `app/rag/embeddings.py` |
| Chroma 持久化路径 | `data/chroma/` | `settings.chroma_persist_dir` |
| 记忆窗口 | 最近 5 轮 + LLM 摘要 | `conversations.py` / `RECENT_ROUNDS=5` |
| 摘要触发间隔 | 每 5 轮 | `SUMMARY_INTERVAL = 10` 条消息 |
| 数据库 | SQLite → `data/app.db` | `app/config.py` / `database_url` |

---

## 四、相关源码文件速查

| 文件 | 职责 |
|---|---|
| `app/api/documents.py` | 文档上传、列表、删除、重新处理接口 |
| `app/api/conversations.py` | 对话 CRUD、历史记忆组装、RAG 查询入口 |
| `app/rag/pipeline.py` | 文档处理流程编排（加载→分块→索引） |
| `app/rag/chain.py` | RAG 查询链构建 + 对话摘要生成 |
| `app/rag/vector_store.py` | Chroma 封装（单集合 "documents"） |
| `app/rag/embeddings.py` | 嵌入模型和 LLM 初始化 |
| `app/rag/splitters.py` | 文本分块配置 |
| `app/config.py` | 全局配置（pydantic-settings + `.env`） |
| `app/services/conversation_service.py` | 对话/消息数据库操作 |
| `app/services/document_service.py` | 文档数据库操作 |
| `app/models/conversation.py` | Conversation / Message ORM 模型 |
| `app/models/document.py` | Document / DocumentChunk ORM 模型 |