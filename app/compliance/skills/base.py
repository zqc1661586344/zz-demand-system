"""Skill 基类 — 所有 compliance skills 的统一父类。

提供 execute 接口约定、成功/失败返回格式、日志入口。
约定所有 skill 的 execute 返回：
  - {"ok": True, "data": {...}}  — 成功
  - {"ok": False, "error": str}  — 失败（不抛异常，由调用方决定降级/中断）
"""

from app.logging_config import get_logger

logger = get_logger(__name__)


class SkillBase:
    """Skill 统一基类。子类实现 execute(self, ctx: dict) -> dict。"""

    name: str = "skill"

    def execute(self, ctx: dict) -> dict:
        raise NotImplementedError

    def ok(self, data=None) -> dict:
        return {"ok": True, "data": data}

    def err(self, error: str) -> dict:
        return {"ok": False, "error": error}

    def log(self, msg: str) -> None:
        logger.info("[%s] %s", self.name, msg)
