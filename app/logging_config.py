"""统一日志配置 — 为整个应用程序提供集中式日志记录。
在任何模块（后端或前端）中的使用：

    from app.logging_config import get_logger

    logger = get_logger(__name__)

    logger.info("...")
    logger.warning("...")
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from app.config import settings


def _configure_logging() -> None:
    """一次性应用统一的日志配置。"""
    if getattr(_configure_logging, "_done", False):
        return

    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # ---- Formatter ----
    formatter = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ---- Handlers ----
    handlers: list[logging.Handler] = []

    # 1. Console (stdout) handler — always present
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)

    # 2. Rotating file handler — only when log_file is configured
    file_path = (settings.log_file or "").strip()
    if file_path:
        log_dir = Path(file_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        # 内置的日志轮转器会自动处理日志文件名和大小
        file_handler = logging.handlers.RotatingFileHandler(
            filename=file_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    # ---- Root logger ----
    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,  # replace any previous config
    )

    # Suppress noisy third-party logs at DEBUG level
    if level <= logging.DEBUG:
        for noisy in ("httpx", "httpcore", "urllib3", "chromadb"):
            logging.getLogger(noisy).setLevel(logging.WARNING)

    _configure_logging._done = True


def get_logger(name: str) -> logging.Logger:
    """获取一个应用统一配置的日志记录器实例。
    logger = get_logger(__name__)

    Args:
        name: 通常为“__name__”（模块级日志记录器）。传递一个点路径，如“app.api.conversations”，以获取根日志记录器的子级。

    Returns:
        一个`logging.Logger`类，其级别和处理程序来自统一配置。
    """
    if not getattr(_configure_logging, "_done", False):
        _configure_logging()
    return logging.getLogger(name)
