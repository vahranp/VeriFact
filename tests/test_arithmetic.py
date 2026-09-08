"""Tests for arithmetic self-validation.

The feature's value rests on it being *discovery-based*: there is no table
of accounting equations in the module, so these tests use both a financial
example and a deliberately non-financial one to confirm it isn't secretly
domain-specific.
"""
import pytest

from app.arithmetic import check_arithmetic_consistency


def _f(statement, value, unit="INR million", period="FY24", scope="consolidated", evidence_status=None):
    return {
        "statement": statement, "value_numeric": value, "unit": unit,
        "time_period": period, "scope": scope, "evidence_status": evidence_status,
    }


BALANCE_SHEET = [
    _f("Total assets", 114530.20),
    _f("Total liabilities", 23083.74),
    _f("Total equity", 91446.46),
    _f("Equity share capital", 736.79),
    _f("Other equity", 90709.67),
]


class TestIdentityDiscovery:
    def test_finds_real_accounting_identities_without_being_told_them(self):
        report = check_arithmetic_consistency(BALANCE_SHEET)
        found = {round(i.error_pct, 2) for i in report.identities}
        assert report.identities, "expected to discover at least one identity"
        assert all(e <= 0.5 for e in found)

        described = " ".join(i.describe() for i in report.identities)
        # liabilities + equity = assets, and share capital + other equity = total equity
        assert "1.1453e+11" in described or "114530" in described

    def test_works_on_a_non_financial_domain(self):
        """Same code, no accounting vocabulary: headcount splits that sum
        to a total must be discovered identically."""
        facts = [
            _f("Engineering headcount", 120, unit="people"),
            _f("Sales headcount", 80, unit="people"),
            _f("Total headcount", 200, unit="people"),
        ]
        report = check_arithmetic_consistency(facts)
        assert len(report.identities) == 1
        assert report.identities[0].c_value == 200

    def test_coincidental_near_matches_are_excluded_at_default_tolerance(self):
        report = check_arithmetic_consistency(BALANCE_SHEET)
        assert all(i.error_pct <= 0.5 for i in report.identities)

    def test_no_identities_when_numbers_are_unrelated(self):
        facts = [_f("A", 13), _f("B", 71), _f("C", 907)]
        assert check_arithmetic_consistency(facts).identities == []


class TestFalsePositiveGuards:
    """These cases come from running the module over 41 real extracted
    facts, which surfaced failures the synthetic set above could not: a
    5-number balance sheet is too small and too clean to expose them."""

    def test_a_dominant_addend_cannot_absorb_an_arbitrary_second_value(self):
        """Real failure: 'other equity' (90,709.67) sits 0.81% from 'total
        equity' (91,446.46), so at 0.5% tolerance on the total, any second
        addend in a wide band completed a passing identity. Borrowings,
        provisions and other financial liabilities each produced a
        confident-looking identity that was pure coincidence."""
        facts = BALANCE_SHEET + [
            _f("Borrowings", 401.84),
            _f("Provisions", 646.61),
            _f("Other financial liabilities", 1091.14),
        ]
        report = check_arithmetic_consistency(facts)
        totals = {(round(i.a_value, 2), round(i.b_value, 2)) for i in report.identities}
        for junk in (401.84, 646.61, 1091.14):
            assert (90709.67, junk) not in totals and (junk, 90709.67) not in totals

        # the two genuine identities must survive the tightening
        assert len(report.identities) == 2
        assert all(i.error_pct == 0.0 for i in report.identities)

    def test_many_real_figures_do_not_manufacture_scale_anomalies(self):
        """Real failure: 13 figures spanning three orders of magnitude
        produced nine scale 'anomalies', all false -- with enough numbers,
        some pair always sums to within a couple of percent of 10x
        another. Consistent units mean there is no anomaly to find."""
        facts = BALANCE_SHEET + [
            _f("Borrowings", 401.84), _f("Provisions", 646.61),
            _f("Other financial liabilities", 1091.14),
            _f("Lease liabilities", 8436.40), _f("Deferred tax liabilities", 133.66),
            _f("Trade payables", 5423.19),
        ]
        assert check_arithmetic_consistency(facts).scale_anomalies == []

    def test_uniform_misdenomination_is_undetectable_by_design(self):
        """If every figure on a page is mis-scaled by the same factor the
        identities still close perfectly, so internal consistency cannot
        see it. Documented rather than silently assumed."""
        uniform = [dict(f, unit="INR") for f in BALANCE_SHEET]  # all missing "million"
        report = check_arithmetic_consistency(uniform)
        assert report.scale_anomalies == []
        assert len(report.identities) == 2  # still internally consistent


