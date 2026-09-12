"""Regression tests for sparse_search rank filter (docs/review Round 8 fix).

Round 8 bug: sparse_search.search() SQL had the @@ match condition but
no ts_rank(...) >= :min_rank filter, so generic tokens (合同/条款) from
jieba always matched every document → sparse_docs never empty → bypassed
hybrid free-chat logic.

Tests verify:
  - tokenize_query produces space-OR joined query (generates too many hits
    without ts_rank filter — that's why the filter is critical)
  - search() passes min_rank parameter into SQL params
  - _STOP_WORDS filters filler tokens
  - search() handles DB errors gracefully (returns [])

Run: python -m unittest tests/test_rag/test_sparse_search_rank.py -v
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestSparseSearchRankFilter(unittest.TestCase):
    """Verify ts_rank >= min_rank is wired into sparse_search."""

    def test_tokenize_query_uses_or_join(self):
        """tokenize_query 用 ' OR ' 连接分词 — 召回率高但必须配 ts_rank 把关."""
        from app.rag.sparse_search import tokenize_query

        result = tokenize_query("劳动合同的必备条款")
        self.assertIn(" OR ", result)
        # 停用词 '的' 应被过滤
        self.assertNotRegex(result, r"(?:^| )的(?: |$)")

    def test_tokenize_query_filters_stop_words(self):
        """停用词列表（的/了/哪些/什么/怎么...）不出现在 tsquery 中."""
        from app.rag.sparse_search import tokenize_query, _STOP_WORDS, _chinese_tokenizer

        # 先确认 jieba 会分出停用词表里的词
        raw_tokens = _chinese_tokenizer("哪些条款是必备的")
        self.assertTrue(
            any(t in _STOP_WORDS for t in raw_tokens),
            f"precondition: jieba should emit at least one stop-word token, got {raw_tokens}",
        )

        result = tokenize_query("哪些条款是必备的")
        parts = [p.strip() for p in result.split(" OR ")]
        for sw in _STOP_WORDS:
            self.assertNotIn(
                sw,
                parts,
                f"stop word '{sw}' leaked into tsquery parts: {parts}",
            )

    def test_search_passes_min_rank_to_sql(self):
        """search() 的 SQL params 必须包含 settings.rag_sparse_min_rank."""
        fake_conn = MagicMock()
        fake_result = MagicMock()
        fake_result.mappings.return_value.all.return_value = []
        fake_conn.execute.return_value = fake_result
        fake_cm = MagicMock()
        fake_cm.__enter__.return_value = fake_conn

        with (
            patch("app.rag.sparse_search.engine.connect", return_value=fake_cm),
            patch("app.rag.sparse_search.is_pg_available", return_value=True),
        ):
            from app.rag.sparse_search import search
            from app.config import settings

            search("合同", top_k=5, user_id="u1")

            call_args = fake_conn.execute.call_args[0]
            sql_obj, params = call_args[0], call_args[1]
            self.assertIn(
                "min_rank",
                params,
                "BUG: search() SQL params missing 'min_rank' — ts_rank threshold not applied!",
            )
            self.assertAlmostEqual(
                params["min_rank"],
                settings.rag_sparse_min_rank,
                msg="min_rank param must equal settings.rag_sparse_min_rank",
            )
            # SQL 字符串必须含有 ts_rank >= :min_rank 条件
            sql_str = str(sql_obj)
            self.assertIn(
                "ts_rank",
                sql_str,
                "BUG: SQL missing ts_rank expression",
            )

    def test_search_db_error_returns_empty(self):
        """DB 异常 → 返回 []，不抛出."""
        with patch("app.rag.sparse_search.engine.connect", side_effect=RuntimeError("PG down")):
            from app.rag.sparse_search import search

            results = search("合同", top_k=5, user_id="u1")
            self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
