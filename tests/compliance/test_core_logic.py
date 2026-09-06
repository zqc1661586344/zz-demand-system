"""Tests for compliance P2 bugfixes (reflect quality scoring + citation verifier).

These tests are pure-logic / deterministic — no DB, no LLM, no network.
Run with: python -m pytest tests/compliance/ -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.compliance.knowledge.citation_verifier import (
    normalize_text,
    verify_citation,
    verify_references,
    _ref_coverage,
)
from app.compliance.harness.runtime import compute_reflect_quality


class TestReflectQualityScoring:
    """Test coverage_score / avg_confidence logic via real compute_reflect_quality()."""

    @staticmethod
    def _quality_from_counts(
        clauses_count: int,
        risks_count: int,
        avg_conf: float = 0.5,
        retry: int = 0,
        has_rules: bool = False,
    ):
        clauses = [{}] * clauses_count
        risks = [{"ai_confidence": avg_conf}] * risks_count if risks_count else []
        rules = [{"id": "r1"}] if has_rules else []
        q, cov, conf, degraded = compute_reflect_quality(
            clauses, risks, retry_count=retry, rules=rules
        )
        return q, cov, conf, degraded

    def test_clean_contract_with_rules_high_quality(self):
        """有 Playbook 规则 + 干净合同 → coverage=0.9 (clauses+no_risks 衰减) avg_conf=0.9 → quality=0.9。"""
        q, cov, conf, degraded = self._quality_from_counts(
            clauses_count=12, risks_count=0, has_rules=True
        )
        assert cov == 0.9, f"coverage should be 0.9 (clauses+no_risks decay), got {cov}"
        assert conf == 0.9, f"avg_conf should be 0.9 with rules, got {conf}"
        assert q == 0.9, f"expected 0.9, got {q}"
        assert "no_playbook_rules" not in degraded

    def test_clean_contract_no_rules_capped(self):
        """零 Playbook 规则 + 干净合同 → coverage=0.45, quality 上限 0.5（不能说合规）。"""
        q, cov, conf, degraded = self._quality_from_counts(
            clauses_count=12, risks_count=0, has_rules=False
        )
        assert cov == 0.45, f"coverage should be 0.45 (0.5*0.9), got {cov}"
        assert conf == 0.7, f"avg_conf should be 0.7 without rules, got {conf}"
        assert q <= 0.5, f"no-rules quality must be capped at 0.5, got {q}"
        assert "no_playbook_rules" in degraded
        assert "llm_only_no_rules_no_risks" in degraded

    def test_risky_contract_with_rules(self):
        """有规则 + 有风险 → avg_conf 用实际 confidence。"""
        q, cov, conf, degraded = self._quality_from_counts(
            clauses_count=10, risks_count=3, avg_conf=0.75, has_rules=True
        )
        assert conf == 0.75
        assert q == 0.875, f"expected 0.875, got {q}"

    def test_empty_clauses_low_quality(self):
        """无 clauses → coverage=0.2, 无规则再打 0.5 → coverage=0.1, quality=0.3。"""
        q, cov, conf, degraded = self._quality_from_counts(
            clauses_count=0, risks_count=0, has_rules=False
        )
        assert cov == 0.1, f"expected coverage 0.1, got {cov}"
        assert q == 0.3, f"expected quality 0.3, got {q}"
        assert "no_clauses_extracted" in degraded
        assert "no_playbook_rules" in degraded

    def test_decay_with_retry(self):
        """每次 retry 加 0.15 衰减（有规则场景）。"""
        base, _, _, _ = self._quality_from_counts(clauses_count=10, risks_count=0, has_rules=True)
        retry1, _, _, _ = self._quality_from_counts(
            clauses_count=10, risks_count=0, retry=1, has_rules=True
        )
        retry2, _, _, _ = self._quality_from_counts(
            clauses_count=10, risks_count=0, retry=2, has_rules=True
        )
        assert round(base - retry1, 2) == 0.15
        assert round(retry1 - retry2, 2) == 0.15

    def test_quality_never_below_min(self):
        """衰减不会低于 0.1。"""
        q, _, _, _ = self._quality_from_counts(
            clauses_count=0, risks_count=0, retry=100, has_rules=False
        )
        assert q == 0.1

    def test_degraded_has_verified_ref_when_present(self):
        """有 verified 引用时无 no_verified_reasons 警告。"""
        clauses = [{}] * 5
        risks = [
            {
                "ai_confidence": 0.9,
                "legal_references": [{"verified": True}],
            }
        ]
        rules = [{"id": "r1"}]
        _, _, _, degraded = compute_reflect_quality(clauses, risks, rules=rules)
        assert "no_verified_references" not in degraded
        assert "no_playbook_rules" not in degraded

    def test_real_function_imported_not_shadowed(self):
        """Sanity: compute_reflect_quality 是真实函数，不是影子副本。"""
        import inspect

        assert "compute_reflect_quality" in inspect.getsource(
            __import__("app.compliance.harness.runtime", fromlist=["compute_reflect_quality"])
        )


class TestCitationVerifier:
    """Deterministic tests for citation_verifier (防幻觉核心组件)."""

    def test_normalize_text_removes_whitespace(self):
        assert normalize_text("  Hello  World \n  ") == "helloworld"

    def test_normalize_text_fullwidth_to_halfwidth(self):
        assert normalize_text("ＡＢＣ") == "abc"

    def test_ref_coverage_identical(self):
        assert _ref_coverage("hello world", "hello world") == 1.0

    def test_ref_coverage_substring(self):
        """引用是原文摘录 → coverage=1.0（子串命中）。"""
        assert _ref_coverage("第十条规定", "劳动合同法第十条规定建立劳动关系") == 1.0

    def test_ref_coverage_no_overlap(self):
        assert _ref_coverage("abc", "xyz") == 0.0

    def test_verify_citation_substring_match(self):
        """引用是原文摘录 → verified（子串命中优先于 coverage）。"""
        refs = [{"ref_content": "劳动合同法第十条规定"}]
        candidates = [
            {"content": "中华人民共和国劳动合同法第十条规定建立劳动关系应当订立书面劳动合同"}
        ]
        verified = verify_references(refs, candidates)
        assert verified[0]["verified"] is True

    def test_verify_citation_full_match(self):
        refs = [{"ref_content": "劳动合同法第十条规定建立劳动关系应当订立书面劳动合同"}]
        candidates = [{"content": "劳动合同法第十条规定建立劳动关系应当订立书面劳动合同"}]
        verified = verify_references(refs, candidates)
        assert verified[0]["verified"] is True

    def test_verify_citation_mismatch(self):
        refs = [{"ref_content": "完全不相关的引用内容ABCDEFGHIJ"}]
        candidates = [{"content": "真实的劳动合同法条文"}]
        verified = verify_references(refs, candidates)
        assert verified[0]["verified"] is False
        assert verified[0]["needs_human_check"] is True

    def test_verify_citation_empty_pool(self):
        refs = [{"ref_content": "任何内容"}]
        verified = verify_references(refs, [])
        assert verified[0]["verified"] is False

    def test_verify_citation_short_reference_exact_match_only(self):
        """过短引用 (<15 归一化字符) 只走精确子串判定，coverage 不稳定。"""
        refs = [{"ref_content": "第十条"}]
        candidates = [{"content": "第十条规定建立劳动关系应当订立书面劳动合同"}]
        verified = verify_references(refs, candidates)
        assert verified[0]["verified"] is True

    def test_verify_citation_long_reference_coverage(self):
        """较长引用通过 coverage 比率验证。"""
        ref = "劳动合同法第22条规定用人单位为劳动者提供专项培训费用可以约定服务期"
        article = "劳动合同法第二十二条用人单位为劳动者提供专项培训费用对其进行专业技术培训的可以与该劳动者订立协议约定服务期劳动者违反服务期约定的应当按照约定向用人单位支付违约金"
        verified = verify_citation(ref, [{"content": article}])
        assert verified is True
