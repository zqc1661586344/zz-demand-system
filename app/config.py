"""Application configuration using pydantic-settings."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "Enterprise RAG System"
    app_debug: bool = True
    log_level: str = "INFO"

    # Database
    database_url: str = "sqlite:///./data/app.db"

    # JWT
    jwt_secret_key: str = "dev-secret-key-do-not-use-in-production-123456"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # LLM (OpenAI-compatible)
    llm_provider: Literal["openai", "ollama", "test"] = "openai"
    llm_api_key: str = ""
    llm_api_base: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # LLM (Ollama)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"

    # Embedding
    embedding_provider: Literal["openai", "ollama", "test"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    ollama_embedding_model: str = "nomic-embed-text"

    # Chroma
    chroma_persist_dir: str = "./data/chroma"

    # Upload
    upload_dir: str = "./data/uploads"
    max_upload_size_mb: int = 50

    # Chunking
    chunk_size: int = 800
    chunk_overlap: int = 150

    @property
    def chroma_persist_path(self) -> Path:
        return Path(self.chroma_persist_dir)

    @property
    def upload_path(self) -> Path:
        return Path(self.upload_dir)

    @property
    def jwt_access_expire_seconds(self) -> int:
        return self.jwt_access_token_expire_minutes * 60

    @property
    def jwt_refresh_expire_seconds(self) -> int:
        return self.jwt_refresh_token_expire_days * 86400


settings = Settings()