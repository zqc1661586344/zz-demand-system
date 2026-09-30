"""RAG 自动化评估服务 — 评估执行、持久化、回归判定。

供 scripts/eval_ragas.py 与 app/api/eval.py 共用，避免逻辑重复。

核心职责：
  1. 跑一轮 Ragas 评估（复用 scripts/eval_ragas 里的构建/评估核心）
  2. 把结果写入 EvalRun（含当前 settings 参数快照）
  3. 与基线（最近一次/最近 N 次均值）对比，判定是否回归
  4. 返回值/退出码供 CLI 与 API 使用
"""

from __future__ import annotations

import json

from app.config import settings
from app.database import SessionLocal
from app.logging_config import get_logger
from app.models.eval import (
    EvalRun,
    GoldenDataset,
    latest_eval_run,
    record_eval_run,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 1. Golden Dataset 管理
# ---------------------------------------------------------------------------


def seed_golden_from_json(db, json_path: str) -> int:
    """把一个 golden JSON 文件导入 GoldenDataset 表（幂等：词名+问题重复则跳过）。

    支持两形态：
      - [{name, question, ground_truth, domain, tags}]
      - {name, domain, items: [question, ground_truth, ...]}（同域打包）
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    inserted = 0
    name = getattr(json_path, "name", json_path).split("/")[-1]

    items: list[dict] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        domain = data.get("domain", "")
        for it in data.get("items", []):
            items.append(
                {
                    "name": it.get("name", it.get("question")),
                    "question": it["question"],
                    "ground_truth": it["ground_truth"],
                    "domain": domain,
                }
            )

    for it in items:
        question = it["question"]
        gt = it.get("ground_truth", "")
        domain = it.get("domain")
        tags = it.get("tags")
        exists = db.query(GoldenDataset).filter(GoldenDataset.question == question).first()
        if exists:
            continue
        db.add(
            GoldenDataset(
                name=it.get("name") or question[:50],
                question=question,
                ground_truth=gt,
                domain=domain,
                tags_json=json.dumps(tags or [], ensure_ascii=False) if tags else None,
            )
        )
        inserted += 1

    db.commit()
    logger.info(
        "seeded %d golden items from %s (%d already existed)", inserted, name, len(items) - inserted
    )
    return inserted


def list_golden(db, domain: str | None = None) -> list[dict]:
    """列出 golden 测试集。"""
    q = db.query(GoldenDataset).filter(GoldenDataset.enabled == 1)
    if domain:
        q = q.filter(GoldenDataset.domain == domain)
    return [
        {
            "id": g.id,
            "name": g.name,
            "question": g.question,
            "domain": g.domain,
            "tags": json.loads(g.tags_json) if g.tags_json else [],
        }
        for g in q.order_by(GoldenDataset.created_at.asc()).all()
    ]


# ---------------------------------------------------------------------------
# 2. 回归判定（纯函数，便于单测）
# ---------------------------------------------------------------------------


def check_regression(new_metrics: dict, baseline_metrics: dict, threshold: float) -> dict:
    """对比新指标与基线，返回下跌超阈值的指标集合。

    new_metrics   当前这次评估的结果 {metric: value}
    baseline_metrics  基线结果 {metric: value}
    threshold     允许的最大下跌幅度（0~1）
    Returns: {metric: {"baseline":.., "current":.., "delta":..}}
    """
    regressions = {}
    for metric, val in (new_metrics or {}).items():
        base = (baseline_metrics or {}).get(metric)
        if base is None:
            continue
        try:
            delta = float(base) - float(val)
        except (TypeError, ValueError):
            continue
        if delta > threshold:
            regressions[metric] = {
                "baseline": round(float(base), 4),
                "current": round(float(val), 4),
                "delta": round(delta, 4),
            }
    return regressions


def run_and_persist_eval(
    *,
    scene: str,
    metric_values: dict,
    dataset_name: str | None = None,
    trigger: str = "cli",
    notes: str | None = None,
    threshold: float | None = None,
    failed: bool = False,
) -> dict:
    """把一次评估的 metric 结果写入 EvalRun，并与基线对比，返回回归判定与退出码建议。

    Returns: {
        run_id, status, metric_values,
        baseline, regressions (dict), threshold,
        passed (bool)
    }
    """
    params = {
        "top_k": getattr(settings, "rag_top_k", 5),
        "search_type": getattr(settings, "rag_search_type", "hybrid"),
        "multi_query_enabled": getattr(settings, "rag_multi_query_enabled", False),
        "multi_query_n": getattr(settings, "rag_multi_query_n", 3),
        "llm_provider": getattr(settings, "llm_provider", ""),
        "embedding_provider": getattr(settings, "embedding_provider", ""),
        "min_score": getattr(settings, "rag_min_score", 0.4),
    }
    th = threshold if threshold is not None else settings.rag_eval_regression_threshold

    db = SessionLocal()
    try:
        status = "failed" if failed else "success"
        run = record_eval_run(
            db,
            trigger=trigger,
            scene=scene,
            dataset_name=dataset_name,
            params=params,
            metric_values=metric_values if not failed else {},
            status=status,
            notes=notes,
        )
        db.commit()

        result: dict = {
            "run_id": run.id,
            "status": status,
            "metric_values": metric_values,
            "params": params,
            "baseline": {},
            "regressions": {},
            "threshold": th,
            "passed": True,
        }

        if not failed:
            baseline = latest_eval_run(db, scene=scene)
            # baseline 可能是虚拟对象（均值），需重新读 metric_values
            base_m = baseline.metric_values if baseline else {}
            # 排除自身（刚写入的最新一条即基线）：
            # latest_eval_run 可能取到刚写入的 run 本身，此时无意义
            if baseline and baseline.id == run.id:
                base_m = {}
            regressions = check_regression(metric_values, base_m, th)
            result["baseline"] = base_m
            result["regressions"] = regressions
            result["passed"] = not regressions
        else:
            result["passed"] = True

        return result
    finally:
        db.close()


def get_history(db, scene: str | None = None, limit: int = 20) -> list[dict]:
    """查看历史评估趋势。"""
    q = db.query(EvalRun).filter(EvalRun.scene == scene) if scene else db.query(EvalRun)
    runs = q.order_by(EvalRun.created_at.desc()).limit(limit).all()
    out = []
    for r in runs:
        out.append(
            {
                "id": r.id,
                "trigger": r.trigger,
                "scene": r.scene,
                "dataset_name": r.dataset_name,
                "status": r.status,
                "metric_values": r.metric_values,
                "params": r.params,
                "notes": r.notes,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return out
