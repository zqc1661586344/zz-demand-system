# 合规审查链路图

## API → Service → Harness 调用链

```mermaid
sequenceDiagram
    participant Client as 前端 / curl
    participant API as FastAPI<br/>/api/compliance/reviews
    participant Service as ReviewService
    participant Harness as ComplianceHarness<br/>(LangGraph Runtime)
    participant DB as PostgreSQL
    participant KB as 法规知识库<br/>(PGVector)
    participant File as 磁盘文件

    Client->>API: POST /compliance/reviews
    API->>Service: create_review(document_id)
    Service->>DB: SELECT documents WHERE id=?
    alt document 不存在或 status != 'indexed'
        Service-->>API: raise ValueError
        API-->>Client: 400 Bad Request
    else OK
        Service->>DB: UPSERT compliance_documents
        Service->>DB: INSERT compliance_reviews (status='pending')
        Service->>DB: SELECT compliance_playbooks<br/>(contract_type + is_active)
        DB-->>Service: rules[]
        Service-->>API: {review_id, status='pending'}
        API-->>Client: 200 OK
        API->>Harness: BackgroundTasks.run_review(payload)

        rect rgb(240, 248, 255)
            Note over Harness: LangGraph Stream (stream_mode="updates")
            Harness->>DB: _persist_status(review_id, 'parsing')
            Harness->>File: parse_document<br/>(ParseSkill)
            File-->>Harness: raw_text + clauses[] + doc_type

            Harness->>DB: _persist_status(review_id, 'planning')
            Harness->>Harness: supervise<br/>(SupervisorAgent.plan_review)
            Harness->>Harness: extract_clauses<br/>(ExtractorAgent: 条款分类 + key_info)

            Harness->>DB: _persist_status(review_id, 'reviewing', risk_counts)
            Harness->>Harness: review_clauses
            Note over Harness: PlaybookSkill (keyword 匹配)
            Note over Harness: RiskSkill (风险评级)
            Harness->>KB: rag_skill.citation_verifier<br/>(引用法规条文, 可选)
            KB-->>Harness: legal_references[]

            Harness->>DB: _persist_status(review_id, 'reflecting')
            Harness->>Harness: reflect<br/>(质量评分 + retry_count++)

            Harness->>DB: _persist_status(review_id, 'pending_human')
            Harness->>DB: INSERT compliance_human_actions<br/>(高风险留痕, MVP 不阻塞)

            Harness->>DB: _persist_status(review_id, 'generating')
            Harness->>Harness: generate_report
            Note over Harness: ReportSkill → ReporterAgent<br/>→ reporting/generator.py
            Harness->>File: 写入 HTML/Word 报告
            Harness->>DB: INSERT compliance_risks + references
            Harness->>DB: _persist_status(review_id, 'completed')
        end

        API-->>Client: (Background, 异步完成)
        Client->>API: GET /compliance/reviews/{id}
        API->>DB: SELECT compliance_reviews + risks
        DB-->>API: review detail
        API-->>Client: {status, risks, report_path, ...}
    end
```

## LangGraph 工作流图

```mermaid
flowchart TD
    A([START]) --> B[parse_document<br/>ParseSkill]
    B --> C[supervise<br/>SupervisorAgent]
    C --> D[extract_clauses<br/>ExtractorAgent]
    D --> E[review_clauses<br/>PlaybookSkill + RiskSkill]
    E --> F[reflect<br/>质量自评]
    F --> G{should_retry?}

    G -->|quality < threshold<br/>retry ≤ max| E
    G -->|HITL enabled<br/>且有高风险| H[human_review<br/>HitlManager]
    G -->|否则| I[compare_template<br/>MVP 直通]

    H --> I
    I --> J[generate_report<br/>ReporterAgent + generator.py]
    J --> K([END])

    style A fill:#6ee7b7
    style K fill:#fca5a5
    style G fill:#fde68a
    style E fill:#c4b5fd
```

## 状态流转

```mermaid
stateDiagram-v2
    [*] --> pending: POST 创建任务
    pending --> parsing: 进入 parse_document
    parsing --> planning: supervise + extract_clauses
    planning --> reviewing: PlaybookSkill 匹配
    reviewing --> reflecting: 质量自评
    reflecting --> reviewing: should_retry 条件边<br/>(max_retry 内)
    reflecting --> pending_human: should_retry → human<br/>(高风险需人工)
    reflecting --> generating: should_retry → skip_human
    pending_human --> generating: HITL MVP 不阻塞
    generating --> completed: HTML/Word 报告落库
    parsing --> failed: ParseSkill 失败
    planning --> failed: 异常
    reviewing --> failed: 异常
    generating --> failed: 报告生成异常
    failed --> [*]
    completed --> [*]
```

## 数据库表关系（compliance_* 前缀）

```mermaid
erDiagram
    documents ||--|| compliance_documents : "document_id"
    compliance_documents ||--o{ compliance_reviews : "compliance_doc_id"
    compliance_reviews ||--o{ compliance_risks : "review_id"
    compliance_reviews ||--o{ compliance_human_actions : "review_id"
    compliance_risks ||--o{ compliance_risk_references : "risk_id"

    compliance_playbooks {
        string id PK
        string name
        string contract_type
        string clause_type
        string risk_level
        string match_type
        string match_pattern
        float match_threshold
        string legal_basis_ref
        boolean is_active
        int priority
    }

    compliance_reviews {
        string id PK
        string compliance_doc_id FK
        string thread_id
        string status
        int high_risk_count
        int medium_risk_count
        int low_risk_count
        datetime started_at
        datetime completed_at
        string error_message
    }

    compliance_risks {
        string id PK
        string review_id FK
        string clause_number
        string risk_level
        string risk_category
        string description
        string suggestion
        float ai_confidence
        boolean human_confirmed
    }
```

## 关键组件一览

| 组件 | 文件 | 职责 |
|------|------|------|
| ReviewService | `services/review_service.py` | 业务编排：创建任务、加载 Playbook、启动 Harness |
| ComplianceHarness | `harness/runtime.py` | LangGraph 运行时：节点路由、状态落库、条件边 |
| ReviewGraph | `workflows/review_graph.py` | 图构建：节点连接 + 条件边 |
| ReviewState | `workflows/state.py` | 流水线 TypedDict + 阶段常量 |
| PlaybookSkill | `skills/playbook_skill.py` | keyword / hybrid 规则匹配 |
| RiskSkill | `skills/risk_skill.py` | 风险 5 类 × 3 级识别 |
| RagSkill | `skills/rag_skill.py` | 法规条文检索 + 引用校验 |
| ReporterAgent | `agents/reporter.py` | 报告数据组装 |
| HTML Generator | `reporting/generator.py` | 自包含 HTML 报告渲染 |
| API Router | `api/reviews.py` | CRUD + BackgroundTasks + HITL |
| Playbook 种子 | `scripts/seed_playbooks.py` | 从 `default_rules/*.json` 灌规则 |