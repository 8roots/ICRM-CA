"""Pure redline engine: Decimal math, rule/LPR selection, and evaluators.

The engine is deliberately free of database and HTTP concerns. These tests
drive the evaluators and selectors table-driven and assert the documented
states: triggered / not triggered / risk warning / insufficient data /
indeterminate. Money and rate math must never use float.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from app.redline import (
    CALC_TYPES,
    DEMO_PRODUCT,
    DEMO_RULE_CONTEXT,
    DEMO_RULES,
    RedlineState,
    applicable_references,
    build_schedule,
    critical_fields_for,
    d,
    demo_lpr_entries,
    equal_installment_payment,
    equal_principal_schedule,
    evaluate_annual_rate_limit,
    evaluate_effective_cost,
    evaluate_lpr_multiple_limit,
    evaluate_redline,
    evaluate_rule,
    fmt_money,
    fmt_rate_pct,
    irr_monthly,
    parse_optional_decimal,
    rule_in_effect,
    select_lpr,
    select_primary_rule,
    validate_calc_params,
)

TODAY = date(2026, 8, 7)


@dataclass
class Entry:
    effective_date: date
    tenor: str
    value: str
    id: str = ""


@dataclass
class Package:
    code: str
    name: str
    kind: str
    status: str
    lender_qualification: str
    rule_context: str
    product: str
    effective_from: date
    effective_until: date | None
    calc_type: str
    params: dict
    legal_basis: str = ""
    reviewer: str = ""
    reviewed_at: date = date(2026, 1, 1)
    version: int = 1
    demo_only: bool = False
    content_hash: str = ""
    id: str = ""


def approved_hard(
    code: str,
    *,
    context: str = DEMO_RULE_CONTEXT,
    product: str = DEMO_PRODUCT,
    calc_type: str = "annual_rate_limit",
    params: dict | None = None,
    effective_from: date = date(2026, 1, 1),
    effective_until: date | None = None,
) -> Package:
    return Package(
        code=code,
        name=code,
        kind="hard",
        status="approved",
        lender_qualification="small_loan_company",
        rule_context=context,
        product=product,
        effective_from=effective_from,
        effective_until=effective_until,
        calc_type=calc_type,
        params=params or {"threshold_pct": "24"},
    )


def approved_reference(
    code: str,
    *,
    context: str = DEMO_RULE_CONTEXT,
    product: str = DEMO_PRODUCT,
    calc_type: str = "annual_rate_limit",
    params: dict | None = None,
) -> Package:
    return Package(
        code=code,
        name=code,
        kind="reference",
        status="approved",
        lender_qualification="*",
        rule_context=context,
        product=product,
        effective_from=date(2026, 1, 1),
        effective_until=None,
        calc_type=calc_type,
        params=params or {"threshold_pct": "24"},
    )


def confirmed(**kwargs) -> dict[str, dict]:
    return {key: {"value": value} for key, value in kwargs.items()}


# ---------------------------------------------------------------------------
# Demo seeds are well-formed
# ---------------------------------------------------------------------------


def test_demo_rule_specs_are_well_formed() -> None:
    codes = {spec["code"] for spec in DEMO_RULES}
    assert codes == {"DEMO-EFFECTIVE-COST-36", "DEMO-LPR-4X", "DEMO-RATE-24"}
    kinds = {spec["kind"] for spec in DEMO_RULES}
    assert kinds == {"hard", "reference"}
    for spec in DEMO_RULES:
        assert spec["product"] == DEMO_PRODUCT
        assert spec["rule_context"] == DEMO_RULE_CONTEXT
        validate_calc_params(spec["calc_type"], spec["params"])


def test_demo_lpr_entries_cover_recent_dates() -> None:
    entries = demo_lpr_entries()
    assert entries
    assert all(entry["tenor"] == "1Y" for entry in entries)
    assert entries[0]["effective_date"] == date(2024, 1, 20)
    assert entries[-1]["effective_date"] >= date(2026, 12, 20)
    assert all(Decimal(entry["value"]) > 0 for entry in entries)


# ---------------------------------------------------------------------------
# Decimal helpers
# ---------------------------------------------------------------------------


def test_decimal_helpers_never_use_float() -> None:
    assert d("3.14") == Decimal("3.14")
    assert parse_optional_decimal("1.5") == Decimal("1.5")
    assert parse_optional_decimal("") is None
    assert parse_optional_decimal("abc") is None
    assert fmt_money(Decimal("1234.567")) == "1234.57"  # ROUND_HALF_UP
    assert fmt_money(Decimal("1234.564")) == "1234.56"
    assert fmt_rate_pct(Decimal("12.345")) == "12.35"
    assert isinstance(d("1.1"), Decimal)


def test_irr_without_fees_equals_nominal_monthly_rate() -> None:
    principal = Decimal("100000")
    monthly_rate = Decimal("0.01")
    payment = equal_installment_payment(principal, monthly_rate, 12)
    flows = [-principal] + [payment] * 12
    irr = irr_monthly(flows)
    assert irr is not None
    assert abs(irr - monthly_rate) < Decimal("1e-9")


def test_irr_with_fees_exceeds_nominal_rate() -> None:
    principal = Decimal("100000")
    fees = Decimal("5000")
    monthly_rate = Decimal("0.01")
    payment = equal_installment_payment(principal, monthly_rate, 12)
    flows = [-(principal - fees)] + [payment] * 12
    irr = irr_monthly(flows)
    assert irr is not None
    assert irr > monthly_rate


def test_equal_principal_schedule_sum() -> None:
    principal = Decimal("120000")
    monthly_rate = Decimal("0.01")
    payments = equal_principal_schedule(principal, monthly_rate, 12)
    assert len(payments) == 12
    assert sum(payments, Decimal(0)) == principal + Decimal("7800")  # 120000*0.01*(12+...+1)/12


def test_build_schedule_methods() -> None:
    schedule = build_schedule(Decimal("120000"), Decimal("0.01"), 12, "等额本息")
    assert schedule is not None and schedule[0] == "等额本息"
    schedule = build_schedule(Decimal("120000"), Decimal("0.01"), 12, "按月付息，到期还本")
    assert schedule is None


# ---------------------------------------------------------------------------
# LPR selection
# ---------------------------------------------------------------------------


def test_select_lpr_uses_latest_effective_date_on_or_before_as_of() -> None:
    entries = [
        Entry(date(2025, 1, 20), "1Y", "3.45"),
        Entry(date(2026, 1, 20), "1Y", "3.10"),
        Entry(date(2026, 1, 20), "5Y", "3.60"),
    ]
    assert select_lpr(entries, date(2025, 6, 1)).value == "3.45"
    assert select_lpr(entries, date(2026, 2, 1)).value == "3.10"
    assert select_lpr(entries, date(2026, 2, 1), tenor="5Y").value == "3.60"
    assert select_lpr(entries, date(2024, 12, 31)) is None
    assert select_lpr(entries, date(2026, 2, 1), tenor="2Y") is None


# ---------------------------------------------------------------------------
# Rule selection
# ---------------------------------------------------------------------------


def test_primary_rule_selection_unique_match() -> None:
    packages = [approved_hard("R1")]
    result = select_primary_rule(
        packages,
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        as_of_date=TODAY,
    )
    assert result.reason == "unique"
    assert result.package is packages[0]


def test_primary_rule_selection_no_match() -> None:
    packages = [approved_hard("R1")]
    result = select_primary_rule(
        packages,
        lender_qualification="small_loan_company",
        rule_context="其他地区",
        product=DEMO_PRODUCT,
        as_of_date=TODAY,
    )
    assert result.reason == "no_match"
    assert result.package is None


def test_primary_rule_selection_multiple_match_is_indeterminate() -> None:
    packages = [approved_hard("R1"), approved_hard("R2")]
    result = select_primary_rule(
        packages,
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        as_of_date=TODAY,
    )
    assert result.reason == "multiple_match"
    assert result.package is None
    assert {candidate.code for candidate in result.candidates} == {"R1", "R2"}


def test_primary_rule_selection_requires_confirmed_context() -> None:
    packages = [approved_hard("R1")]
    result = select_primary_rule(
        packages,
        lender_qualification="small_loan_company",
        rule_context=None,
        product=DEMO_PRODUCT,
        as_of_date=TODAY,
    )
    assert result.reason == "no_rule_context"
    assert result.package is None


def test_primary_rule_selection_respects_interval_and_status() -> None:
    packages = [
        approved_hard("FUTURE", effective_from=date(2027, 1, 1)),
        approved_hard("PAST", effective_from=date(2025, 1, 1), effective_until=date(2025, 12, 31)),
        Package(
            code="DRAFT",
            name="draft",
            kind="hard",
            status="draft",
            lender_qualification="small_loan_company",
            rule_context=DEMO_RULE_CONTEXT,
            product=DEMO_PRODUCT,
            effective_from=date(2025, 1, 1),
            effective_until=None,
            calc_type="annual_rate_limit",
            params={"threshold_pct": "24"},
        ),
    ]
    result = select_primary_rule(
        packages,
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        as_of_date=TODAY,
    )
    assert result.reason == "no_match"


def test_rule_in_effect() -> None:
    rule = approved_hard("R", effective_from=date(2026, 1, 1), effective_until=date(2026, 6, 30))
    assert rule_in_effect(rule, date(2026, 3, 1))
    assert not rule_in_effect(rule, date(2026, 7, 1))
    assert not rule_in_effect(rule, date(2025, 12, 31))


def test_applicable_references_ignores_lender_qualification() -> None:
    references = [
        approved_reference("REF1"),
        approved_reference("REF2", context="其他地区"),
    ]
    result = applicable_references(
        references,
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        as_of_date=TODAY,
    )
    assert [reference.code for reference in result] == ["REF1"]


def test_critical_fields_per_calc_type() -> None:
    rate_rule = approved_hard("R1", calc_type="annual_rate_limit")
    assert critical_fields_for(rate_rule) == {"interest_rate"}
    cost_rule = approved_hard(
        "R2", calc_type="effective_cost_limit", params={"threshold_pct": "36", "overdue_days": 90}
    )
    assert critical_fields_for(cost_rule) == {
        "loan_amount",
        "loan_term",
        "interest_rate",
        "repayment_method",
        "loan_fees",
        "overdue_interest_rate",
    }
    no_overdue = approved_hard(
        "R3", calc_type="effective_cost_limit", params={"threshold_pct": "36", "overdue_days": 0}
    )
    assert "overdue_interest_rate" not in critical_fields_for(no_overdue)


# ---------------------------------------------------------------------------
# annual_rate_limit
# ---------------------------------------------------------------------------


def test_annual_rate_limit_table_driven() -> None:
    cases = [
        (Decimal("23"), Decimal("24"), RedlineState.NOT_TRIGGERED),
        (Decimal("24"), Decimal("24"), RedlineState.NOT_TRIGGERED),
        (Decimal("24.01"), Decimal("24"), RedlineState.TRIGGERED),
        (None, Decimal("24"), RedlineState.INSUFFICIENT_DATA),
    ]
    for rate, threshold, expected in cases:
        result = evaluate_annual_rate_limit(rate, threshold)
        assert result.state == expected, (rate, threshold)
        assert result.steps, (rate, threshold)
    missing = evaluate_annual_rate_limit(None, Decimal("24"))
    assert missing.missing_inputs == ["interest_rate"]


# ---------------------------------------------------------------------------
# lpr_multiple_limit
# ---------------------------------------------------------------------------


def test_lpr_multiple_limit_table_driven() -> None:
    cases = [
        (Decimal("13"), Decimal("3.25"), Decimal("4"), RedlineState.NOT_TRIGGERED),
        (Decimal("13.1"), Decimal("3.25"), Decimal("4"), RedlineState.TRIGGERED),
        (None, Decimal("3.25"), Decimal("4"), RedlineState.INSUFFICIENT_DATA),
        (Decimal("13"), None, Decimal("4"), RedlineState.INSUFFICIENT_DATA),
    ]
    for rate, lpr, multiplier, expected in cases:
        result = evaluate_lpr_multiple_limit(rate, lpr, multiplier)
        assert result.state == expected, (rate, lpr, multiplier)
    missing = evaluate_lpr_multiple_limit(None, None, Decimal("4"))
    assert set(missing.missing_inputs) == {"interest_rate", "lpr"}


# ---------------------------------------------------------------------------
# effective_cost_limit
# ---------------------------------------------------------------------------


def cost_inputs(**overrides) -> dict:
    base = {
        "loan_amount": Decimal("100000"),
        "loan_term_months": 12,
        "annual_rate_pct": Decimal("12"),
        "repayment_method": "等额本息",
        "mandatory_fees": Decimal("0"),
        "overdue_rate_pct": Decimal("18"),
        "overdue_days": 90,
        "threshold_pct": Decimal("36"),
    }
    base.update(overrides)
    return base


def test_effective_cost_missing_inputs() -> None:
    result = evaluate_effective_cost(**cost_inputs(loan_amount=None))
    assert result.state == RedlineState.INSUFFICIENT_DATA
    assert "loan_amount" in result.missing_inputs
    result = evaluate_effective_cost(**cost_inputs(repayment_method=None))
    assert result.state == RedlineState.INSUFFICIENT_DATA
    assert "repayment_method" in result.missing_inputs


def test_effective_cost_no_fees_matches_nominal_rate() -> None:
    result = evaluate_effective_cost(**cost_inputs(mandatory_fees=Decimal("0")))
    assert result.state == RedlineState.NOT_TRIGGERED
    assert Decimal(result.metrics["effective_cost_pct"]) == pytest.approx(12, abs=0.01)
    assert Decimal(result.metrics["net_proceeds"]) == Decimal("100000")


def test_effective_cost_fees_reduce_proceeds_and_raise_cost() -> None:
    result = evaluate_effective_cost(**cost_inputs(mandatory_fees=Decimal("5000")))
    assert result.state == RedlineState.NOT_TRIGGERED
    assert Decimal(result.metrics["net_proceeds"]) == Decimal("95000")
    assert Decimal(result.metrics["effective_cost_pct"]) > 12


def test_effective_cost_triggers_above_threshold() -> None:
    result = evaluate_effective_cost(**cost_inputs(threshold_pct=Decimal("10")))
    assert result.state == RedlineState.TRIGGERED


def test_effective_cost_overdue_scenario_raises_risk_warning() -> None:
    result = evaluate_effective_cost(**cost_inputs(overdue_rate_pct=Decimal("120")))
    assert Decimal(result.metrics["effective_cost_pct"]) < 36
    assert Decimal(result.metrics["overdue_cost_pct"]) > 36
    assert result.state == RedlineState.RISK_WARNING


def test_effective_cost_without_overdue_scenario_skips_overdue_rate() -> None:
    result = evaluate_effective_cost(**cost_inputs(overdue_days=0, overdue_rate_pct=None))
    assert result.state == RedlineState.NOT_TRIGGERED
    assert "overdue_cost_pct" not in result.metrics


def test_effective_cost_unsupported_method_is_insufficient() -> None:
    result = evaluate_effective_cost(**cost_inputs(repayment_method="按月付息，到期还本"))
    assert result.state == RedlineState.INSUFFICIENT_DATA
    assert "repayment_method" in result.missing_inputs


def test_effective_cost_equal_principal_method() -> None:
    result = evaluate_effective_cost(
        **cost_inputs(repayment_method="等额本金", mandatory_fees=Decimal("0"))
    )
    assert result.state == RedlineState.NOT_TRIGGERED
    assert Decimal(result.metrics["effective_cost_pct"]) == pytest.approx(12, abs=0.01)


def test_effective_cost_exposes_every_step() -> None:
    result = evaluate_effective_cost(**cost_inputs(mandatory_fees=Decimal("2000")))
    labels = [step["label"] for step in result.steps]
    assert "实际可用本金" in labels
    assert "还款计划" in labels
    assert "现金流" in labels
    assert "正常履约综合年化成本" in labels
    assert "逾期情景（独立计算）" in labels
    assert "阈值比较" in labels


def test_effective_cost_fees_exceeding_principal_is_insufficient() -> None:
    result = evaluate_effective_cost(**cost_inputs(mandatory_fees=Decimal("200000")))
    assert result.state == RedlineState.INSUFFICIENT_DATA
    assert "loan_fees" in result.missing_inputs


# ---------------------------------------------------------------------------
# Aggregate evaluate_redline
# ---------------------------------------------------------------------------


def test_evaluate_redline_indeterminate_without_context() -> None:
    result = evaluate_redline(
        [approved_hard("R1")],
        [],
        lender_qualification="small_loan_company",
        rule_context=None,
        product=DEMO_PRODUCT,
        evaluation_date=TODAY,
        proposed_signing_date=None,
        confirmed={},
    )
    assert result.state == RedlineState.INDETERMINATE
    assert result.selection.reason == "no_rule_context"


def test_evaluate_redline_unique_match_with_lpr() -> None:
    packages = [approved_hard("RATE", calc_type="lpr_multiple_limit", params={"multiplier": "4"})]
    entries = [Entry(date(2026, 1, 20), "1Y", "3.10")]
    result = evaluate_redline(
        packages,
        entries,
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        evaluation_date=TODAY,
        proposed_signing_date=date(2026, 6, 1),
        confirmed=confirmed(interest_rate="12"),
    )
    assert result.state == RedlineState.NOT_TRIGGERED
    assert result.selection.package is packages[0]
    assert result.lpr_entry is entries[0]
    assert result.lpr_provisional is False


def test_evaluate_redline_provisional_lpr_without_signing_date() -> None:
    packages = [approved_hard("RATE", calc_type="lpr_multiple_limit", params={"multiplier": "4"})]
    entries = [Entry(date(2026, 1, 20), "1Y", "3.10")]
    result = evaluate_redline(
        packages,
        entries,
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        evaluation_date=TODAY,
        proposed_signing_date=None,
        confirmed=confirmed(interest_rate="13"),
    )
    assert result.lpr_provisional is True


def test_evaluate_redline_missing_lpr_is_insufficient_data() -> None:
    packages = [approved_hard("RATE", calc_type="lpr_multiple_limit", params={"multiplier": "4"})]
    result = evaluate_redline(
        packages,
        [],
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        evaluation_date=TODAY,
        proposed_signing_date=date(2026, 6, 1),
        confirmed=confirmed(interest_rate="13"),
    )
    assert result.state == RedlineState.INSUFFICIENT_DATA
    assert result.primary.missing_inputs == ["lpr"]


def test_evaluate_redline_missing_critical_input_never_not_triggered() -> None:
    packages = [approved_hard("R1")]
    result = evaluate_redline(
        packages,
        [],
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        evaluation_date=TODAY,
        proposed_signing_date=None,
        confirmed={},
    )
    assert result.state == RedlineState.INSUFFICIENT_DATA
    assert result.primary.missing_inputs == ["interest_rate"]


def test_evaluate_redline_references_are_separate_warnings() -> None:
    packages = [
        approved_hard("HARD"),
        approved_reference("REF", calc_type="annual_rate_limit", params={"threshold_pct": "24"}),
    ]
    result = evaluate_redline(
        packages,
        [],
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        evaluation_date=TODAY,
        proposed_signing_date=None,
        confirmed=confirmed(interest_rate="30"),
    )
    # hard rule triggered; reference also crossed as a risk warning
    assert result.state == RedlineState.TRIGGERED
    assert len(result.references) == 1
    reference, evaluation = result.references[0]
    assert reference.code == "REF"
    assert evaluation.state == RedlineState.RISK_WARNING


def test_evaluate_redline_multiple_match_is_indeterminate() -> None:
    packages = [approved_hard("R1"), approved_hard("R2")]
    result = evaluate_redline(
        packages,
        [],
        lender_qualification="small_loan_company",
        rule_context=DEMO_RULE_CONTEXT,
        product=DEMO_PRODUCT,
        evaluation_date=TODAY,
        proposed_signing_date=None,
        confirmed=confirmed(interest_rate="13"),
    )
    assert result.state == RedlineState.INDETERMINATE
    assert result.selection.reason == "multiple_match"


def test_evaluate_rule_effective_cost_from_typed_values() -> None:
    rule = approved_hard(
        "COST",
        calc_type="effective_cost_limit",
        params={"threshold_pct": "36", "overdue_days": 90},
    )
    result = evaluate_rule(
        rule,
        confirmed=confirmed(
            loan_amount="100000",
            loan_term="12",
            interest_rate="12",
            repayment_method="等额本息",
            loan_fees="0",
            overdue_interest_rate="18",
        ),
        lpr_value_pct=None,
    )
    assert result.state == RedlineState.NOT_TRIGGERED


# ---------------------------------------------------------------------------
# Params validation
# ---------------------------------------------------------------------------


def test_validate_calc_params_table_driven() -> None:
    valid = [
        ("annual_rate_limit", {"threshold_pct": "24"}),
        ("lpr_multiple_limit", {"multiplier": "4"}),
        ("effective_cost_limit", {"threshold_pct": "36", "overdue_days": 90}),
        ("effective_cost_limit", {"threshold_pct": "36", "overdue_days": 0}),
    ]
    for calc_type, params in valid:
        validate_calc_params(calc_type, params)

    invalid = [
        ("annual_rate_limit", {}),
        ("annual_rate_limit", {"threshold_pct": "0"}),
        ("annual_rate_limit", {"threshold_pct": "-1"}),
        ("lpr_multiple_limit", {"multiplier": "abc"}),
        ("effective_cost_limit", {"threshold_pct": "36"}),
        ("effective_cost_limit", {"threshold_pct": "36", "overdue_days": -1}),
        ("effective_cost_limit", {"threshold_pct": "36", "overdue_days": 90, "extra": 1}),
        ("unknown_type", {"threshold_pct": "24"}),
    ]
    for calc_type, params in invalid:
        with pytest.raises(ValueError):
            validate_calc_params(calc_type, params)


def test_calc_types_are_code_defined() -> None:
    assert set(CALC_TYPES) == {"annual_rate_limit", "lpr_multiple_limit", "effective_cost_limit"}
