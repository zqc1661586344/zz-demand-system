#!/usr/bin/env python3
"""轻量 Ragas 评估脚本 — 评估 RAG 问答链路 和 合规审查引用质量。

用法:
    # 1. 评估 RAG 问答链路（内置示例测试集 + query_rag 直接调用，无需启动后端）
    python scripts/eval_ragas.py rag

    # 2. 用自定义测试集文件（JSON: [{"question": "...", "ground_truth": "..."}]）
    python scripts/eval_ragas.py rag --dataset ./my_queries.json

    # 3. 评估合规审查引用质量（从数据库拉已完成的 review，评估 RiskItem.description
    #    是否真的基于 legal_references[].ref_content）
    python scripts/eval_ragas.py compliance

    # 4. 只跑指定指标
    python scripts/eval_ragas.py rag --metrics faithfulness,answer_relevancy

    # 5. 同时跑两个场景
    python scripts/eval_ragas.py all

依赖: pip install "ragas>=0.2.0,<0.3.0" datasets
"""

from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

_PATCHED = False


def _patch_langchain_community() -> None:
    """Monkey-patch: langchain-community 0.4.x 移除了 ChatVertexAI，
    但 ragas 0.2.x 仍 import 它。注入空模块绕过。"""

    global _PATCHED
    if _PATCHED:
        return
    vertexai_mod = types.ModuleType("langchain_community.chat_models.vertexai")
    vertexai_mod.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules["langchain_community.chat_models.vertexai"] = vertexai_mod
    _PATCHED = True


_patch_langchain_community()

import pandas as pd
from datasets import Dataset

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

GOLDEN_DIR = PROJECT_ROOT / "datasets" / "golden"

from ragas import evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

from app.config import settings
from app.rag.chain import query_rag
from app.rag.embeddings import get_embedding_model
from app.rag.llms import get_llm

# ---------------------------------------------------------------------------
# 内置示例测试集 — 劳动合同方向（项目默认 Playbook + 法规种子的业务域）
# 如果 RAG 问答知识库还没劳动合同相关文档，这组 query 会走 free_chat，
# 那 Faithfulness 会很低——恰好能暴露"知识库缺文档"的问题。
# ---------------------------------------------------------------------------

RAG_SAMPLE_DATASET = [
    {
        "question": "试用期最长不能超过多长时间？",
        "ground_truth": (
            "劳动合同期限三个月以上不满一年的，试用期不得超过一个月；"
            "劳动合同期限一年以上不满三年的，试用期不得超过二个月；"
            "三年以上固定期限和无固定期限的劳动合同，试用期不得超过六个月。"
        ),
    },
    {
        "question": "竞业限制的期限最长是多久？",
        "ground_truth": "竞业限制的期限不得超过二年。",
    },
    {
        "question": "用人单位可以在劳动合同中约定违约金吗？",
        "ground_truth": (
            "除服务期约定和竞业限制约定外，用人单位不得与劳动者约定由劳动者承担违约金。"
        ),
    },
    {
        "question": "违法解除劳动合同的赔偿金怎么计算？",
        "ground_truth": (
            "用人单位违法解除劳动合同的，应当按照经济补偿标准的二倍向劳动者支付赔偿金。"
            "经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。"
        ),
    },
    {
        "question": "劳动合同到期公司不续签需要支付经济补偿吗？",
        "ground_truth": (
            "劳动合同期满，用人单位不续签的，应当向劳动者支付经济补偿。"
            "但如果是劳动者主动不续签，则用人单位无需支付经济补偿。"
        ),
    },
]

METRIC_REGISTRY = {
    "faithfulness": faithfulness,
    "answer_relevancy": answer_relevancy,
    "context_recall": context_recall,
    "context_precision": context_precision,
}

# ---------------------------------------------------------------------------
# 评估场景 1: RAG 问答链路
# ---------------------------------------------------------------------------


def build_rag_dataset(test_cases: list[dict], top_k: int) -> Dataset:
    """跑一遍 query_rag，把 {question, ground_truth} 变成 Ragas 需要的
    {question, answer, contexts, ground_truth} HuggingFace Dataset。"""

    rows = []
    for case in test_cases:
        q = case["question"]
        gt = case.get("ground_truth", "")
        print(f"  🔍 query: {q[:50]}...")
        result = query_rag(q, top_k=top_k)

        contexts = [c["content"] for c in result.get("chunks", [])]
        free_chat = result.get("free_chat", False)

        if free_chat or not contexts:
            print(f"    ⚠️  free_chat=True 或 contexts 为空 — Faithfulness 预期低")

        rows.append(
            {
                "question": q,
                "answer": result["answer"],
                "contexts": contexts,
                "ground_truth": gt,
            }
        )

    return Dataset.from_list(rows)


