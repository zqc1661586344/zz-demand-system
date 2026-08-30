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

    # 检索算法配置：similarity（默认）/ mmr（多样性）/ hybrid（向量+BM25+RRF融合）
    rag_search_type: Literal["similarity", "mmr", "hybrid"] = "hybrid"

    # Hybrid RAG 稠密向量 vs 稀疏关键词权重（0=纯BM25, 1=纯向量）
    rag_hybrid_alpha: float = 0.5

    # 稀疏检索后端：bm25_memory（进程内 BM25，全量载入内存）/ pg_tsvector（PG 原生
    # tsvector + ts_rank + GIN，增量、零内存驻留）。
    # 仅当 database_url 指向 PostgreSQL 时才可用 pg_tsvector；SQLite 环境自动回退 bm25_memory。
    rag_sparse_backend: Literal["bm25_memory", "pg_tsvector"] = "pg_tsvector"

    # Hybrid 检索时稠密分数的离散度下限：top-1 与 top-2 的分数差低于此值，
    # 说明检索结果没有区分度（平带），判定为 query 与文档集无关，回退 free chat。
    # bge-m3 的分数被压缩在窄区间内，不相关的 query 也会打出 0.44~0.50 的分数，
    # 仅靠绝对阈值拦不住，需要看 spread 来识别"无命中"。
    rag_hybrid_min_spread: float = 0.015

    # 稀疏检索（pg_tsvector 后端）的 ts_rank 下限：低于此值的"命中"视为弱命中，
    # 在 hybrid 稀疏命中分支被过滤掉，防止仅靠个别泛词共现的无关 chunk 混入 RRF。
    # 该阈值仅作用于 pg_tsvector 后端（其 SQL 算出的 ts_rank r）；bm25_memory 回退不加下限。
    rag_sparse_min_rank: float = 0.1

    # 是否启用 bge-reranker 交叉编码器重排（需 transformers + torch）
    rag_rerank_enabled: bool = False
    # 重排器模型名
    rag_rerank_model: str = "BAAI/bge-reranker-v2-m3"
    # 重排后保留的 top_n 结果
    rag_rerank_top_n: int = 5

    # RAG 相关性阈值：检索结果的相关性分数（0~1，越高越相关）低于该值时，视为"文档中找不到相关内容"，回退到大模型自由聊天。
    rag_min_score: float = 0.4

    # Upload 文件上传存储路径配置
    upload_dir: str = "./data/uploads"
    max_upload_size_mb: int = 50
    # 允许上传的文件扩展名（逗号分隔，与 pipeline.py 支持的格式保持一致）
    allowed_extensions: list[str] = [".pdf", ".txt", ".md", ".docx"]

    # 多进程部署配置
    web_concurrency: int = 4
    # BM25 缓存绕过：多 worker 下每个进程独立缓存，设为 True 则每次从 DB 读取（正确但较慢）
    rag_bm25_cache_bypass: bool = False

    # Celery 异步任务队列配置（为空字符串时不启用 Celery，回退 BackgroundTasks）
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    # celery异步处理开关
    use_celery_task: bool = False

    # CORS（逗号分隔，生产环境必须覆盖；允许从 CORS_ORIGINS 环境变量读取）
    cors_origins: list[str] = ["*"]

    # 限流配置
    rate_limit_enabled: bool = True
    rate_limit_default: str = "30/minute"
    rate_limit_llm_query: str = "10/minute"
    rate_limit_login: str = "5/minute"
    rate_limit_upload: str = "5/minute"

    # Redis BM25 缓存时间戳 TTL（秒）：进程内 LRU 缓存通过 Redis 时间戳判断是否过期，
    # 在 TTL 内且时间戳一致则直接用缓存，否则从 DB 重建。
    # 仅当 celery_broker_url 配置了 Redis 时生效。
    redis_bm25_cache_ttl_seconds: int = 300
    chunk_size: int = 800
    chunk_overlap: int = 150

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
