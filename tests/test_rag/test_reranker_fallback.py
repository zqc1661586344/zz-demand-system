"""Regression tests for reranker cooldown/fallback (docs/review Round 7 fix).

Verifies the temp-cooldown mechanism replacing the permanent-disable bug:
  - _maybe_rerank catches reranker exceptions → calls mark_reranker_failed()
  - During cooldown window get_reranker() returns None (gracefully falls back)
  - After cooldown expires get_reranker() auto-rebuilds

Run: python -m unittest tests/test_rag/test_reranker_fallback.py -v
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestRerankerFallback(unittest.TestCase):
    """Cooldown behavior of reranker on transient failures."""

    def _reset_reranker_globals(self):
        from app.rag import rerankers

        rerankers._built_reranker = None
        rerankers._reranker_fail_ts = None

    # ------------------------------------------------------------------
    def test_mark_failed_sets_timestamp(self):
        """mark_reranker_failed() 写入 _reranker_fail_ts."""
        self._reset_reranker_globals()
        from app.rag import rerankers

        before = time.time()
        rerankers.mark_reranker_failed()
        after = time.time()

        self.assertIsNotNone(rerankers._reranker_fail_ts)
        self.assertGreaterEqual(rerankers._reranker_fail_ts, before)
        self.assertLessEqual(rerankers._reranker_fail_ts, after)

    # ------------------------------------------------------------------
    def test_get_reranker_returns_none_during_cooldown(self):
        """冷却期内 get_reranker() 返回 None."""
        self._reset_reranker_globals()
        from app.rag import rerankers

        rerankers._built_reranker = MagicMock()
        rerankers._reranker_fail_ts = time.time()

        result = rerankers.get_reranker()
        self.assertIsNone(result, "should return None during active cooldown")

    # ------------------------------------------------------------------
    def test_get_reranker_clears_fail_ts_after_cooldown(self):
        """冷却期过后 get_reranker() 清除 fail_ts 并返回缓存的 reranker."""
        self._reset_reranker_globals()
        from app.rag import rerankers

        mock_reranker = MagicMock()
        rerankers._built_reranker = mock_reranker
        rerankers._reranker_fail_ts = time.time() - rerankers._RERANK_COOLDOWN_SEC - 10

        result = rerankers.get_reranker()
        self.assertIs(result, mock_reranker, "should return cached reranker after cooldown expires")
        self.assertIsNone(
            rerankers._reranker_fail_ts,
            "fail_ts should be cleared after cooldown",
        )

    # ------------------------------------------------------------------
    def test_permanent_false_stays_permanent(self):
        """配置/依赖缺失导致 _built_reranker=False — 永不恢复（重启才清除）。"""
        self._reset_reranker_globals()
        from app.rag import rerankers

        rerankers._built_reranker = False
        rerankers._reranker_fail_ts = None

        # 即使设置了一个过期的 fail_ts，False 也优先
        rerankers._reranker_fail_ts = time.time() - 999999
        result = rerankers.get_reranker()
        self.assertIsNone(result)
        self.assertIs(rerankers._built_reranker, False)

    # ------------------------------------------------------------------
    def test_maybe_rerank_catches_exception_and_marks_failed(self):
        """_maybe_rerank 捕获 reranker 异常 → 调 mark_reranker_failed → 返回 None."""
        self._reset_reranker_globals()

        fake_reranker = MagicMock()
        fake_reranker.compress_documents.side_effect = RuntimeError("API timed out")

        with (
            patch("app.rag.rerankers.get_reranker", return_value=fake_reranker),
            patch("app.rag.rerankers.mark_reranker_failed") as mock_mark,
        ):
            from app.rag.retrievers import _maybe_rerank

            docs = []
            result = _maybe_rerank("query", docs)
            self.assertIsNone(result)
            mock_mark.assert_called_once()


if __name__ == "__main__":
    unittest.main()