class TestScaleAnomalyDetection:
    def test_detects_the_real_unit_denomination_bug(self):
        """The actual failure this project hit: a balance sheet's
        'all amounts in millions' header was chunked away from its rows,
        so one figure came back denominated in bare rupees. The identity
        then only closes if that value is rescaled by 1e6."""
        broken = [dict(f) for f in BALANCE_SHEET]
        for f in broken:
            if f["statement"] == "Total equity":
                f["unit"] = "INR"          # the literal extracted value
        report = check_arithmetic_consistency(broken)

        assert report.scale_anomalies, "a 1e6 scale error must be flagged"
        anomaly = report.scale_anomalies[0]
        assert anomaly.factor == pytest.approx(1_000_000)
        assert anomaly.suspect_fact["statement"] == "Total equity"

    def test_clean_data_produces_no_anomalies(self):
        assert check_arithmetic_consistency(BALANCE_SHEET).scale_anomalies == []


class TestGrouping:
    def test_different_periods_are_never_summed_together(self):
        """Adding an FY23 figure to an FY24 one would manufacture
        nonsense identities."""
        facts = [
            _f("Revenue", 100, period="FY24"),
            _f("Other income", 50, period="FY23"),
            _f("Total income", 150, period="FY24"),
        ]
        assert check_arithmetic_consistency(facts).identities == []

    def test_different_scopes_are_never_summed_together(self):
        facts = [
            _f("Revenue", 100, scope="standalone"),
            _f("Other income", 50, scope="consolidated"),
            _f("Total income", 150, scope="standalone"),
        ]
        assert check_arithmetic_consistency(facts).identities == []

    def test_incomparable_units_are_never_summed_together(self):
        facts = [
            _f("Area", 100, unit="sq ft"),
            _f("Weight", 50, unit="tons"),
            _f("Total", 150, unit="sq ft"),
        ]
        assert check_arithmetic_consistency(facts).identities == []

    def test_percentages_are_excluded(self):
        """Percentages don't sum meaningfully -- 40% + 60% = 100% is not
        evidence of anything."""
        facts = [_f("Male share", 40, unit="%"), _f("Female share", 60, unit="%"), _f("Total", 100, unit="%")]
        report = check_arithmetic_consistency(facts)
        assert report.identities == []
        assert report.facts_considered == 0

    def test_facts_without_numbers_are_ignored(self):
        facts = BALANCE_SHEET + [_f("Director status", None, unit=None)]
        report = check_arithmetic_consistency(facts)
        assert report.facts_considered == len(BALANCE_SHEET)

    def test_the_same_period_written_two_ways_is_one_group(self):
        """Regression test: grouping used to key on the RAW period string,
        so "FY24" and "FY2023-24" -- one fiscal year written two ways, per
        app/context.py -- landed in separate groups and could never be
        checked against each other, even though the two modules exist
        specifically to agree on what "the same period" means."""
        facts = [
            _f("Liabilities", 23083.74, period="FY24"),
            _f("Equity", 91446.46, period="FY2023-24"),
            _f("Assets", 114530.20, period="FY 2024"),
        ]
        report = check_arithmetic_consistency(facts)
        assert len(report.identities) == 1

    def test_a_fiscal_year_and_a_calendar_year_of_one_number_stay_separate(self):
        """The canonicalization must not over-merge: a fiscal year and a
        bare calendar year of the same number are related but not
        confirmed identical (see app/context.py OVERLAPPING) and must not
        be summed as if they were the same window."""
        facts = [
            _f("Revenue", 100, period="FY24"),
            _f("Other income", 50, period="2024"),
            _f("Total income", 150, period="FY24"),
        ]
        assert check_arithmetic_consistency(facts).identities == []

    def test_an_unparseable_period_still_groups_by_its_own_raw_text(self):
        facts = [
            _f("Revenue", 100, period="the reporting period"),
            _f("Other income", 50, period="the reporting period"),
            _f("Total income", 150, period="the reporting period"),
        ]
        report = check_arithmetic_consistency(facts)
        assert len(report.identities) == 1


