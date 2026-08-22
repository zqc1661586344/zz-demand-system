"""Application configuration using pydantic-settings."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 配置模型，用于从环境变量加载配置。没有设置 case_sensitive=True —默认大小写不敏感。Pydantic v2的元配置机制，在类定义时由 Pydantic的元类（metaclass）读取。
    model_config = SettingsConfigDict(
        env_file=".env",  # 指定环境变量文件
        env_file_encoding="utf-8",  # 环境变量文件编码
        extra="ignore",  # 忽略额外的配置项
    )

    # App信息配置
    app_name: str = "Enterprise RAG System"
    app_debug: bool = True
    log_level: str = "INFO"

    # Database配置，当前项目选择使用sqlite
    database_url: str = "sqlite:///./data/app.db"

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

    # LLM 使用ollama加载的模型配置
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_embedding_model: str = "BAAI/bge-m3"

    # Embedding 模型配置
    embedding_provider: Literal["openai", "ollama", "test"] = "openai"
    embedding_api_key: str = ""
    embedding_api_base: str = "https://api.openai.com/v1"
    embedding_model: str = "BAAI/bge-m3"

    # Chroma 向量数据库配置
    chroma_collection_name: str = "documents"
    chroma_persist_dir: str = "./data/chroma"

    # 检索算法配置：similarity（默认）/ mmr（多样性）/ hybrid（向量+BM25+RRF融合）
    rag_search_type: Literal["similarity", "mmr", "hybrid"] = "hybrid"

    # Hybrid RAG 稠密向量 vs 稀疏关键词权重（0=纯BM25, 1=纯向量）
    rag_hybrid_alpha: float = 0.5

    # 是否启用 bge-reranker 交叉编码器重排（需 transformers + torch）
    rag_rerank_enabled: bool = False
    # 重排器模型名
    rag_rerank_model: str = "BAAI/bge-reranker-v2-m3"
    # 重排后保留的 top_n 结果
    rag_rerank_top_n: int = 5

    # RAG 相关性阈值：检索结果的相关性分数（0~1，越高越相关）低于该值时，
    # 视为"文档中找不到相关内容"，回退到大模型自由聊天。
    rag_min_score: float = 0.4

    # Upload 文件上传存储路径配置
    upload_dir: str = "./data/uploads"
    max_upload_size_mb: int = 50

    # Chunking 配置
    chunk_size: int = 800
    chunk_overlap: int = 150

    @property
    def chroma_persist_path(self) -> Path:
        """获取Chroma持久化存储路径的方法

        返回:
            Path: Chroma数据库持久化存储的路径对象，该路径指向chroma_persist_dir属性指定的目录
        """
        return Path(self.chroma_persist_dir)  # 将chroma_persist_dir转换为Path对象并返回

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


settings = Settings()
