"""RAG 自动化评估 ORM models.

- GoldenDataset: 固定评估集（golden dataset），题目 + 标准答案。
- EvalRun:       一次 Ragas 评估的历史快照，用于基线对比与趋势回顾。

评估为低频低量操作，EvalRun 按 settings.rag_eval_keep_recent 保留最近 N 轮，
每次写入后由 _prune_old_runs 清理更久历史。
"""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


def _json_dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


class GoldenDataset(Base):
    """固定评估集条目：一条评估题 + 标准答案。"""

    __tablename__ = "golden_datasets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    question = Column(Text, nullable=False)
    ground_truth = Column(Text, nullable=False)
    domain = Column(String(100), nullable=True)  # 如 "labor_contract"
    tags_json = Column("tags", Text, nullable=True)  # JSON list
    enabled = Column(Integer, default=1)  # 是否参与评估
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class EvalRun(Base):
    """一次评估执行的历史快照。"""

    __tablename__ = "eval_runs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    trigger = Column(String(20), default="cli")  # manual | auto | cli
    scene = Column(String(20), default="rag")  # rag | compliance
    dataset_name = Column(String(255), nullable=True)
    params_json = Column("params", Text, nullable=True)  # JSON
    metric_values_json = Column("metric_values", Text, nullable=True)  # JSON
    status = Column(String(20), default="success")  # success | failed
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    @property
    def params(self) -> dict:
        return json.loads(self.params_json) if self.params_json else {}

    @property
    def metric_values(self) -> dict:
        return json.loads(self.metric_values_json) if self.metric_values_json else {}


def record_eval_run(
    db,
    *,
    trigger: str,
    scene: str,
    dataset_name: str | None,
    params: dict | None,
    metric_values: dict | None,
    status: str = "success",
    notes: str | None = None,
    keep_recent: int | None = None,
) -> EvalRun:
    """写入一条 EvalRun，并按 keep_recent 清理更旧的记录。"""
    run = EvalRun(
        id=str(uuid.uuid4()),
        trigger=trigger,
        scene=scene,
        dataset_name=dataset_name,
        params_json=_json_dumps(params or {}),
        metric_values_json=_json_dumps(metric_values or {}),
        status=status,
        notes=notes,
    )
    db.add(run)
    db.flush()

    from app.config import settings

    limit = keep_recent if keep_recent is not None else settings.rag_eval_keep_recent
    if limit and limit > 0:
        _prune_old_runs(db, scene=scene, keep_recent=limit)

    return run


def _prune_old_runs(db, *, scene: str, keep_recent: int) -> None:
    """保留每个 scene 最近 keep_recent 轮，清理更早记录。"""
    # 取该 scene 下最新 keep_recent 个 id，删除其余
    subq = (
        db.query(EvalRun.id)
        .filter(EvalRun.scene == scene)
        .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
        .limit(keep_recent)
    )
    keep_ids = [rid for (rid,) in db.execute(subq).all()]
    db.query(EvalRun).filter(EvalRun.scene == scene, ~EvalRun.id.in_(keep_ids)).delete(
        synchronize_session=False
    )


def latest_eval_run(db, *, scene: str) -> EvalRun | None:
    """返回某场景最近一次的 EvalRun（作基线）。"""
    from app.config import settings

    window = settings.rag_eval_baseline_window
    runs = (
        db.query(EvalRun)
        .filter(EvalRun.scene == scene)
        .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
        .limit(window)
        .all()
    )
    if not runs:
        return None
    # baseline-window=1 时直接用最近一次；>1 时取均值
    if window <= 1:
        return runs[0]
    return _averaged_run(runs)


def _averaged_run(runs: list) -> EvalRun:
    """把最近 N 次 metric_values 按均值合成一个"虚拟" EvalRun。"""
    import pandas as pd

    frames = [pd.Series(r.metric_values) for r in runs]
    avg = {}
    df = pd.concat(frames, axis=1).T
    for col in df.columns:
        try:
            avg[col] = float(df[col].astype(float).mean())
        except (TypeError, ValueError):
            continue
    base = runs[0]
    base.metric_values_json = _json_dumps(avg)
    return base