class TestGroupTruncationIsSurfaced:
    def test_a_group_within_the_cap_is_not_reported_as_truncated(self):
        report = check_arithmetic_consistency(BALANCE_SHEET)
        assert report.groups_truncated == 0

    def test_a_group_over_the_cap_is_counted_and_named_in_the_summary(self):
        from app.arithmetic import MAX_GROUP_SIZE
        facts = [_f(f"Line {i}", float(i + 1)) for i in range(MAX_GROUP_SIZE + 5)]
        report = check_arithmetic_consistency(facts)
        assert report.groups_truncated == 1
        assert "capped" in report.summary()


class TestReportShape:
    def test_summary_is_human_readable(self):
        summary = check_arithmetic_consistency(BALANCE_SHEET).summary()
        assert "identities confirmed" in summary

    def test_empty_input_is_safe(self):
        report = check_arithmetic_consistency([])
        assert report.identities == [] and report.facts_considered == 0


class TestOnlyEvidencedFactsParticipate:
    """Audited in after the evidence layer (app/evidence.py) was added:
    this module was built before evidence_status existed and had no
    awareness of it, so an ungrounded or circular-quote fact's value could
    pose as ground truth here. That is exactly backwards from what the
    module exists to establish -- "these numbers satisfy an identity they
    didn't have to, which is independent evidence they were read
    correctly" only holds if the inputs were verified to begin with.
    """

    def test_an_ungrounded_fact_is_excluded(self):
        """"Other equity" is one addend of "share capital + other equity =
        total equity"; losing it as ground truth must lose that identity.
        The OTHER identity ("liabilities + equity = assets") doesn't
        involve it and must still be found."""
        facts = [dict(f) for f in BALANCE_SHEET]
        for f in facts:
            if f["statement"] == "Other equity":
                f["evidence_status"] = "ungrounded"
        report = check_arithmetic_consistency(facts)
        described = " ".join(i.describe() for i in report.identities)
        assert "90709.7" not in described
        assert len(report.identities) == 1

    def test_a_circular_quote_fact_is_excluded(self):
        """quote_grounded (without validation) covers the specific real
        failure this was measured against: a quote that is only the value
        itself ("90,709.67"), which restates the number rather than
        evidencing it."""
        facts = [dict(f) for f in BALANCE_SHEET]
        for f in facts:
            if f["statement"] == "Other equity":
                f["evidence_status"] = "quote_grounded"
        report = check_arithmetic_consistency(facts)
        described = " ".join(i.describe() for i in report.identities)
        assert "90709.7" not in described
        assert len(report.identities) == 1

    def test_a_fact_validated_fact_still_participates(self):
        facts = [dict(f, evidence_status="fact_validated") for f in BALANCE_SHEET]
        report = check_arithmetic_consistency(facts)
        assert len(report.identities) == 2

    def test_missing_evidence_status_is_treated_as_not_yet_assessed_not_bad(self):
        """A fact dict built directly (every existing test in this file,
        and any future caller not routed through the evidence check) has
        no evidence_status at all -- that must not be silently treated as
        disqualifying, or every prior test in this module would have
        started failing."""
        facts = [dict(f) for f in BALANCE_SHEET]
        assert all(f.get("evidence_status") is None for f in facts)
        report = check_arithmetic_consistency(facts)
        assert len(report.identities) == 2

    def test_excluded_facts_do_not_count_toward_facts_considered(self):
        facts = [dict(f) for f in BALANCE_SHEET]
        facts[0]["evidence_status"] = "ungrounded"
        report = check_arithmetic_consistency(facts)
        assert report.facts_considered == len(BALANCE_SHEET) - 1
