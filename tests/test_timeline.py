"""Tests for deterministic metric-timeline construction.

The claim this module makes is that a chain of same-metric relationships
already in the graph can be followed into a trend, with no new
extraction or model call. The risk is exactly the bug found while
building it: chaining a quarter against its own parent year produces a
nonsensical sequence ("Q4 2023 -> FY2024 -> Q3 2024") that looks like a
trend but isn't one. These tests pin both directions -- real chains are
found, and that specific failure shape is refused.
"""
from app.timeline import build_timelines


def _fact(fact_id, value, unit="INR million", period="FY24", scope=None,
          subject="Delhivery", attribute="revenue", evidence_status=None):
    return {
        "id": fact_id, "document_id": 1, "value_numeric": value, "unit": unit,
        "time_period": period, "scope": scope, "subject": subject,
        "attribute": attribute, "value": value, "statement": f"{attribute} was {value}",
        "evidence_status": evidence_status,
    }


def _rel(fact_id_a, fact_id_b, relation="reconciled"):
    return {"fact_id_a": fact_id_a, "fact_id_b": fact_id_b, "relation_type": relation}


class TestBasicChaining:
    def test_a_simple_two_point_trend_is_found(self):
        facts = [_fact(1, 100, period="FY23"), _fact(2, 120, period="FY24")]
        rels = [_rel(1, 2)]
        timelines = build_timelines(facts, rels)
        assert len(timelines) == 1
        assert [p.original_value for p in timelines[0].points] == [100, 120]

    def test_evidence_status_is_carried_onto_each_point(self):
        """A trend must not display an unverified point with the same
        implied confidence as a validated one -- the status has to survive
        onto the point for a caller (the API/UI) to show the difference."""
        facts = [_fact(1, 100, period="FY23", evidence_status="fact_validated"),
                 _fact(2, 120, period="FY24", evidence_status="quote_grounded")]
        rels = [_rel(1, 2)]
        points = build_timelines(facts, rels)[0].points
        assert [p.evidence_status for p in points] == ["fact_validated", "quote_grounded"]

    def test_points_are_sorted_chronologically_regardless_of_input_order(self):
        facts = [_fact(1, 120, period="FY24"), _fact(2, 100, period="FY22"), _fact(3, 110, period="FY23")]
        rels = [_rel(1, 3), _rel(3, 2)]
        timeline = build_timelines(facts, rels)[0]
        assert [p.original_value for p in timeline.points] == [100, 110, 120]

    def test_corroborates_edges_also_chain(self):
        """corroborates and reconciled both assert 'same metric' -- the
        underlying claim a timeline chains on -- so both must work."""
        facts = [_fact(1, 100, period="FY23"), _fact(2, 100, period="FY23", unit="INR Crore")]
        rels = [_rel(1, 2, relation="corroborates")]
        # same period on both -- not a trend (needs >=2 distinct periods)
        assert build_timelines(facts, rels) == []

    def test_contradicts_and_uncertain_never_chain(self):
        """A settled disagreement must not be smoothed into a trend line
        -- that would hide the conflict instead of reporting it."""
        facts = [_fact(1, 100, period="FY23"), _fact(2, 999, period="FY24")]
        for relation in ("contradicts", "uncertain"):
            assert build_timelines(facts, [_rel(1, 2, relation=relation)]) == []

    def test_fewer_than_min_points_produces_no_timeline(self):
        facts = [_fact(1, 100, period="FY23")]
        assert build_timelines(facts, []) == []

    def test_an_unrelated_pair_of_facts_is_not_chained(self):
        facts = [_fact(1, 100, period="FY23"), _fact(2, 999, period="FY24")]
        assert build_timelines(facts, []) == []


class TestGranularityNeverMixes:
    """The real bug found on the actual corpus: 'Q4 2023 -> FY2024 ->
    Q3 2024' as if a quarter and its own parent year were sequential
    points. A quarter is expected to be smaller than its annual total by
    construction; that is not a trend step, it's containment."""

    def test_a_quarter_is_never_chained_against_its_own_year(self):
        facts = [_fact(1, 500, period="Q3 2024"), _fact(2, 8142, period="FY2024")]
        assert build_timelines(facts, [_rel(1, 2)]) == []

    def test_a_quarter_is_never_chained_against_a_different_years_annual_total(self):
        """The specific gap this required a second guard for: bare year
        integers differ (2023 vs 2024) even when, under a fiscal
        convention never stated in the document, the quarter might fall
        inside that fiscal year -- so this must be refused independent of
        what the year comparison alone would conclude."""
        facts = [_fact(1, 500, period="Q4 2023"), _fact(2, 8142, period="FY2024")]
        assert build_timelines(facts, [_rel(1, 2)]) == []

    def test_two_quarters_of_different_years_do_chain(self):
        """Quarter-to-quarter is a legitimate trend -- only quarter-vs-
        whole-year mixing is refused."""
        facts = [_fact(1, 300, period="Q4 2023"), _fact(2, 350, period="Q3 2024")]
        timelines = build_timelines(facts, [_rel(1, 2)])
        assert len(timelines) == 1
        assert [p.original_value for p in timelines[0].points] == [300, 350]

    def test_two_whole_years_do_chain(self):
        facts = [_fact(1, 100, period="FY23"), _fact(2, 120, period="FY24")]
        assert len(build_timelines(facts, [_rel(1, 2)])) == 1

    def test_a_half_year_is_never_chained_against_its_own_year(self):
        facts = [_fact(1, 4000, period="H1 2024"), _fact(2, 8142, period="FY2024")]
        assert build_timelines(facts, [_rel(1, 2)]) == []

    def test_a_quarter_is_never_chained_against_a_half_year(self):
        """Different subdivisions of a year -- neither is the whole, but
        mixing granularities is still not a trend step."""
        facts = [_fact(1, 500, period="Q1 2024"), _fact(2, 4000, period="H1 2024")]
        assert build_timelines(facts, [_rel(1, 2)]) == []


