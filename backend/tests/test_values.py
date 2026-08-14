"""Decimal/currency/unit/period/date normalization keeps the original text."""

from decimal import Decimal

from app.fields import FieldDef, FieldGroup, ValueType
from app.values import (
    TypedValue,
    detect_currency,
    normalize_amount,
    normalize_date,
    normalize_field,
    normalize_integer,
    normalize_rate,
)


def test_amount_keeps_raw_text_and_normalizes_unit_multiplier() -> None:
    typed = normalize_amount("贷款金额：500万元")
    assert typed.raw_text == "贷款金额：500万元"
    assert typed.value == "5000000"
    assert typed.unit == "10000"
    assert typed.currency == "CNY"
    assert Decimal(typed.value) == Decimal(500) * 10000


def test_bare_number_does_not_guess_currency() -> None:
    typed = normalize_amount("5000")
    assert typed.value == "5000"
    assert typed.currency is None
    assert typed.unit == "1"


def test_explicit_foreign_currency_is_kept() -> None:
    typed = normalize_amount("USD 12,000.50")
    assert typed.value == "12000.50"
    assert typed.currency == "USD"
    assert typed.unit == "1"


def test_explicit_foreign_currency_wins_over_unit_marker() -> None:
    assert detect_currency("USD 500元") == "USD"
    assert detect_currency("欧元500元") == "EUR"
    assert detect_currency("500元") == "CNY"


def test_detect_currency_returns_none_for_unknown() -> None:
    assert detect_currency("5000") is None
    assert detect_currency("5000元") == "CNY"
    assert detect_currency("US$500") == "USD"


def test_rate_preserves_period_and_method_with_annualized_value() -> None:
    annual = normalize_rate("年利率3.85%")
    assert annual.value == "3.85"
    assert annual.period == "年"
    assert annual.method == "nominal"
    monthly = normalize_rate("月利率0.5%")
    assert monthly.value == "6.0"
    assert monthly.period == "月"
    effective = normalize_rate("执行利率（实际）4.2%")
    assert effective.method == "effective"


def test_date_normalizes_chinese_and_iso_forms() -> None:
    chinese = normalize_date("拟签约日期：2026年8月7日")
    assert chinese.value == "2026-08-07"
    assert chinese.date == "2026-08-07"
    iso = normalize_date("2026/08/07")
    assert iso.value == "2026-08-07"


def test_normalize_field_dispatches_by_declared_type() -> None:
    amount_field = FieldDef("x", FieldGroup.PROPOSED_LOAN, "金额", ValueType.AMOUNT, ("金额",))
    typed = normalize_field(amount_field, "300万元")
    assert typed.type == ValueType.AMOUNT
    assert typed.value == "3000000"
    text_field = FieldDef("y", FieldGroup.IDENTITY, "名称", ValueType.TEXT, ("名称",))
    assert normalize_field(text_field, "示例企业").value == "示例企业"


def test_integer_with_month_unit_normalizes_loan_terms_to_months() -> None:
    months = normalize_integer("36个月", default_unit="月")
    assert months.value == "36"
    assert months.unit == "月"
    years = normalize_integer("2年", default_unit="月")
    assert years.value == "24"
    assert years.unit == "月"
    plain = normalize_integer("36个月")
    assert plain.unit is None


def test_typed_value_stored_json_is_serializable() -> None:
    typed = TypedValue(
        type=ValueType.AMOUNT,
        value="1.5",
        raw_text="1.5元",
        unit="1",
        currency="CNY",
    )
    stored = typed.model_dump_stored()
    assert stored["type"] == "amount"
    assert stored["currency"] == "CNY"
