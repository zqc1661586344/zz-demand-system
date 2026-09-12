"""Application configuration using pydantic-settings."""

from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 配置模型，用于从环境变量加载配置。没有设置 case_sensitive=True —默认大小写不敏感。Pydantic v2的元配置机制，在类定义时由 Pydantic的元类（metaclass）读取。
    model_config = SettingsConfigDict(
        env_file=".env",  # 指定环境变量文件
        env_file_encoding="utf-8",  # 环境变量文件编码
        extra="ignore",  # 忽略额外的配置项
    )

    # 运行环境：development / production
    environment: Literal["development", "production"] = "development"

    # 启动时种子 admin(admin/admin123) 账号：默认关闭。仅开发/测试临时开启；生产与默认配置都不创建，杜绝硬编码超管凭据入库。首次 bootstrap 见 README。
    seed_demo_user: bool = False

    # App信息配置
    app_name: str = "Enterprise RAG System"
    app_debug: bool = True
    log_level: str = "INFO"  # 日志级别（DEBUG/INFO/WARNING/ERROR），由 logging_config 读取
    # 日志文件路径；为空字符串则只输出到标准流，不写文件
    log_file: str = "./data/logs/app.log"
    # 日志格式：json 或 plain（plain 为传统文本格式）
    log_format: str = "plain"
    # 单个日志文件上限（字节），超过后按该大小轮转
    log_max_bytes: int = 5 * 1024 * 1024  # 5MB
    # 保留的轮转日志文件数量
    log_backup_count: int = 5

    # Database配置；留空时回退 SQLite（开发环境），生产必须设 PG 连接串
    database_url: str = ""
    # PostgreSQL 连接池配置（仅 PostgreSQL 生效，SQLite 忽略）
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_pre_ping: bool = True
    db_pool_recycle: int = 3600

    # JWT
    jwt_secret_key: str = "dev-secret-key-do-not-use-in-production-123456"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # LLM 配置
    llm_provider: Literal["openai", "ollama", "test"] = "openai"
    llm_api_key: str = ""
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3

    # LLM 使用ollama加载的模型配置
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_embedding_model: str = "BAAI/bge-m3"

    # Embedding 模型配置
    embedding_provider: Literal["openai", "ollama", "test"] = "openai"
    embedding_api_key: str = ""
    embedding_api_base: str = "https://api.openai.com/v1"
    embedding_model: str = "BAAI/bge-m3"
    embedding_timeout_seconds: int = 30
    embedding_max_retries: int = 3

    # PGVector 向量库连接串（psycopg3 格式，替代 Chroma）。留空则无法使用向量库。
    vector_store_url: str = ""
    vector_collection_name: str = "documents"

    # ===== RAG 检索配置 =====

    # --- Pipeline 分片参数 ---
    chunk_size: int = 800
    chunk_overlap: int = 150

    # --- 检索算法 ---
    # similarity（纯向量）/ mmr（多样性）/ hybrid（向量+BM25+RRF融合）
    rag_search_type: Literal["similarity", "mmr", "hybrid"] = "hybrid"

    # --- Hybrid 融合 ---
    # 稠密 vs 稀疏权重（0=纯BM25, 1=纯向量）
    rag_hybrid_alpha: float = 0.3
    # BM25 缓存绕过：多 worker 下每个进程独立缓存，True 则每次从 DB 读（正确但较慢）
    rag_bm25_cache_bypass: bool = False

    # --- 稀疏检索后端 ---
    # bm25_memory（进程内 BM25）/ pg_tsvector（PG 原生 tsvector + ts_rank + GIN）
    rag_sparse_backend: Literal["bm25_memory", "pg_tsvector"] = "pg_tsvector"
    # ts_rank 归一化阈值：低于此值的弱命中在 SQL WHERE 层直接过滤
    rag_sparse_min_rank: float = 0.1
    # 稠密分数离散度下限（top1-top2）：平带说明与文档集无关 → free chat
    rag_hybrid_min_spread: float = 0.015

    # --- Rerank 交叉编码器 ---
    rag_rerank_enabled: bool = True
    # local（本地 HF 模型）/ siliconflow（硅基 /v1/rerank 远端 API）
    rag_rerank_provider: Literal["local", "siliconflow"] = "siliconflow"
    rag_rerank_model: str = "BAAI/bge-reranker-v2-m3"
    # 硅基远端配置；留空则复用 LLM_API_BASE + "/rerank" 和 LLM_API_KEY
    rag_rerank_api_url: str = ""
    rag_rerank_api_key: str = ""
    rag_rerank_top_n: int = 5

    # --- Free Chat 阈值 ---
    # 稠密 top1 分数低于此值 → 判定为与文档集无关 → free chat
    rag_min_score: float = 0.4

    # Upload 文件上传存储路径配置
    upload_dir: str = "./data/uploads"
    max_upload_size_mb: int = 50
    # 允许上传的文件扩展名（逗号分隔，与 pipeline.py 支持的格式保持一致）
    allowed_extensions: list[str] = [
        ".pdf",
        ".txt",
        ".md",
        ".docx",
        ".csv",
        ".html",
        ".xlsx",
        ".pptx",
        ".toml",
    ]

    # 多进程部署配置
    web_concurrency: int = 4

    # Celery 异步任务队列配置（为空字符串时不启用 Celery，回退 BackgroundTasks）
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    # celery异步处理开关
    use_celery_task: bool = False

    # CORS（逗号分隔，生产环境必须覆盖；允许从 CORS_ORIGINS 环境变量读取）
    cors_origins: list[str] = ["*"]

    # 限流配置
    rate_limit_enabled: bool = False
    rate_limit_default: str = "30/minute"
    rate_limit_llm_query: str = "10/minute"
    rate_limit_login: str = "5/minute"
    rate_limit_upload: str = "5/minute"

    # Redis BM25 缓存版本号键的 TTL（秒）：文档变更时 mark_bm25_data_changed 会把数据版本号时间戳 setex 到 Redis（键 bm25:ts:<user>）。查询时对比本地 _bm25_ts_map（上次重建时间）与该版本号：本地产出 >= Redis 版本号，命中本地缓存；否则失效从 DB 重建。该 TTL 仅作版本号键的过期兜底（过期则退化为本地时间戳短 TTL 兜底），并非缓存本身的生命周期。仅在 celery_broker_url 配置了 Redis 且 rag_sparse_backend=bm25_memory 时生效。
    redis_bm25_cache_ttl_seconds: int = 300

    # ===== 文档合规审查模块（app/compliance/）=====
    # false 时不加载审查路由/模型，不影响原有功能
    compliance_enabled: bool = True
    # 审查用 LLM 模型；为空则复用 llm_model
    compliance_llm_model: str = ""
    # 审查用低温度，降低幻觉
    compliance_llm_temperature: float = 0.1
    # 法规向量库独立 collection（与业务文档 documents 隔离）
    compliance_vector_collection: str = "compliance_regulations"
    # 自反思最大重试次数（图内 reflect → review 回炉）
    compliance_reflect_max_retry: int = 3
    # Celery 任务级重试（网络/LLM 瞬时错误时重投队列，独立于图内重试）
    compliance_task_max_retries: int = 2
    # 自反思质量阈值
    compliance_quality_threshold: float = 0.7
    # 法规检索 Top-K
    compliance_rag_top_k: int = 10
    # Playbook 语义匹配阈值
    compliance_playbook_semantic_threshold: float = 0.8  # deprecated — semantic 引擎未使用
    # 引用强制校验的原文相似度阈值（逐字匹配）
    compliance_citation_similarity_threshold: float = (
        0.95  # deprecated — citation_verifier 硬编码 0.8/0.5
    )
    # 人机协同开关（MVP 预留 interrupt，默认不中断）
    compliance_hitl_enabled: bool = True
    # 低风险是否自动确认
    compliance_hitl_auto_confirm_low: bool = True  # deprecated — "低风险自动确认"逻辑未实现
    # 审查报告存放目录（运行时）与法规原始文件目录
    compliance_report_dir: str = "./data/compliance/reports"
    compliance_regulation_dir: str = "./data/compliance/regulations"
    # 法规库默认合同类型（MVP 聚焦劳动合同）
    compliance_default_contract_type: str = "labor_contract"  # deprecated — review_service 硬编码

    @property
    def upload_path(self) -> Path:
        """获取上传文件路径的属性方法

        Returns:
            Path: 返回上传目录的Path对象，用于处理文件上传路径
        """
        return Path(self.upload_dir)

    @property
    def jwt_access_expire_seconds(self) -> int:
        """获取JWT访问令牌的过期时间（秒）

        Returns:
            int: 返回JWT访问令牌的过期时间，以秒为单位
        """
        # 将分钟转换为秒，返回秒数作为过期时间
        return self.jwt_access_token_expire_minutes * 60

    @property
    def jwt_refresh_expire_seconds(self) -> int:
        """获取JWT刷新令牌的过期时间（秒）

        Returns:
            int: 返回JWT刷新令牌的过期时间，单位为秒
        """
        # 将天转换为秒（86400秒=1天）
        return self.jwt_refresh_token_expire_days * 86400

    @model_validator(mode="after")
    def validate_production(self):
        """生产环境强制安全校验，防止默认值上线。"""
        if self.environment == "production":
            if self.jwt_secret_key == "dev-secret-key-do-not-use-in-production-123456":
                raise ValueError("生产环境必须修改 JWT_SECRET_KEY，禁止使用默认值")
            if self.cors_origins == ["*"]:
                raise ValueError("生产环境 CORS 不允许通配符，请设置 CORS_ORIGINS")
            if not self.vector_store_url:
                raise ValueError("生产环境必须配置 VECTOR_STORE_URL（PGVector 连接串）")
        return self


settings = Settings()
