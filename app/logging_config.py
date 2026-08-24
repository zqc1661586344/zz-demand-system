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
        # 追加 %(filename)s:%(lineno)d 定位日志来源（文件:行号），便于排查定位。
        fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(filename)s:%(lineno)d  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ---- Handlers ----
    handlers: list[logging.Handler] = []

    # 1. Console (stdout) handler — always present
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    # 挂噪声 Filter（见 _NoisyLoggerFilter 的注释说明为什么必须用 Filter）
    console_handler.addFilter(_NoisyLoggerFilter())
    handlers.append(console_handler)

    # 2. Rotating file handler — only when log_file is configured
    file_path = (settings.log_file or "").strip()
    if file_path:
        log_dir = Path(file_path).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=file_path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_NoisyLoggerFilter())
        handlers.append(file_handler)

    # ---- Root logger ----
    logging.basicConfig(
        level=level,
        handlers=handlers,
        force=True,  # replace any previous config
    )

    # 追加 logger 级别抑制（双重保障）：某些库的日志如果绕过 root handler（例如直接给自己的 logger 挂 handler 且 propagate=False），Filter 拦截不了，这里 setLevel(WARNING) 作第二层保障。
    for noisy in _NOISY_NAMES_FILTER:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configure_logging._done = True


# 某天需要调试某个库（比如想看 SQL 语句），只需从_NOISY_NAMES_FILTER 里临时注释掉对应项，重启即可恢复输出。
_NOISY_NAMES_FILTER: frozenset[str] = frozenset(
    {
        # HTTP 客户端 / 连接池
        "httpx",
        "httpcore",
        "urllib3",
        # Chroma 内部组件（telemetry 已在 vector_store.py 关闭，其余组件也一并静音）
        "chromadb",
        "chromadb.telemetry.product.posthog",
        "chromadb.segment.impl.metadata",
        # SQLAlchemy — engine 层面的 SQL 明细已靠 echo=False 解决，这里兜底
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.engine.Engine",
        # PostHog SDK（与 Chroma telemetry 配套，兜底静音）
        "posthog",
    }
)


class _NoisyLoggerFilter(logging.Filter):
    """把已知嘈杂的第三方日志器产生的日志完全过滤掉（任何级别）。

    为什么用 Filter 而不是仅靠 setLevel：
    - setLevel(WARNING) 会被一些库在自己初始化时悄悄改回去（posthog、httpx 等
      会在构建 client 时重置自己的 logger 级别），导致打地鼠。
    - 而 Filter 附着在每个 handler 上。消息无论从哪个 logger 发出，只要走到
      handler，就会被这里的 filter() 判据拦截；库再改 logger level 也绕不过 handler
      层的 Filter，从而一劳永逸。

    因而 Filter 是第一道防线（handler 层），setLevel(WARNING) 是第二道（logger 层）。
    二者互不依赖、任一生效即可静音。

    覆盖范围语义：只要是 _NOISY_NAMES_FILTER 中 logger 及其所有子 logger
    （name 以 "xxx." 开头）的日志，一律丢弃，不论级别（DEBUG/INFO/WARNING/ERROR）。
    因为这类库输出与应用的正常运行无关（如 posthog SDK 版本兼容性报错），
    留在日志中只会干扰排障。若某天需要调试某个库，可临时移除对应项。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # 完全拦截：命中噪声列表的 logger 一律丢弃
        for noisy in _NOISY_NAMES_FILTER:
            if record.name == noisy or record.name.startswith(noisy + "."):
                return False
        return True


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
