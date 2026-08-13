"""Typed value normalization for field candidates and resolutions.

The original text is always preserved next to the normalized value. Money is
kept as a ``Decimal`` string in the base unit with an explicit unit multiplier
and currency; when the currency cannot be determined it is recorded as ``None``
rather than guessed. Rates keep their original period and method alongside an
annualized value. Dates are normalized to ISO.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel

from app.fields import FieldDef, ValueType

AMOUNT_RE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|万元|千元|元|亿|万)?")
PERCENT_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*%")
DATE_RE = re.compile(
    r"(20\d{2}|19\d{2})[-年/](0?[1-9]|1[0-2])[-月/]([12][0-9]|3[01]|0?[1-9])日?"
)

UNIT_MULTIPLIERS = {
    "元": 1,
    "千元": 1000,
    "万元": 10000,
    "亿": 100000000,
    "亿元": 100000000,
    "万": 10000,
}

CURRENCY_MARKERS = (
    ("USD", ("美元", "USD", "usd", "$")),
    ("EUR", ("欧元", "EUR", "eur")),
    ("HKD", ("港币", "港元", "HKD", "hkd")),
    ("CNY", ("¥", "￥", "人民币", "元", "RMB", "CNY", "cny", "rmb")),
)


class TypedValue(BaseModel):
    """Normalized value with the original text and full unit context."""

    type: ValueType
    value: str
    raw_text: str
    unit: str | None = None
    currency: str | None = None
    period: str | None = None
    method: str | None = None
    date: str | None = None
    columns: dict[str, str] | None = None

    def model_dump_stored(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def detect_currency(text: str) -> str | None:
    """Return the currency code when the text states it explicitly; else None."""
    for code, markers in CURRENCY_MARKERS:
        if any(marker in text for marker in markers):
            return code
    return None


def strip_commas(text: str) -> str:
    return text.replace(",", "").replace("，", "").strip()


def parse_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(strip_commas(text))
    except InvalidOperation:
        return None


def _decimal_str(value: Decimal) -> str:
    return format(value, "f")


def normalize_amount(
    text: str, *, default_currency: str | None = None, default_unit: str | None = None
) -> TypedValue | None:
    match = AMOUNT_RE.search(text)
    if not match:
        return None
    number = parse_decimal(match.group(1))
    if number is None:
        return None
    unit_label = match.group(2)
    multiplier = UNIT_MULTIPLIERS.get(unit_label or "", 1) if unit_label else (
        UNIT_MULTIPLIERS.get(default_unit, 1) if default_unit else 1
    )
    currency = detect_currency(text) or default_currency
    return TypedValue(
        type=ValueType.AMOUNT,
        value=_decimal_str(number * multiplier),
        raw_text=text.strip(),
        unit=str(multiplier),
        currency=currency,
    )


RATE_METHODS = {
    "实际": "effective",
    "有效": "effective",
    "名义": "nominal",
    "固定": "nominal",
    "浮动": "floating",
}


def normalize_rate(text: str) -> TypedValue | None:
    match = PERCENT_RE.search(text)
    if not match:
        return None
    number = parse_decimal(match.group(1))
    if number is None:
        return None
    before = text[: match.start()]
    period = None
    for label, annualize in (("日利率", 360), ("月利率", 12), ("年利率", 1)):
        if label in before or label in text:
            period = label[0]
            break
    method = "nominal"
    for label, code in RATE_METHODS.items():
        if label in text:
            method = code
            break
    if period == "日":
        annualized = number * 360
    elif period == "月":
        annualized = number * 12
    else:
        annualized = number
    return TypedValue(
        type=ValueType.RATE,
        value=_decimal_str(annualized),
        raw_text=text.strip(),
        period=period or "年",
        method=method,
    )


def normalize_date(text: str) -> TypedValue | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    iso = f"{year:04d}-{month:02d}-{day:02d}"
    return TypedValue(
        type=ValueType.DATE,
        value=iso,
        raw_text=text.strip(),
        date=iso,
    )


def normalize_integer(text: str, default_unit: str | None = None) -> TypedValue | None:
    """Extract the integer; with ``default_unit == '月'`` normalize loan terms to months."""
    match = re.search(r"\d+", text)
    if not match:
        return None
    typed = TypedValue(type=ValueType.INTEGER, value=match.group(0), raw_text=text.strip())
    if default_unit == "月":
        term = re.search(r"(\d+)\s*(年|个月|月)?", text)
        months = int(term.group(1)) * 12 if term and term.group(2) == "年" else int(term.group(1))
        typed.value = str(months)
        typed.unit = "月"
    return typed


def normalize_text(text: str) -> TypedValue:
    return TypedValue(type=ValueType.TEXT, value=text.strip(), raw_text=text.strip())


def normalize_field(
    field: FieldDef,
    text: str,
    *,
    default_currency: str | None = None,
    default_unit: str | None = None,
) -> TypedValue | None:
    """Normalize ``text`` according to the field's declared value type."""
    if field.value_type == ValueType.AMOUNT:
        return normalize_amount(
            text, default_currency=default_currency, default_unit=default_unit
        )
    if field.value_type == ValueType.RATE:
        return normalize_rate(text)
    if field.value_type == ValueType.DATE:
        return normalize_date(text)
    if field.value_type == ValueType.INTEGER:
        return normalize_integer(text, default_unit)
    return normalize_text(text)
