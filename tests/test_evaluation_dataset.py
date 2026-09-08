"""Structural checks on the evaluation datasets and schema helpers --
fast, no LLM, no DB. Catches a malformed dataset (missing field, an
unknown category typo) before a slow live benchmark run hits it.
"""
import json
from pathlib import Path

from evaluation.schema import RELATIONSHIP_CATEGORIES, category_for_result, make_fact

DATASETS_DIR = Path(__file__).resolve().parents[1] / "evaluation" / "datasets"


class TestMakeFact:
    def test_fills_required_fields_classify_pair_reads(self):
        fact = make_fact("Co", "revenue", "100", 100.0, "INR million", "FY24", None)
        for field in ("id", "page_number", "subject", "attribute", "value", "statement", "quote", "evidence_status"):
            assert field in fact

    def test_each_call_gets_a_distinct_negative_id(self):
        a = make_fact("Co", "x", "1")
        b = make_fact("Co", "y", "2")
        assert a["id"] != b["id"]
        assert a["id"] < 0 and b["id"] < 0


class TestCategoryForResult:
    def test_deterministically_confirmed_corroborates_is_exact(self):
        assert category_for_result("corroborates", "deterministic_confirmed") == "EXACT_CORROBORATION"

    def test_deterministically_overridden_corroborates_is_also_exact(self):
        assert category_for_result("corroborates", "deterministic_override") == "EXACT_CORROBORATION"

    def test_llm_unchecked_corroborates_is_kept_separate(self):
        assert category_for_result("corroborates", "llm_unchecked") == "SEMANTIC_CORROBORATION"

    def test_other_relation_types_map_directly(self):
        assert category_for_result("contradicts", None) == "CONTRADICTION"
        assert category_for_result("reconciled", None) == "CONTEXT_RECONCILIATION"
        assert category_for_result("unrelated", None) == "UNRELATED"
        assert category_for_result("uncertain", None) == "UNCERTAIN"
        assert category_for_result("insufficient_context", None) == "INSUFFICIENT_CONTEXT"
        assert category_for_result("related_but_not_comparable", None) == "RELATED_NOT_COMPARABLE"


class TestRelationshipBenchmarkDataset:
    """Regenerate with: python evaluation/datasets/build_relationship_benchmark.py"""

    def _load(self):
        return json.loads((DATASETS_DIR / "relationship_benchmark.json").read_text(encoding="utf-8"))

    def test_dataset_file_exists_and_is_nonempty(self):
        cases = self._load()
        assert len(cases) > 0

    def test_every_case_has_a_known_category(self):
        for case in self._load():
            assert case["category"] in RELATIONSHIP_CATEGORIES, f"{case['id']}: unknown category {case['category']!r}"

    def test_every_case_id_is_unique(self):
        ids = [c["id"] for c in self._load()]
        assert len(ids) == len(set(ids))

    def test_every_case_has_both_facts_with_required_fields(self):
        for case in self._load():
            for side in ("fact_a", "fact_b"):
                fact = case[side]
                for field in ("subject", "attribute", "value", "statement", "quote", "page_number"):
                    assert field in fact, f"{case['id']}.{side} missing {field!r}"

    def test_provenance_is_one_of_the_declared_kinds(self):
        for case in self._load():
            assert case["provenance"] in ("constructed", "real_mined")


class TestDeterministicOverrideDataset:
    def _load(self):
        return json.loads((DATASETS_DIR / "deterministic_override_cases.json").read_text(encoding="utf-8"))

    def test_every_case_supplies_a_synthetic_llm_proposal(self):
        for case in self._load():
            assert "llm_proposal" in case
            assert "relation_type" in case["llm_proposal"]

    def test_every_case_has_a_known_category(self):
        for case in self._load():
            assert case["category"] in RELATIONSHIP_CATEGORIES


class TestEvidenceBenchmarkDataset:
    def _load(self):
        return json.loads((DATASETS_DIR / "evidence_benchmark.json").read_text(encoding="utf-8"))

    def test_every_real_sample_item_has_a_fact_id_and_a_note(self):
        data = self._load()
        for item in data["real_sample"] + data["known_documented_cases"]:
            assert "fact_id" in item
            assert "should_validate" in item
            assert "note" in item and item["note"]