# ---------------------------------------------------------------------------
# 评估场景 2: 合规审查引用质量
# ---------------------------------------------------------------------------


def build_compliance_dataset(review_id: str | None) -> Dataset:
    """从数据库拉已完成的 ComplianceReview，用 RiskItem.description 当 answer，
    legal_references[].ref_content 当 contexts，评估审查意见是否真基于法规原文。"""

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.compliance.models.review import (
        ComplianceReview,
        ComplianceRisk,
        ComplianceRiskReference,
    )

    engine = create_engine(settings.database_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        query = session.query(ComplianceReview).filter(ComplianceReview.status == "completed")
        if review_id:
            query = query.filter(ComplianceReview.id == review_id)
        review = query.first()

        if not review:
            print("  ❌ 没有找到已完成的 compliance review — 先跑一遍合规审查再来评估")
            print("     或者用 --review-id 指定一个具体的 review id")
            return Dataset.from_list([])

        risks = session.query(ComplianceRisk).filter(ComplianceRisk.review_id == review.id).all()

        rows = []
        for risk in risks:
            refs = (
                session.query(ComplianceRiskReference)
                .filter(ComplianceRiskReference.risk_id == risk.id)
                .all()
            )
            contexts = [r.ref_content for r in refs if r.ref_content]
            if not contexts:
                continue

            rows.append(
                {
                    "question": f"请针对该条款识别法律风险: {risk.clause_content or risk.clause_number}",
                    "answer": risk.description or "",
                    "contexts": contexts,
                    "ground_truth": "",
                }
            )

        if not rows:
            print(f"  ❌ Review {review.id} 没有带法规引用的 risk item")
            return Dataset.from_list([])

        print(f"  📊 review={review.id[:12]}...  risks_with_refs={len(rows)}")
        return Dataset.from_list(rows)

    finally:
        session.close()


# ---------------------------------------------------------------------------
# 核心: 调用 Ragas evaluate
# ---------------------------------------------------------------------------


def run_evaluation(
    dataset: Dataset,
    metrics: list[str],
    llm_wrapper: LangchainLLMWrapper,
    emb_wrapper: LangchainEmbeddingsWrapper,
    tag: str,
) -> pd.DataFrame:
    if len(dataset) == 0:
        print(f"\n⚠️  [{tag}] 数据集为空，跳过评估")
        return pd.DataFrame()

    selected = [METRIC_REGISTRY[m] for m in metrics if m in METRIC_REGISTRY]
    if not selected:
        print(f"\n❌ [{tag}] 没有可运行的指标: {metrics}")
        print(f"   可用: {list(METRIC_REGISTRY.keys())}")
        return pd.DataFrame()

    print(f"\n{'=' * 60}")
    print(f"🎯 [{tag}] 评估开始")
    print(f"   样本数: {len(dataset)}")
    print(f"   指标:   {[m.name for m in selected]}")
    print(f"{'=' * 60}")

    result = evaluate(
        dataset,
        metrics=selected,
        llm=llm_wrapper,
        embeddings=emb_wrapper,
    )

    df = result.to_pandas()

    metric_cols = [m for m in METRIC_REGISTRY if m in df.columns]

    print(f"\n📋 [{tag}] 汇总:")
    if metric_cols:
        print(df[metric_cols].mean().to_string())
    else:
        print("  (无可用指标列)")

    print(f"\n📄 [{tag}] 逐条详情:")
    question_col = (
        "user_input"
        if "user_input" in df.columns
        else ("question" if "question" in df.columns else None)
    )
    display_cols = ([question_col] if question_col else []) + metric_cols
    if display_cols:
        print(df[display_cols].to_string(index=False))
    else:
        print("  (无可用列)")

    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_golden_seed(json_paths: list[str]) -> int:
    """golden seed — 把 goldset JSON 导入 GoldenDataset 表。"""
    from app.database import SessionLocal
    from app.services.eval_service import seed_golden_from_json

    db = SessionLocal()
    try:
        total = 0
        for p in json_paths or [str(GOLDEN_DIR / "labor_contract.json")]:
            if not Path(p).exists():
                print(f"  ❌ 文件不存在: {p}")
                continue
            total += seed_golden_from_json(db, str(p))
        print(f"\n✅ golden seed 完成，共导入 {total} 条")
        return 0
    finally:
        db.close()


def _cmd_golden_list(domain: str | None) -> int:
    from app.database import SessionLocal
    from app.services.eval_service import list_golden

    db = SessionLocal()
    try:
        items = list_golden(db, domain=domain)
        if not items:
            print("  (空) 先用 `golden seed` 导入测试集")
            return 0
        print(f"共 {len(items)} 条 golden 题目:")
        for it in items:
            tags = ",".join(it["tags"]) if it["tags"] else "-"
            print(f"  [{it['id'][:8]}] {it['name']} (domain={it['domain'] or '-'}, tags={tags})")
        return 0
    finally:
        db.close()


def _metric_means(df: pd.DataFrame, metric_names: list[str]) -> dict:
    """从评估结果 DataFrame 提取各指标均值。"""
    means = {}
    for m in metric_names:
        if m in df.columns:
            try:
                means[m] = float(df[m].astype(float).mean())
            except (TypeError, ValueError):
                continue
    return means


def _cmd_run(argv: list[str]) -> int:
    """run <scene> [--threshold X] [--top-k N] [--dataset FILE] [--save CSV] — 跑评估、写 EvalRun、对比基线、返回退出码。

    退出码: 0=通过/无回归, 1=有回归或失败（便于 CI/脚本串联）。
    """
    parser = argparse.ArgumentParser(description="run — 跑 RAG 评估并对比基线")
    parser.add_argument("scene", nargs="?", default="rag", help="rag | compliance | all")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--save", type=Path, default=None)
    args = parser.parse_args(argv)

    return _run_eval_and_persist(
        scene=args.scene,
        metric_names=["faithfulness", "answer_relevancy", "context_recall", "context_precision"],
        threshold=args.threshold,
        trigger="cli",
        top_k=args.top_k,
        dataset_path=str(args.dataset) if args.dataset else None,
        save_to=args.save,
    )


def _cmd_history(scene: str | None) -> int:
    """history — 查看历史评估趋势。"""
    from app.database import SessionLocal
    from app.services.eval_service import get_history

    db = SessionLocal()
    try:
        runs = get_history(db, scene=scene, limit=30)
        if not runs:
            print("  (空) 还没有评估历史 — 先跑 `run rag`")
            return 0
        print(f"最近 {len(runs)} 次评估:")
        for r in runs:
            mv = ", ".join(f"{k}={v:.3f}" for k, v in r["metric_values"].items())
            print(
                f"  [{r['created_at'][:19]}] {r['scene']:10} {r['trigger']:6} "
                f"{r['status']:7} {mv or '(n/a)'} notes={r['notes'] or '-'}"
            )
        return 0
    finally:
        db.close()


def _run_eval_and_persist(
    *,
    scene: str,
    metric_names: list[str],
    threshold: float | None,
    trigger: str,
    top_k: int = 5,
    dataset_path: str | None = None,
    review_id: str | None = None,
    save_to: Path | None = None,
) -> int:
    """核心执行流程：构数据集 → 评估 → 写 EvalRun → 基线对比 → 返回退出码。

    供 _cmd_run 与旧式 main() 场景共用。
    """
    from app.services.eval_service import run_and_persist_eval

    print("🔧 初始化 LangChain LLM / Embedding ...")
    lc_llm = get_llm()
    lc_emb = get_embedding_model()
    llm_wrapper = LangchainLLMWrapper(lc_llm)
    emb_wrapper = LangchainEmbeddingsWrapper(lc_emb)

    print(f"   LLM:       {settings.llm_provider}")
    print(f"   Embedding: {settings.embedding_provider}")
    print(f"   RAG mode:  {settings.rag_search_type}")

    all_results: dict[str, pd.DataFrame] = {}
    metrics_by_scene: dict[str, dict] = {}

    if scene in ("rag", "all"):
        test_cases = RAG_SAMPLE_DATASET
        if dataset_path:
            with open(dataset_path, encoding="utf-8") as f:
                test_cases = json.load(f)
        print(f"\n🔎 构建 RAG 评估数据集 (top_k={top_k}, {len(test_cases)} 条 query)")
        dataset = build_rag_dataset(test_cases, top_k=top_k)
        df = run_evaluation(dataset, metric_names, llm_wrapper, emb_wrapper, tag="RAG")
        if not df.empty:
            all_results["rag"] = df
            metrics_by_scene["rag"] = _metric_means(df, metric_names)

    if scene in ("compliance", "all"):
        print("\n🔎 构建合规审查引用评估数据集")
        dataset = build_compliance_dataset(review_id=review_id)
        df = run_evaluation(dataset, metric_names, llm_wrapper, emb_wrapper, tag="Compliance")
        if not df.empty:
            all_results["compliance"] = df
            metrics_by_scene["compliance"] = _metric_means(df, metric_names)

    # 写入 EvalRun + 基线对比 + 回归判定
    exit_code = 0
    for sc, mv in metrics_by_scene.items():
        result = run_and_persist_eval(
            scene=sc,
            metric_values=mv,
            dataset_name=str(dataset_path) if dataset_path else "golden",
            trigger=trigger,
            threshold=threshold,
            notes=f"eval_ragas run scene={sc}",
        )
        print(f"\n⚖️  [{sc}] 基线对比 (threshold={result['threshold']}):")
        if result["baseline"]:
            for m, bv in result["baseline"].items():
                cv = mv.get(m)
                marker = "🔴 REGRESSION" if m in result["regressions"] else "🟢"
                print(f"    {m:20} base={bv:.3f}  now={cv:.3f} {marker}")
        else:
            print(f"    无基线（首次评估）")
        if result["regressions"]:
            exit_code = 1

    # 可选：保存 CSV
    if save_to and all_results:
        save_to.parent.mkdir(parents=True, exist_ok=True)
        for tag, df in all_results.items():
            path = save_to.with_name(f"{save_to.stem}_{tag}{save_to.suffix}")
            df.to_csv(path, index=False, encoding="utf-8-sig")
            print(f"\n💾 [{tag}] 已保存: {path}")

    print(f"\n{'✅ 评估完成，无回归' if exit_code == 0 else '❌ 评估完成，检测到回归'}")
    return exit_code


def main():
    import sys as _sys

    # 新式命令路由：golden / run / history（保持旧式 scene 用法向后兼容）
    if len(_sys.argv) > 1 and _sys.argv[1] in ("golden", "run", "history"):
        if _sys.argv[1] == "golden":
            if len(_sys.argv) <= 2 or _sys.argv[2] == "list":
                return _cmd_golden_list(None)
            return _cmd_golden_seed(_sys.argv[3:])  # golden seed <paths...>
        if _sys.argv[1] == "history":
            return _cmd_history(None)
        return _cmd_run(_sys.argv[2:])  # run <scene> [--threshold ..]

    parser = argparse.ArgumentParser(
        description="Ragas 轻量评估 — RAG 问答链路 + 合规审查引用质量",
    )
    parser.add_argument(
        "scene",
        choices=["rag", "compliance", "all"],
        help="评估场景: rag=RAG问答链路, compliance=合规审查引用质量, all=两者都跑",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="自定义 RAG 测试集 JSON 文件，格式: [{question, ground_truth}]",
    )
    parser.add_argument(
        "--metrics",
        default="faithfulness,answer_relevancy,context_recall,context_precision",
        help="逗号分隔的指标列表 (默认全部)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="RAG 检索 top_k (默认 5)",
    )
    parser.add_argument(
        "--review-id",
        default=None,
        help="合规评估时指定某个具体的 review id (默认取第一个 completed)",
    )
    parser.add_argument(
        "--save",
        type=Path,
        default=None,
        help="把评估结果保存为 CSV",
    )
    args = parser.parse_args()

    metric_names = [m.strip() for m in args.metrics.split(",") if m.strip()]

    dataset_path = str(args.dataset) if args.dataset else None
    rc = _run_eval_and_persist(
        scene=args.scene,
        metric_names=metric_names,
        threshold=None,
        trigger="cli",
        top_k=args.top_k,
        dataset_path=dataset_path,
        review_id=args.review_id,
        save_to=args.save,
    )
    sys.exit(rc)


if __name__ == "__main__":
    main()
