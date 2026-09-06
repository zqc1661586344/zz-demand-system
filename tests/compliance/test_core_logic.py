"""Tests for compliance P2 bugfixes (reflect quality scoring + citation verifier).

These tests are pure-logic / deterministic — no DB, no LLM, no network.
Run with: python -m pytest tests/compliance/ -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.compliance.knowledge.citation_verifier import (
    normalize_text,
    text_similarity,
    verify_citation,
    verify_references,
)


class TestReflectQualityScoring:
    """Test coverage_score / avg_confidence logic after P2 #11 fix.

    Before fix: clauses + no risks → coverage_score=0.4 → quality too low → false retry.
    After fix:  clauses + no risks → coverage_score=1.0, avg_conf=0.9 → quality ~0.95 → no retry.
    """

    @staticmethod
    def _compute_quality(
        clauses_count: int, risks_count: int, avg_conf: float = 0.5, retry: int = 0
    ):
        if not clauses_count:
            coverage = 0.2
        else:
            coverage = 1.0

        if risks_count > 0:
            conf = avg_conf
        elif clauses_count > 0:
            conf = 0.9
        else:
            conf = 0.5

        decay = max(0.0, 0.15 * retry)
        return max(0.1, (coverage * 0.5 + conf * 0.5) - decay)

    def test_clean_contract_high_quality(self):
        """A contract with many clauses and no risks should score high (>= threshold 0.7)."""
        q = self._compute_quality(clauses_count=12, risks_count=0)
        assert q >= 0.85, f"clean contract quality should be ~0.95, got {q}"

    def test_risky_contract_quality(self):
        """A contract with risks should use actual avg_confidence."""
        q = self._compute_quality(clauses_count=10, risks_count=3, avg_conf=0.75)
        assert q == 0.875, f"expected 0.875, got {q}"

    def test_empty_clauses_low_quality(self):
        """No clauses parsed → genuinely low quality → triggers retry correctly."""
        q = self._compute_quality(clauses_count=0, risks_count=0)
        assert q == 0.35, f"expected 0.35, got {q}"

    def test_decay_with_retry(self):
        """Each retry adds 0.15 decay."""
        base = self._compute_quality(clauses_count=10, risks_count=0)
        retry1 = self._compute_quality(clauses_count=10, risks_count=0, retry=1)
        retry2 = self._compute_quality(clauses_count=10, risks_count=0, retry=2)
        assert round(base - retry1, 2) == 0.15
        assert round(retry1 - retry2, 2) == 0.15

    def test_quality_never_below_min(self):
        """Decay should not push quality below 0.1."""
        q = self._compute_quality(clauses_count=0, risks_count=0, retry=100)
        assert q == 0.1


class TestCitationVerifier:
    """Deterministic tests for citation_verifier (防幻觉核心组件)."""

    def test_normalize_text_removes_whitespace(self):
        assert normalize_text("  Hello  World \n  ") == "helloworld"

    def test_normalize_text_fullwidth_to_halfwidth(self):
        assert normalize_text("ＡＢＣ") == "abc"

    def test_text_similarity_identical(self):
        assert text_similarity("hello world", "hello world") == 1.0

    def test_text_similarity_empty(self):
        assert text_similarity("", "something") == 0.0

    def test_verify_citation_match(self):
        refs = [{"ref_content": "劳动合同法第十条规定建立劳动关系应当订立书面劳动合同"}]
        candidates = [{"content": "劳动合同法第十条规定建立劳动关系应当订立书面劳动合同"}]
        verified = verify_references(refs, candidates)
        assert verified[0]["verified"] is True

    def test_verify_citation_mismatch(self):
        refs = [{"ref_content": "完全不相关的引用内容ABCDE"}]
        candidates = [{"content": "真实的劳动合同法条文XYZ"}]
        verified = verify_references(refs, candidates)
        assert verified[0]["verified"] is False
        assert verified[0]["needs_human_check"] is True

    def test_verify_citation_empty_pool(self):
        """空法规库 → 全部 unverified（降级）。"""
        refs = [{"ref_content": "任何内容"}]
        verified = verify_references(refs, [])
        assert verified[0]["verified"] is False

    def test_verify_citation_short_content(self):
        """过短引用（<10 字）无法支撑逐字匹配 → unverified。"""
        refs = [{"ref_content": "第十条"}]
        candidates = [{"content": "第十条规定建立劳动关系应当订立书面劳动合同"}]
        verified = verify_references(refs, candidates)
        assert verified[0]["verified"] is False
