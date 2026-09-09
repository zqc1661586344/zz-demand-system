#!/usr/bin/env python3
"""
从法规种子数据 (seed_data/) 自动生成 RAG 评估测试集。

对每条法规条文调用 LLM 生成 3-5 条自然语言 question + ground_truth，
输出 JSON 文件直接可喂给 eval_ragas.py --dataset。

用法:
    python scripts/gen_eval_dataset.py
    python scripts/gen_eval_dataset.py --per-article 5 --output ./datasets/compliance_rag_eval.json
    python scripts/gen_eval_dataset.py --max-articles 20
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.rag.llms import get_llm

SEED_GLOB = "app/compliance/knowledge/seed_data/**/*.json"

PROMPT_TEMPLATE = """你是一位精通中国劳动法和合同法的资深律师。以下是一条法规条文：

【法规名称】{regulation_title}
【条文编号】{article_number}
【条文内容】
{article_content}

任务：基于上述条文，生成 {num_questions} 条普通用户（企业 HR 或劳动者）可能会提出的自然语言法律问题，以及每条问题的参考答案。

要求：
1. 问题必须是真实的业务场景，不要出现"请根据法条回答"之类的提示
2. 问题应覆盖条文的核心要点，避免过于简单的复述
3. 参考答案必须严格基于条文原文，可适当补充条文间的逻辑关联，但不要引入条文外的法规
4. 输出严格的 JSON 数组格式，不要包含 markdown 代码块标记

输出格式：
[
  {{"question": "问题1", "ground_truth": "参考答案1"}},
  {{"question": "问题2", "ground_truth": "参考答案2"}}
]"""


def load_articles(seed_glob: str) -> list[dict]:
    articles = []
    for fpath in sorted(glob.glob(seed_glob, recursive=True)):
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        title = data.get("title") or data.get("name") or Path(fpath).stem
        for art in data.get("articles", []):
            content = art.get("content", "").strip()
            if not content or len(content) < 10:
                continue
            articles.append(
                {
                    "regulation_title": title,
                    "article_number": art.get("article_number", ""),
                    "content": content,
                    "source_file": fpath,
                }
            )
    return articles


def generate_for_article(
    llm, article: dict, num_questions: int, max_retries: int = 3
) -> list[dict]:
    prompt = PROMPT_TEMPLATE.format(
        regulation_title=article["regulation_title"],
        article_number=article["article_number"],
        article_content=article["content"],
        num_questions=num_questions,
    )

    for attempt in range(1, max_retries + 1):
        try:
            resp = llm.invoke(prompt).content.strip()
            start = resp.find("[")
            end = resp.rfind("]") + 1
            if start == -1 or end == 0:
                raise ValueError("no JSON array found")
            json_str = resp[start:end]
            items = json.loads(json_str)
            if not isinstance(items, list):
                raise ValueError("expected list")
            results = []
            for item in items:
                q = str(item.get("question", "")).strip()
                gt = str(item.get("ground_truth", "")).strip()
                if q and gt:
                    results.append({"question": q, "ground_truth": gt})
            return results
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1)
                continue
            print(f"    ⚠️  生成失败 ({article['article_number']}): {e}", file=sys.stderr)
            return []
    return []


def main():
    parser = argparse.ArgumentParser(description="从 seed_data 自动生成 RAG 评估测试集")
    parser.add_argument("--seed-glob", default=SEED_GLOB, help="法规种子 JSON 文件 glob 路径")
    parser.add_argument(
        "--per-article", type=int, default=3, help="每条条文生成的 question 数量 (default: 3)"
    )
    parser.add_argument("--max-articles", type=int, default=0, help="最多处理多少条条文 (0=全部)")
    parser.add_argument(
        "--output", default="./datasets/compliance_rag_eval.json", help="输出 JSON 文件路径"
    )
    args = parser.parse_args()

    print("🔧 初始化 LLM ...")
    llm = get_llm()

    articles = load_articles(args.seed_glob)
    if args.max_articles > 0:
        articles = articles[: args.max_articles]
    print(f"📚 加载 {len(articles)} 条法规条文")

    all_questions: list[dict] = []
    seen_questions: set[str] = set()
    failed_articles = 0

    for i, art in enumerate(articles, 1):
        label = f"{art['regulation_title']} {art['article_number']}"
        print(f"\n  [{i}/{len(articles)}] {label}")
        items = generate_for_article(llm, art, args.per_article)
        if not items:
            failed_articles += 1
        for item in items:
            q_key = item["question"].strip()
            if q_key not in seen_questions:
                seen_questions.add(q_key)
                all_questions.append(item)
        print(f"    → 生成 {len(items)} 条 (累计 {len(all_questions)})")
        time.sleep(0.3)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_questions, f, ensure_ascii=False, indent=2)

    print(f"\n{'=' * 60}")
    print(f"✅ 完成！")
    print(f"   输出文件: {out_path}")
    print(f"   总条数:   {len(all_questions)}")
    print(f"   失败条文: {failed_articles}/{len(articles)}")
    print(f"\n下一步:")
    print(f"   python scripts/eval_ragas.py rag --dataset {out_path}")


if __name__ == "__main__":
    main()
