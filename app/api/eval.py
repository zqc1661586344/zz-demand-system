"""RAG 自动化评估 API — 手动触发评估 + 查看历史。

评估本质是"跑 CLI 脚本"（scripts/eval_ragas.py），复用其 Ragas 逻辑与 EvalRun 持久化。
这里通过子进程触发 CLI（同步/后台），避免在 API 进程内临时装配 ragas 依赖。

角色：admin 可用。返回评估排队/结果，详情与历史走 GET 接口。
"""

import subprocess
import sys
from pathlib import Path

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import require_roles
from app.logging_config import get_logger
from app.models.user import User
from app.services.eval_service import get_history, run_and_persist_eval

logger = get_logger(__name__)

router = APIRouter(prefix="/api/eval", tags=["eval"])

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_EVAL_SCRIPT = _PROJECT_ROOT / "scripts" / "eval_ragas.py"


class EvalRunRequest(BaseModel):
    scene: Literal["rag", "compliance", "all"] = Field(
        default="rag", description="rag | compliance | all"
    )
    threshold: float | None = Field(default=None, ge=0, le=1)
    top_k: int = Field(default=5, ge=1, le=50)


def _build_cmd(req: EvalRunRequest) -> list[str]:
    cmd = [
        sys.executable,
        str(_EVAL_SCRIPT),
        "run",
        req.scene,
        "--top-k",
        str(req.top_k),
    ]
    if req.threshold is not None:
        cmd += ["--threshold", str(req.threshold)]
    return cmd


def _run_eval_in_subprocess(req: EvalRunRequest) -> None:
    """同步运行评估子进程（供多线程/后台调用）。

    成功后 CLI 自行写 EvalRun；失败（非零退出/异常/超时）这里补写一条
    status=failed 的 EvalRun，让 history 接口能看到明确失败原因，
    而不是只剩日志里的 stderr。
    """
    cmd = _build_cmd(req)
    logger.info("eval subprocess start: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=settings.rag_eval_subprocess_timeout,
        )
        logger.info("eval subprocess exit=%s", proc.returncode)
        if proc.returncode != 0:
            tail = proc.stderr[-2000:] if proc.stderr else proc.stdout[-2000:] or ""
            logger.warning("eval subprocess stderr:\n%s", tail)
            run_and_persist_eval(
                scene=req.scene,
                metric_values={},
                trigger="manual",
                notes=f"eval subprocess failed rc={proc.returncode}: {tail.strip()[:200]}",
                failed=True,
            )
    except Exception as e:  # noqa: BLE001
        logger.error("eval subprocess failed: %s", e)
        run_and_persist_eval(
            scene=req.scene,
            metric_values={},
            trigger="manual",
            notes=f"eval subprocess error: {e}",
            failed=True,
        )


@router.post("/run")
def run_eval(
    req: EvalRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """后台触发一次评估。返回排队状态，结果异步写入 EvalRun 后经 history 查看。"""
    # Celery 已启用 → 走 celery 任务；否则 BackgroundTasks 后台执行
    if settings.celery_broker_url and settings.use_celery_task:
        from app.rag.eval_task import run_eval_task

        run_eval_task.delay(
            scene=req.scene,
            threshold=req.threshold,
            top_k=req.top_k,
        )
        logger.info("eval queued via celery: scene=%s", req.scene)
    else:
        background_tasks.add_task(_run_eval_in_subprocess, req)

    return {
        "status": "queued",
        "scene": req.scene,
        "trigger": "manual",
        "note": "评估异步执行，结果写入 EvalRun，用 GET /api/eval/history 查看",
    }


@router.get("/history")
def eval_history(
    scene: str | None = Query(default=None, description="rag | compliance"),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """查看评估历史趋势。"""
    return get_history(db, scene=scene, limit=limit)