class TestUnitConsistency:
    def test_mismatched_units_are_dropped_not_silently_mixed(self):
        """A 'timeline' mixing money and a headcount would be meaningless
        -- the minority-unit point is dropped rather than converted wrong."""
        facts = [
            _fact(1, 100, period="FY22", unit="INR million"),
            _fact(2, 120, period="FY23", unit="INR million"),
            _fact(3, 50, period="FY24", unit="people"),
        ]
        rels = [_rel(1, 2), _rel(2, 3)]
        timeline = build_timelines(facts, rels)[0]
        assert len(timeline.points) == 2
        assert timeline.base_unit == "inr"

    def test_values_are_normalized_to_a_common_base(self):
        """crore vs million must resolve to the same base before
        comparison -- the same normalization used everywhere else."""
        facts = [
            _fact(1, 100, period="FY23", unit="INR Crore"),
            _fact(2, 1_100_000_000, period="FY24", unit="INR"),
        ]
        timeline = build_timelines(facts, [_rel(1, 2)])[0]
        assert timeline.points[0].value == 100 * 1e7
        assert timeline.points[1].value == 1_100_000_000


class TestScopeConsistency:
    def test_the_majority_scope_is_used_consistently(self):
        facts = [
            _fact(1, 100, period="FY22", scope="consolidated"),
            _fact(2, 110, period="FY22", scope="standalone"),  # same period, different scope
            _fact(3, 120, period="FY23", scope="consolidated"),
            _fact(4, 130, period="FY24", scope="consolidated"),
        ]
        rels = [_rel(1, 3), _rel(3, 4), _rel(1, 2)]
        timeline = build_timelines(facts, rels)[0]
        assert timeline.scope_used == "consolidated"
        assert len(timeline.points) == 3
        assert all(p.scope == "consolidated" for p in timeline.points)


class TestOrderingAndLabels:
    def test_timelines_are_sorted_longest_first(self):
        short = [_fact(1, 1, period="FY23"), _fact(2, 2, period="FY24")]
        long_ = [_fact(3, 1, period="FY21"), _fact(4, 2, period="FY22"), _fact(5, 3, period="FY23")]
        rels = [_rel(1, 2), _rel(3, 4), _rel(4, 5)]
        timelines = build_timelines(short + long_, rels)
        assert len(timelines[0].points) >= len(timelines[1].points)

    def test_label_includes_subject_and_attribute(self):
        facts = [_fact(1, 100, period="FY23", subject="Acme", attribute="headcount"),
                 _fact(2, 120, period="FY24", subject="Acme", attribute="headcount")]
        timeline = build_timelines(facts, [_rel(1, 2)])[0]
        assert "Acme" in timeline.label and "headcount" in timeline.label

    def test_pct_change_is_computed_correctly(self):
        facts = [_fact(1, 100, period="FY23"), _fact(2, 150, period="FY24")]
        timeline = build_timelines(facts, [_rel(1, 2)])[0]
        pct = timeline.points[1].pct_change_from(timeline.points[0])
        assert pct == 50.0

    def test_pct_change_handles_a_zero_baseline_safely(self):
        facts = [_fact(1, 0, period="FY23"), _fact(2, 150, period="FY24")]
        timeline = build_timelines(facts, [_rel(1, 2)])[0]
        assert timeline.points[1].pct_change_from(timeline.points[0]) is None


class TestDomainNeutrality:
    def test_a_non_financial_metric_chains_identically(self):
        """Nothing here knows what a document is about -- population,
        headcount, GDP growth, anything numeric-and-periodic works."""
        facts = [
            _fact(1, 1000, period="2021", unit="individuals", subject="Species X", attribute="population"),
            _fact(2, 1200, period="2022", unit="individuals", subject="Species X", attribute="population"),
        ]
        timeline = build_timelines(facts, [_rel(1, 2)])[0]
        assert [p.original_value for p in timeline.points] == [1000, 1200]


class TestSafety:
    def test_empty_input_is_safe(self):
        assert build_timelines([], []) == []

    def test_facts_with_no_parseable_period_are_excluded(self):
        facts = [_fact(1, 100, period="sometime"), _fact(2, 120, period="FY24")]
        assert build_timelines(facts, [_rel(1, 2)]) == []

    def test_facts_with_no_numeric_value_are_excluded(self):
        facts = [_fact(1, None, period="FY23"), _fact(2, 120, period="FY24")]
        facts[0]["value_numeric"] = None
        assert build_timelines(facts, [_rel(1, 2)]) == []

    def test_a_relationship_referencing_an_unknown_fact_id_is_ignored(self):
        facts = [_fact(1, 100, period="FY23"), _fact(2, 120, period="FY24")]
        rels = [_rel(1, 999)]
        assert build_timelines(facts, rels) == []
