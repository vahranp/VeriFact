"""Builds evaluation/datasets/relationship_benchmark.json.

Two kinds of ground truth, both allowed by the eval brief and neither
obtained by asking the production LLM to grade itself:

1. "constructed" -- fact pairs built by hand with controlled fields
   (value_numeric, unit, time_period, scope). The correct category is
   true BY CONSTRUCTION (e.g. same value + same period + same scope is
   definitionally an exact corroboration), independently checkable
   against the deterministic functions (app.normalize.compare_values,
   app.context.compare_periods/compare_scopes) rather than asserted.
   Subjects use a fictional company ("Northwind Logistics") so nothing
   here is a real extracted fact or could be mistaken for one.

2. "real_mined" -- actual fact pairs already in the live database, from
   real PDF extractions this session. Ground truth for these was
   assigned by reading the fact's own quote/statement/structured fields
   directly (the same evidence a human reviewer would use), not by
   querying app.relationships' LLM for a label. Where the correct
   category is only obvious from real prose I already know well (the
   documented Case 1-4 examples, the BRSR turnover-rate mismatch), that
   prior knowledge is cited in `notes` rather than left implicit.

Run this file to regenerate the dataset:
    python evaluation/datasets/build_relationship_benchmark.py

It does not call any LLM and makes no network/Ollama calls -- it only
writes the case DEFINITIONS. evaluation/run_benchmark.py is what
actually executes them against the real pipeline.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evaluation.schema import (
    BenchmarkCase, EXACT_CORROBORATION, CONTRADICTION, CONTEXT_RECONCILIATION,
    UNRELATED, INSUFFICIENT_CONTEXT, RELATED_NOT_COMPARABLE, UNCERTAIN,
    LLM_UNCHECKED_CORROBORATES, make_fact,
)

CASES: list[BenchmarkCase] = []


def add(case: BenchmarkCase):
    CASES.append(case)


# ============================================================ CONSTRUCTED =
# Fictional subject throughout ("Northwind Logistics") -- these are not
# real extracted facts and must never be mistaken for corpus content.

# ---- EXACT_CORROBORATION: same claim, verifiable numeric equality ----

add(BenchmarkCase(
    id="ec-01", category=EXACT_CORROBORATION, provenance="constructed",
    trap="crore_vs_million",
    notes="100 crore == 1,000 million; tests the scale-word table, not a hardcoded pair.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "100 crore", 100.0, "INR crore", "FY24", None,
                      quote="Revenue from operations was Rs 100 crore in FY24"),
    fact_b=make_fact("Northwind Logistics", "revenue from services", "1,000 million", 1000.0, "INR million", "FY24", None,
                      quote="Revenue from services stood at INR 1,000 million for FY24"),
))
add(BenchmarkCase(
    id="ec-02", category=EXACT_CORROBORATION, provenance="constructed",
    trap="parenthesized_negative_vs_minus",
    notes="Accounting-style (452) must equal -452, not merely '452' with sign ignored.",
    fact_a=make_fact("Northwind Logistics", "net loss", "(452)", -452.0, "INR million", "FY24", "consolidated",
                      quote="Net loss for the year was (452)"),
    fact_b=make_fact("Northwind Logistics", "net loss", "-452", -452.0, "INR million", "FY24", "consolidated",
                      quote="Net loss of -452 was recorded for FY24"),
))
add(BenchmarkCase(
    id="ec-03", category=EXACT_CORROBORATION, provenance="constructed",
    trap="thousands_separator",
    notes="1,000 and 1000.0 are the same number; a naive string compare would call them different.",
    fact_a=make_fact("Northwind Logistics", "headcount", "1,000", 1000.0, "count", "FY24", None,
                      quote="Total headcount stood at 1,000 employees"),
    fact_b=make_fact("Northwind Logistics", "headcount", "1000", 1000.0, "count", "FY24", None,
                      quote="The company employed 1000 people as of FY24"),
))
add(BenchmarkCase(
    id="ec-04", category=LLM_UNCHECKED_CORROBORATES, provenance="constructed",
    trap="qualitative_status_match",
    notes="No number to check deterministically -- correct decision_source is llm_unchecked, "
          "not deterministic_confirmed. This is what distinguishes real corroboration from a "
          "*verified numeric equality*.",
    fact_a=make_fact("Northwind Logistics", "plant status", "operational", None, None, "FY24", None,
                      quote="The Chennai plant remained fully operational throughout FY24"),
    fact_b=make_fact("Northwind Logistics", "plant status", "running", None, None, "FY24", None,
                      quote="The Chennai facility continued running without interruption in FY24"),
))
add(BenchmarkCase(
    id="ec-05", category=EXACT_CORROBORATION, provenance="constructed",
    trap="percent_notation",
    notes="'12.5%' and '12.5 percent' are the same percentage, differently notated.",
    fact_a=make_fact("Northwind Logistics", "operating margin", "12.5%", 12.5, "%", "FY24", "consolidated",
                      quote="Operating margin was 12.5% in FY24"),
    fact_b=make_fact("Northwind Logistics", "operating margin", "12.5 percent", 12.5, "percent", "FY24", "consolidated",
                      quote="The company reported an operating margin of 12.5 percent for FY24"),
))

# ---- CONTRADICTION: same claim, same context, verified different value ----

add(BenchmarkCase(
    id="ct-01", category=CONTRADICTION, provenance="constructed",
    trap="same_context_different_value",
    notes="20% gap, same period/scope/unit -- a real, unexplained conflict, not a rounding artifact.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "100 million", 100.0, "INR million", "FY24", "consolidated",
                      quote="Revenue from operations was INR 100 million in FY24"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "120 million", 120.0, "INR million", "FY24", "consolidated",
                      quote="Revenue from operations of INR 120 million was reported for FY24"),
))
add(BenchmarkCase(
    id="ct-02", category=CONTRADICTION, provenance="constructed",
    trap="sign_flip",
    notes="A loss vs. a profit of the same magnitude is a real contradiction, not a sign typo to ignore.",
    fact_a=make_fact("Northwind Logistics", "net result", "(50)", -50.0, "INR million", "FY24", "consolidated",
                      quote="Net result for FY24 was a loss of (50)"),
    fact_b=make_fact("Northwind Logistics", "net result", "50", 50.0, "INR million", "FY24", "consolidated",
                      quote="Net result for FY24 was a profit of 50"),
))
add(BenchmarkCase(
    id="ct-03", category=CONTRADICTION, provenance="constructed",
    trap="percentage_conflict",
    notes="Same period/scope percentage, 15pp gap -- not a unit or rounding difference.",
    fact_a=make_fact("Northwind Logistics", "employee turnover rate", "30%", 30.0, "%", "FY24", "consolidated",
                      quote="Employee turnover rate was 30% in FY24"),
    fact_b=make_fact("Northwind Logistics", "employee turnover rate", "45%", 45.0, "%", "FY24", "consolidated",
                      quote="Employee turnover rate stood at 45% for FY24"),
))
add(BenchmarkCase(
    id="ct-04", category=CONTRADICTION, provenance="constructed",
    trap="digit_substring_trap_12_vs_120",
    notes="The literal failure mode this project's evidence check was rewritten to close: a naive "
          "digit-substring comparer would see '12' inside '120' and call these compatible. They "
          "are a real 10x contradiction, same period/scope.",
    fact_a=make_fact("Northwind Logistics", "customer complaints", "12", 12.0, "count", "FY24", "North region",
                      quote="12 customer complaints were recorded for the North region in FY24"),
    fact_b=make_fact("Northwind Logistics", "customer complaints", "120", 120.0, "count", "FY24", "North region",
                      quote="120 customer complaints were logged for the North region in FY24"),
))
add(BenchmarkCase(
    id="ct-05", category=CONTRADICTION, provenance="constructed",
    trap="decimal_shift_trap_12_5_vs_125",
    notes="12.5 vs 125 -- a decimal-point/digit-stripping bug would collapse these (strip '.' from "
          "'12.5' to get '125'). They are a real 10x contradiction. Scope must be stated and "
          "matching on both sides (unlike ct-01..03's None values here would have been a copy-paste "
          "bug caught on review) -- CONTRADICTION requires period=SAME *and* scope=SAME to be a "
          "conclusive deterministic verdict; leaving scope unstated would make INSUFFICIENT_CONTEXT "
          "the actually-correct answer instead, not a system error.",
    fact_a=make_fact("Northwind Logistics", "cost per unit", "12.5", 12.5, "INR", "FY24", "consolidated",
                      quote="Cost per unit was INR 12.5 in FY24"),
    fact_b=make_fact("Northwind Logistics", "cost per unit", "125", 125.0, "INR", "FY24", "consolidated",
                      quote="Cost per unit stood at INR 125 for FY24"),
))

# ---- CONTEXT_RECONCILIATION: period / scope / basis ----

add(BenchmarkCase(
    id="cr-01", category=CONTEXT_RECONCILIATION, provenance="constructed", trap="period_fy23_vs_fy24",
    notes="Different fiscal years -- a value difference is expected, not a conflict.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "100 million", 100.0, "INR million", "FY23", "consolidated",
                      quote="Revenue from operations was INR 100 million in FY23"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "140 million", 140.0, "INR million", "FY24", "consolidated",
                      quote="Revenue from operations grew to INR 140 million in FY24"),
))
add(BenchmarkCase(
    id="cr-02", category=CONTEXT_RECONCILIATION, provenance="constructed", trap="period_q4_vs_annual",
    notes="A quarter inside a year is a subset, not the same period -- period relation is OVERLAPPING.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "35 million", 35.0, "INR million", "Q4 FY24", "consolidated",
                      quote="Q4 FY24 revenue from operations was INR 35 million"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "140 million", 140.0, "INR million", "FY24", "consolidated",
                      quote="Full-year FY24 revenue from operations was INR 140 million"),
))
add(BenchmarkCase(
    id="cr-03", category=CONTEXT_RECONCILIATION, provenance="constructed", trap="scope_consolidated_vs_standalone",
    notes="Consolidated includes subsidiaries; standalone does not -- a gap is expected.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "140 million", 140.0, "INR million", "FY24", "standalone",
                      quote="Standalone revenue from operations was INR 140 million in FY24"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "165 million", 165.0, "INR million", "FY24", "consolidated",
                      quote="Consolidated revenue from operations was INR 165 million in FY24"),
))
add(BenchmarkCase(
    id="cr-04", category=CONTEXT_RECONCILIATION, provenance="constructed", trap="scope_gross_vs_net",
    notes="Gross vs. net is a scope-axis contrast in this codebase's own model (see app/context.py _AXES).",
    fact_a=make_fact("Northwind Logistics", "revenue", "150 million", 150.0, "INR million", "FY24", "gross",
                      quote="Gross revenue was INR 150 million in FY24"),
    fact_b=make_fact("Northwind Logistics", "revenue", "140 million", 140.0, "INR million", "FY24", "net",
                      quote="Net revenue of INR 140 million was reported for FY24"),
))
add(BenchmarkCase(
    id="cr-05", category=CONTEXT_RECONCILIATION, provenance="constructed", trap="basis_actual_vs_forecast",
    notes="Actual vs. forecast is the basis axis, distinct from scope -- must not be collapsed together.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "140 million", 140.0, "INR million", "FY24", "actual",
                      quote="Actual revenue from operations for FY24 was INR 140 million"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "155 million", 155.0, "INR million", "FY24", "forecast",
                      quote="FY24 revenue from operations had been forecast at INR 155 million"),
))
add(BenchmarkCase(
    id="cr-06", category=CONTEXT_RECONCILIATION, provenance="constructed", trap="basis_restated_vs_current",
    notes="A restated prior figure differing from the originally reported one is expected, not a conflict.",
    fact_a=make_fact("Northwind Logistics", "net profit", "80 million", 80.0, "INR million", "FY23", "restated",
                      quote="Restated FY23 net profit was INR 80 million"),
    fact_b=make_fact("Northwind Logistics", "net profit", "92 million", 92.0, "INR million", "FY23", "current",
                      quote="FY23 net profit as originally reported was INR 92 million"),
))

# ---- RELATED_NOT_COMPARABLE: same-ish metric, structurally incompatible units ----

add(BenchmarkCase(
    id="rn-01", category=RELATED_NOT_COMPARABLE, provenance="constructed", trap="rate_vs_count",
    notes="A rate and an absolute count of the 'same' underlying thing don't reduce to one base unit.",
    fact_a=make_fact("Northwind Logistics", "employee turnover rate", "12%", 12.0, "%", "FY24", "consolidated",
                      quote="Employee turnover rate was 12% in FY24"),
    fact_b=make_fact("Northwind Logistics", "employee turnover count", "45", 45.0, "count", "FY24", "consolidated",
                      quote="45 employees left the company in FY24"),
))
add(BenchmarkCase(
    id="rn-02", category=RELATED_NOT_COMPARABLE, provenance="constructed", trap="currency_vs_count",
    notes="A monetary amount and a headcount are structurally incompatible units.",
    fact_a=make_fact("Northwind Logistics", "employee cost", "50 million", 50.0, "INR million", "FY24", None,
                      quote="Total employee cost was INR 50 million in FY24"),
    fact_b=make_fact("Northwind Logistics", "employee count", "500", 500.0, "count", "FY24", None,
                      quote="The company had 500 employees in FY24"),
))
add(BenchmarkCase(
    id="rn-03", category=RELATED_NOT_COMPARABLE, provenance="constructed", trap="unparseable_unit",
    notes="A unit compare_values can't parse at all must refuse to compare, not guess.",
    fact_a=make_fact("Northwind Logistics", "customer satisfaction index", "78", 78.0, "index points", "FY24", None,
                      quote="Customer satisfaction index stood at 78 in FY24"),
    fact_b=make_fact("Northwind Logistics", "customer satisfaction index", "82", 82.0, "index points", "FY23", None,
                      quote="Customer satisfaction index stood at 82 in FY23"),
))

# ---- INSUFFICIENT_CONTEXT: values differ, context genuinely unknown ----

add(BenchmarkCase(
    id="ic-01", category=INSUFFICIENT_CONTEXT, provenance="constructed", trap="unresolvable_relative_period",
    notes="'the previous year' can't be resolved to a calendar/fiscal year without the document's own "
          "reporting date -- period must come back UNKNOWN, not guessed.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "100 million", 100.0, "INR million", "the previous year", "consolidated",
                      quote="Revenue from operations in the previous year was INR 100 million"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "140 million", 140.0, "INR million", "FY24", "consolidated",
                      quote="Revenue from operations was INR 140 million in FY24"),
))
add(BenchmarkCase(
    id="ic-02", category=INSUFFICIENT_CONTEXT, provenance="constructed", trap="scope_unstated_both_sides",
    notes="Neither fact states a scope, values differ -- can't tell contradiction from reconciliation.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "100 million", 100.0, "INR million", "FY24", None,
                      quote="Revenue from operations was INR 100 million in FY24"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "140 million", 140.0, "INR million", "FY24", None,
                      quote="Revenue from operations of INR 140 million was recorded for FY24"),
))
add(BenchmarkCase(
    id="ic-03", category=INSUFFICIENT_CONTEXT, provenance="constructed", trap="period_entirely_absent",
    notes="One side states no period at all -- not resolvable to same/different/overlapping.",
    fact_a=make_fact("Northwind Logistics", "warehouse capacity", "50,000 sq ft", 50000.0, "sq ft", None, None,
                      quote="Warehouse capacity is 50,000 sq ft"),
    fact_b=make_fact("Northwind Logistics", "warehouse capacity", "75,000 sq ft", 75000.0, "sq ft", "FY24", None,
                      quote="Warehouse capacity reached 75,000 sq ft in FY24"),
))

# ---- UNRELATED: different real-world quantities, deceptively similar surface ----

add(BenchmarkCase(
    id="un-01", category=UNRELATED, provenance="constructed", trap="same_value_different_metric",
    notes="Named explicitly in the brief: identical numeric value, completely different metrics. A "
          "naive value-matching approach would flag this; nothing about the two facts is actually "
          "in tension.",
    fact_a=make_fact("Northwind Logistics", "engineering headcount", "100", 100.0, "count", "FY24", None,
                      quote="Engineering headcount was 100 in FY24"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "100 million", 100.0, "INR million", "FY24", None,
                      quote="Revenue from operations was INR 100 million in FY24"),
))
add(BenchmarkCase(
    id="un-02", category=UNRELATED, provenance="constructed", trap="different_entity",
    notes="Same metric name, different real-world subject -- not comparable regardless of context.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "140 million", 140.0, "INR million", "FY24", None,
                      quote="Northwind Logistics revenue from operations was INR 140 million in FY24"),
    fact_b=make_fact("Southgate Freight", "revenue from operations", "140 million", 140.0, "INR million", "FY24", None,
                      quote="Southgate Freight revenue from operations was INR 140 million in FY24"),
))
add(BenchmarkCase(
    id="un-03", category=UNRELATED, provenance="constructed", trap="different_attribute_same_subject",
    notes="Same subject, unrelated attributes -- sharing a subject doesn't make two facts comparable.",
    fact_a=make_fact("Northwind Logistics", "CEO tenure", "5 years", 5.0, "years", "FY24", None,
                      quote="The CEO has served for 5 years as of FY24"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "140 million", 140.0, "INR million", "FY24", None,
                      quote="Revenue from operations was INR 140 million in FY24"),
))

# ---- Evidence-adjacent: unverified evidence must cap confidence, not vanish ----

add(BenchmarkCase(
    id="ev-01", category=UNCERTAIN, provenance="constructed", trap="unverified_evidence_caps_confidence",
    notes="Fact B's evidence_status is only quote_grounded (not fact_validated) -- adjudication rule "
          "3 must cap a confident corroborates/contradicts at uncertain rather than trust an "
          "unverified value.",
    fact_a=make_fact("Northwind Logistics", "revenue from operations", "100 million", 100.0, "INR million", "FY24", "consolidated",
                      quote="Revenue from operations was INR 100 million in FY24"),
    fact_b=make_fact("Northwind Logistics", "revenue from operations", "130 million", 130.0, "INR million", "FY24", "consolidated",
                      quote="130", evidence_status="quote_grounded"),
))

# ============================================================= REAL_MINED =
# Actual fact pairs already in storage/facts.db, extracted from the real
# Delhivery/IMF/RBI PDFs this session. Every field below (subject, value,
# quote, evidence_status) is copied verbatim from the live database, not
# invented. run_live=True: prompts changed since these were first computed
# (see the prompt-injection-defense addition), so a fresh call is made
# rather than trusting a result cached under an older prompt.

add(BenchmarkCase(
    id="rm-01", category=EXACT_CORROBORATION, provenance="real_mined",
    source_relationship_id=None,  # the historical row predates decision_source; re-run fresh
    notes="README's documented Case 1: FY24 revenue from operations, Annual Report (facts.id=94) vs "
          "the Q4 earnings deck's revenue from services (facts.id=241) -- 8.14154e10 vs 8.142e10 INR, "
          "agree within 0.006%. Both facts read directly from the live DB.",
    fact_a=make_fact("Delhivery Limited", "revenue from contracts with customers", "81,415.38", 81415.38,
                      "INR million", "FY24", None,
                      statement="The revenue from contracts with customers of Delhivery Limited for the year ended March 31, 2024 is INR 81,415.38 million.",
                      quote="Revenue from contracts with customers 21 81,415.38"),
    fact_b=make_fact("Delhivery", "revenue from services FY24", "₹8,142 Cr", 8142.0, "INR Crore", "FY24", None,
                      statement="Delhivery's revenue from services in FY24 was ₹8,142 Cr.",
                      quote="₹8,142 Cr FY24 revenue from services"),
))
add(BenchmarkCase(
    id="rm-02", category=CONTEXT_RECONCILIATION, provenance="real_mined",
    notes="README's documented Case 3: standalone (facts.id=215) vs consolidated (facts.id=216) "
          "revenue from operations, both FY ended March 2023 -- 7.8% gap explained by scope.",
    fact_a=make_fact("Company", "revenue from operations (standalone)", "₹ 66,586.61 million", 66586.61,
                      "INR million", "FY ended March 31, 2023", "Standalone",
                      statement="The company's standalone revenue from operations for the FY ended March 31, 2023 was ₹ 66,586.61 million.",
                      quote="66,586.61", evidence_status="quote_grounded"),
    fact_b=make_fact("Company", "revenue from operations (consolidated)", "₹ 72,253.01 million", 72253.01,
                      "INR million", "FY ended March 31, 2023", "Consolidated",
                      statement="The company's consolidated revenue from operations for the FY ended March 31, 2023 was ₹ 72,253.01 million.",
                      quote="72,253.01", evidence_status="quote_grounded"),
))
add(BenchmarkCase(
    id="rm-03", category=CONTEXT_RECONCILIATION, provenance="real_mined", trap="period_reconciliation_real_macro_data",
    source_relationship_id=882,
    notes="RBI Annual Report facts, real generalization-run data (see README Generalization section): "
          "Foodgrains Production 213.6 (2003-04) vs 329.7 million tonnes (2022-23) -- a 19-year gap, "
          "growth is expected. Already computed live once (relationship id 882, "
          "decision_source=deterministic_confirmed); re-run here to reflect the current prompt.",
    fact_a=make_fact("Foodgrains Production", "Million Tonnes", "213.6", 213.6, "Million Tonnes", "2003-04", None,
                      quote="Foodgrains Production (Million Tonnes)** | 213.6 |"),
    fact_b=make_fact("Foodgrains Production", "Million Tonnes", "329.7", 329.7, "Million Tonnes", "2022-23", None,
                      quote="I.3 | Foodgrains Production (Million Tonnes)** | 213.6 | 248.8 | 269.8 | 329.7 | 332.3 | 3"),
))
add(BenchmarkCase(
    id="rm-04", category=UNRELATED, provenance="real_mined", trap="metric_match_false_positive_stock_vs_flow",
    source_relationship_id=883,
    notes="Real RBI data, same table/period/unit: Food Stocks (an inventory level) vs Foodgrains "
          "Production (an annual output flow) are different economic concepts despite sharing a unit "
          "and period. Ground truth assigned by reading the two labels directly -- a stock and a flow "
          "are not the same measure. Step 1 (metric-matching) evidently disagrees, judging them "
          "comparable enough to reach step 2 -- a real, honest step-1/metric-matching gap, not an "
          "adjudication gap: step 2 then proposed 'contradicts', and the deterministic layer, "
          "correctly noting neither fact states a scope, capped that at insufficient_context rather "
          "than compounding the upstream error into a false contradiction. That's the deterministic "
          "layer doing its job on a bad premise, not the deterministic layer introducing a new one -- "
          "see the evaluation report's failure analysis for the precise framing.",
    fact_a=make_fact("Food Stocks", "Million Tonnes", "18.6", 18.6, "Million Tonnes", "2003-04", None,
                      quote="a) Food Stocks (Million Tonnes)*** | 18.6 |"),
    fact_b=make_fact("Foodgrains Production", "Million Tonnes", "213.6", 213.6, "Million Tonnes", "2003-04", None,
                      quote="Foodgrains Production (Million Tonnes)** | 213.6 |"),
))
add(BenchmarkCase(
    id="rm-05", category=RELATED_NOT_COMPARABLE, provenance="real_mined",
    source_relationship_id=870,
    notes="Real IMF facts: a numeric debt ratio (48.9% of GDP) vs. a purely qualitative footnote about "
          "what a debt figure includes -- one side has no parseable numeric unit at all.",
    fact_a=make_fact("India", "Debt, Reform Scenario (percent of GDP)", "48.9", 48.9, "%", None, "Centre",
                      quote="48.9", evidence_status="quote_grounded"),
    fact_b=make_fact("India", "central government debt inclusion",
                      "includes SDR, and for FY2021/22 reflects the additional SDR allocation of about 0.6 percent of GDP",
                      None, None, "FY2021/22 onwards", None,
                      quote="11/ Central government debt includes SDR, and for FY2021/22 reflects the additional SDR allocation"),
))
add(BenchmarkCase(
    id="rm-06", category=UNRELATED, provenance="real_mined", trap="unrelated_footnotes_same_page",
    source_relationship_id=863,
    notes="Real IMF footnotes, both fact_validated: one is about asset-sales accounting treatment, the "
          "other about wireless-spectrum-auction revenue classification -- two distinct footnotes with "
          "no real relationship beyond appearing in the same table. The live system's recorded answer "
          "was 'uncertain' (llm_unchecked) -- safe, but 'unrelated' would be the more precise label; "
          "see the evaluation report.",
    fact_a=make_fact("India", "asset sales inclusion", "in receipts, and excludes certain non-tax revenue items",
                      None, None, None, None,
                      quote="10/ Includes asset sales in receipts, and excludes certain non-tax revenue items. Includes"),
    fact_b=make_fact("India", "non-tax revenues classification", "auctions for wireless spectrum",
                      None, None, None, None,
                      quote="3/ Auctions for wireless spectrum are classified as non-tax revenues."),
))

# ================================================ DETERMINISTIC OVERRIDE ==
# No LLM call: app.adjudication.adjudicate() is exercised directly with a
# deliberately WRONG llm_proposal, the same way tests/test_adjudication.py
# does. These answer Phase 8's question ("does the deterministic layer
# actually correct real LLM mistakes") for the specific, reproducible shape
# of mistake rather than hoping a live call happens to reproduce it -- and
# they cost nothing to re-run, so they're re-verified every time this
# dataset is built rather than just once.
DETERMINISTIC_OVERRIDE_CASES = [
    {
        "id": "do-01", "category": CONTRADICTION,
        "notes": "LLM wrongly proposes 'reconciled' with an invented reason on a same-period, "
                 "same-scope, verified 20-unit gap -- adjudication rule 5 must override to contradicts.",
        "fact_a": make_fact("Northwind Logistics", "revenue", "100", 100.0, "INR million", "FY24", "consolidated"),
        "fact_b": make_fact("Northwind Logistics", "revenue", "120", 120.0, "INR million", "FY24", "consolidated"),
        "llm_proposal": {"relation_type": "reconciled", "explanation": "Fabricated: figures reflect a restatement.",
                          "reconciliation_context": "restatement", "confidence": 0.8},
    },
    {
        "id": "do-02", "category": EXACT_CORROBORATION,
        "notes": "LLM wrongly proposes 'uncertain' on values that verify as equal, same period/scope -- "
                 "rule 4 must override to corroborates regardless.",
        "fact_a": make_fact("Northwind Logistics", "revenue", "100", 100.0, "INR million", "FY24", "consolidated"),
        "fact_b": make_fact("Northwind Logistics", "revenue", "100", 100.0, "INR million", "FY24", "consolidated"),
        "llm_proposal": {"relation_type": "uncertain", "explanation": "Fabricated: model expressed unwarranted doubt.",
                          "reconciliation_context": None, "confidence": 0.4},
    },
    {
        "id": "do-03", "category": RELATED_NOT_COMPARABLE,
        "notes": "LLM wrongly proposes 'contradicts' between a percentage and an absolute count -- "
                 "rule 1 (incompatible units) must override to related_but_not_comparable.",
        "fact_a": make_fact("Northwind Logistics", "turnover rate", "12%", 12.0, "%", "FY24", "consolidated"),
        "fact_b": make_fact("Northwind Logistics", "turnover count", "45", 45.0, "count", "FY24", "consolidated"),
        "llm_proposal": {"relation_type": "contradicts", "explanation": "Fabricated: model conflated a rate with a count.",
                          "reconciliation_context": None, "confidence": 0.7},
    },
    {
        "id": "do-04", "category": UNCERTAIN,
        "notes": "LLM wrongly proposes a confident 'contradicts' on a magnitude-suspect (~1,000,000x) "
                 "gap that is almost certainly a unit-metadata error -- rule 2 must cap at uncertain.",
        "fact_a": make_fact("Northwind Logistics", "total equity", "91,446.46", 91446.46, "INR million", "FY24", None),
        "fact_b": make_fact("Northwind Logistics", "total equity", "85,466.74", 85466.74, "INR", "FY24", None),
        "llm_proposal": {"relation_type": "contradicts", "explanation": "Fabricated: model did not notice the ~1,000,000x gap.",
                          "reconciliation_context": None, "confidence": 0.9},
    },
    {
        "id": "do-05", "category": CONTEXT_RECONCILIATION,
        "notes": "LLM wrongly proposes 'contradicts' on facts whose period is confirmed DIFFERENT -- "
                 "rule 6 must override to reconciled.",
        "fact_a": make_fact("Northwind Logistics", "revenue", "100", 100.0, "INR million", "FY23", "consolidated"),
        "fact_b": make_fact("Northwind Logistics", "revenue", "140", 140.0, "INR million", "FY24", "consolidated"),
        "llm_proposal": {"relation_type": "contradicts", "explanation": "Fabricated: model ignored the differing fiscal years.",
                          "reconciliation_context": None, "confidence": 0.85},
    },
    {
        "id": "do-06", "category": UNCERTAIN,
        "notes": "LLM wrongly proposes a confident 'corroborates' when fact B's evidence is unverified -- "
                 "rule 3 must cap at uncertain rather than trust an unverified value.",
        "fact_a": make_fact("Northwind Logistics", "revenue", "100", 100.0, "INR million", "FY24", "consolidated"),
        "fact_b": make_fact("Northwind Logistics", "revenue", "100", 100.0, "INR million", "FY24", "consolidated",
                             evidence_status="quote_grounded"),
        "llm_proposal": {"relation_type": "corroborates", "explanation": "Fabricated: model trusted an unverified value.",
                          "reconciliation_context": None, "confidence": 0.9},
    },
]

# Write both JSON files.
if __name__ == "__main__":
    out_dir = Path(__file__).parent

    rel_path = out_dir / "relationship_benchmark.json"
    rel_path.write_text(
        json.dumps([c.to_json() for c in CASES], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    by_provenance: dict[str, int] = {}
    for c in CASES:
        by_provenance[c.provenance] = by_provenance.get(c.provenance, 0) + 1
    print(f"Wrote {len(CASES)} classify_pair cases to {rel_path}: {by_provenance}")

    override_path = out_dir / "deterministic_override_cases.json"
    override_path.write_text(
        json.dumps(DETERMINISTIC_OVERRIDE_CASES, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(DETERMINISTIC_OVERRIDE_CASES)} deterministic-override cases to {override_path}")
