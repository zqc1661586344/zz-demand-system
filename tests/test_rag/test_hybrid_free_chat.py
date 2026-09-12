"""Regression tests for hybrid_search free-chat fallback (docs/review Round 8 fix).

这些测试直接 mock 掉所有 DB/网络/向量库，只验证 hybrid_search 的决策逻辑：
  - dense-only 路径：top1 < min_score → free chat
  - hybrid 路径：sparse 有弱命中，但 dense top1 < min_score → 仍然 free chat
    （这是 Round 8 修的 bug：之前只检查 sparse 是否空，导致泛词永远命中
      → 永远走 hybrid → 永远不 free chat）
  - hybrid 路径：sparse 有真命中 + dense top1 >= min_score → 返回结果

Run: python -m unittest tests/test_rag/test_hybrid_free_chat.py -v
"""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from langchain_core.documents import Document


def _doc(content: str, doc_id: str = "d1") -> Document:
    return Document(page_content=content, metadata={"document_id": doc_id})


class TestHybridSearchFreeChat(unittest.TestCase):
    """Decision logic of hybrid_search under different sparse/dense score combos."""

    def _reset_ragers_globals(self):
        """Clean BM25 map / reranker caches before each test."""
        from app.rag import retrievers, rerankers

        with retrievers._bm25_lock:
            retrievers._bm25_map.clear()
            retrievers._bm25_ts_map.clear()
        rerankers._built_reranker = None
        rerankers._reranker_fail_ts = None

    # ------------------------------------------------------------------
    # Case A: dense-only path, no sparse hits
    # ------------------------------------------------------------------
    def test_dense_only_top1_below_min_score_returns_empty(self):
        """稀疏空 + dense top1=0.35 < min_score=0.4 → free chat."""
        self._reset_ragers_globals()

        with (
            patch("app.rag.retrievers._sparse_docs", return_value=[]),
            patch("app.rag.vector_store.similarity_search_with_relevance") as mock_sim,
        ):
            mock_sim.return_value = [
                (_doc("irrelevant"), 0.35),
                (_doc("also irrelevant"), 0.31),
            ]
            from app.rag.retrievers import hybrid_search

            results = hybrid_search("今天北京几点下雨", top_k=5, user_id="u1")
            self.assertEqual(results, [], "should return empty for unrelated query")

    def test_dense_only_top1_above_min_score_returns_results(self):
        """稀疏空 + dense top1=0.8 >= min_score + spread 足够 → 正常返回."""
        self._reset_ragers_globals()

        fake_docs = [_doc("劳动合同应当具备以下条款", "d1")] * 3
        fake_retriever = MagicMock()
        fake_retriever.invoke.return_value = fake_docs
        fake_vs = MagicMock()
        fake_vs.as_retriever.return_value = fake_retriever

        with (
            patch("app.rag.retrievers._sparse_docs", return_value=[]),
            patch("app.rag.vector_store.similarity_search_with_relevance") as mock_sim,
            patch("app.rag.vector_store.get_vector_store", return_value=fake_vs),
        ):
            mock_sim.return_value = [
                (_doc("劳动合同应当具备以下条款"), 0.80),
                (_doc("用人单位名称住所"), 0.72),
                (_doc("劳动合同期限"), 0.65),
            ]
            from app.rag.retrievers import hybrid_search

            results = hybrid_search("劳动合同的必备条款有哪些", top_k=5, user_id="u1")
            self.assertEqual(len(results), 3)

    def test_dense_only_spread_too_small_returns_empty(self):
        """稀疏空 + top1=0.5 >= min_score 但 spread=0.003 < threshold → free chat."""
        self._reset_ragers_globals()

        with (
            patch("app.rag.retrievers._sparse_docs", return_value=[]),
            patch("app.rag.vector_store.similarity_search_with_relevance") as mock_sim,
        ):
            mock_sim.return_value = [
                (_doc("几乎一样的内容A"), 0.500),
                (_doc("几乎一样的内容B"), 0.497),
            ]
            from app.rag.retrievers import hybrid_search

            results = hybrid_search("这个文档到底讲什么", top_k=5, user_id="u1")
            self.assertEqual(results, [])

    # ------------------------------------------------------------------
    # Case B: hybrid path — the critical regression (Round 8 bug)
    # ------------------------------------------------------------------
    def test_hybrid_sparse_hit_but_dense_top1_below_min_score_returns_empty(self):
        """稀疏有泛词弱命中 + dense top1=0.25 < min_score → 必须 free chat。

        This is the bug that was reported:
          用户问完全无关的问题 → jieba 分词出泛词（"合同"/"条款"）→
          tsvector OR 匹配到所有法规的泛词 → sparse_docs 非空 →
          进入 hybrid 路径 → 以前只判 dense 是否完全空（embedding 总会返回向量）
          → 永远不 free chat。

        Round 8 fix added the dense top1 < min_score check AFTER fusion.
        """
        self._reset_ragers_globals()

        weak_sparse = [_doc("合同", "d1"), _doc("条款", "d2")]
        fake_dense = [_doc("劳动合同应当具备以下条款", "d1")] * 2
        fake_retriever = MagicMock()
        fake_retriever.invoke.return_value = fake_dense
        fake_vs = MagicMock()
        fake_vs.as_retriever.return_value = fake_retriever

        with (
            patch("app.rag.retrievers._sparse_docs", return_value=weak_sparse),
            patch("app.rag.vector_store.similarity_search_with_relevance") as mock_sim,
            patch("app.rag.vector_store.get_vector_store", return_value=fake_vs),
            patch("app.rag.retrievers._maybe_rerank", return_value=None),
        ):
            mock_sim.return_value = [
                (_doc("劳动合同应当具备以下条款"), 0.25),
            ]
            from app.rag.retrievers import hybrid_search

            results = hybrid_search("今天北京几点下雨", top_k=5, user_id="u1")
            self.assertEqual(
                results,
                [],
                "BUG: sparse hit from generic terms (合同/条款) must NOT bypass free-chat "
                "when dense top1 is way below min_score",
            )

    def test_hybrid_both_significant_scores_returns_fused(self):
        """稀疏真命中 + dense top1=0.75 >= min_score → 正常 RRF 融合返回."""
        self._reset_ragers_globals()

        sparse = [
            _doc("劳动合同应当具备以下条款", "d1"),
            _doc("用人单位的名称住所和法定代表人", "d2"),
        ]
        dense = [
            _doc("劳动合同应当具备以下条款", "d1"),
            _doc("劳动合同期限工作内容和工作地点", "d3"),
        ]
        fake_retriever = MagicMock()
        fake_retriever.invoke.return_value = dense
        fake_vs = MagicMock()
        fake_vs.as_retriever.return_value = fake_retriever

        with (
            patch("app.rag.retrievers._sparse_docs", return_value=sparse),
            patch("app.rag.vector_store.similarity_search_with_relevance") as mock_sim,
            patch("app.rag.vector_store.get_vector_store", return_value=fake_vs),
            patch("app.rag.retrievers._maybe_rerank", return_value=None),
        ):
            mock_sim.return_value = [
                (_doc("劳动合同应当具备以下条款"), 0.75),
            ]
            from app.rag.retrievers import hybrid_search

            results = hybrid_search("劳动合同的必备条款有哪些", top_k=5, user_id="u1")
            self.assertGreaterEqual(len(results), 1, "should return fused results")


if __name__ == "__main__":
    unittest.main()
