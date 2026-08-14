"""Rule packages, LPR selection, Decimal redline evaluation, and immutable runs.

Design section 7: the redline engine only treats institution-approved rules
with explicit lender qualification, rule context, product, and effective
interval as hard rules. Judicial-risk references such as 4xLPR or 24% are
separate lines that never produce "not triggered"; they only warn. Calculation
types are code-defined and published with tests; rule packages only configure
scope, parameters, thresholds, and legal basis — never executable formulas.
The primary rule package must match uniquely; zero or multiple matches are
explicit indeterminate outcomes.

All money, rate, and cash-flow math uses ``Decimal`` (never float) and every
step is exposed. A formal run freezes rule + inputs + steps into an immutable
snapshot with a content hash (ADR-0005).
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.completeness import sha256_json
from app.models import (
    LprEntry,
    LprImport,
    LprImportStatus,
    RedlineRun,
    Resolution,
    RuleContextConfirmation,
    RuleKind,
    RulePackage,
    RuleStatus,
    RunStatus,
)
from app.values import UNIT_MULTIPLIERS

MONEY_QUANT = Decimal("0.01")
RATE_PCT_QUANT = Decimal("0.01")  # display precision for percent values
IRR_PRECISION = Decimal("1e-12")
IRR_MAX_ITERATIONS = 200
OVERDUE_YEAR_DAYS = 360  # 银行惯例计息基数，与设计文档一致

CALC_TYPES: dict[str, dict[str, Any]] = {
    "annual_rate_limit": {
        "label": "年化利率上限",
        "params": {
            "threshold_pct": {"type": "decimal", "gt": 0},
        },
    },
    "lpr_multiple_limit": {
        "label": "LPR 倍数上限",
        "params": {
            "multiplier": {"type": "decimal", "gt": 0},
        },
    },
    "effective_cost_limit": {
        "label": "综合年化成本上限",
        "params": {
            "threshold_pct": {"type": "decimal", "gt": 0},
            "overdue_days": {"type": "integer", "ge": 0},
        },
    },
}

CALC_LABELS = {code: spec["label"] for code, spec in CALC_TYPES.items()}

# Critical inputs per calc type. For effective-cost rules the overdue rate is
# only critical when the rule configures an overdue scenario (overdue_days > 0).
CRITICAL_FIELDS: dict[str, set[str]] = {
    "annual_rate_limit": {"interest_rate"},
    "lpr_multiple_limit": {"interest_rate"},
    "effective_cost_limit": {
        "loan_amount",
        "loan_term",
        "interest_rate",
        "repayment_method",
        "loan_fees",
        "overdue_interest_rate",
    },
}

DEMO_RULE_CONTEXT = "全国"
DEMO_PRODUCT = "经营贷"

RATE_LABELS = {
    "loan_amount": "贷款金额",
    "loan_term": "贷款期限",
    "interest_rate": "利率",
    "repayment_method": "还款方式",
    "loan_fees": "必要费用",
    "overdue_interest_rate": "罚息利率",
}


# ---------------------------------------------------------------------------
# Decimal helpers
# ---------------------------------------------------------------------------


def d(value: Any) -> Decimal:
    """Parse a stored decimal string (or number) into Decimal, never float."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def parse_optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return d(value)
    except (InvalidOperation, ValueError):
        return None


FEE_AMOUNT_RE = re.compile(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|万元|千元|元|亿|万)?")


def parse_fee_amount(text: str) -> Decimal | None:
    """Extract a monetary amount from a free-text fee entry.

    ``loan_fees`` stays a TEXT extraction field (fee-name lists must still
    extract for the golden contract); the redline engine parses the amount
    from the confirmed text. ``"0"``/``"5000"`` parse directly; a text such
    as ``"担保费5000元"`` yields 5000; a fee-name list without amounts yields
    None, which the evaluator reports as insufficient data (fails safe).
    """
    stripped = text.replace(",", "").replace("，", "").strip()
    try:
        return Decimal(stripped)
    except InvalidOperation:
        pass
    match = FEE_AMOUNT_RE.search(text)
    if not match:
        return None
    value = parse_optional_decimal(match.group(1))
    if value is None:
        return None
    return value * d(UNIT_MULTIPLIERS.get(match.group(2) or "", 1))


def fmt_money(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP), "f")


def fmt_rate_pct(value: Decimal) -> str:
    return format(value.quantize(RATE_PCT_QUANT, rounding=ROUND_HALF_UP), "f")


def rate_frac(percent: Decimal) -> Decimal:
    """Percent (e.g. 12 for 12%) to fraction (0.12)."""
    return percent / 100


def is_strictly_positive(value: Decimal) -> bool:
    return value > 0


# ---------------------------------------------------------------------------
# Pure LPR selection
# ---------------------------------------------------------------------------


class LprLike(Protocol):
    effective_date: date
    tenor: str
    value: str


def select_lpr(entries: list[LprLike], as_of_date: date, tenor: str = "1Y") -> LprLike | None:
    """Latest published entry whose effective date is on or before ``as_of_date``."""
    eligible = [
        entry for entry in entries if entry.tenor == tenor and entry.effective_date <= as_of_date
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda entry: entry.effective_date)


# ---------------------------------------------------------------------------
# Pure rule selection
# ---------------------------------------------------------------------------


class RuleLike(Protocol):
    kind: str
    status: str
    lender_qualification: str
    rule_context: str
    product: str
    effective_from: date
    effective_until: date | None


@dataclass
class SelectionResult:
    """Primary rule package selection. ``package`` is set only on unique match."""

    package: RuleLike | None
    reason: str  # unique | no_rule_context | no_match | multiple_match
    candidates: list[RuleLike] = field(default_factory=list)


def rule_in_effect(rule: RuleLike, as_of_date: date) -> bool:
    return (
        rule.status == RuleStatus.APPROVED
        and rule.effective_from <= as_of_date
        and (rule.effective_until is None or rule.effective_until >= as_of_date)
    )


def select_primary_rule(
    packages: list[RuleLike],
    *,
    lender_qualification: str,
    rule_context: str | None,
    product: str,
    as_of_date: date,
) -> SelectionResult:
    """Unique approved hard-rule match; none/multiple are indeterminate.

    Rule context is an explicitly confirmed input — never inferred from the
    borrower address. Without a confirmed context there can be no match.
    """
    if not rule_context:
        return SelectionResult(package=None, reason="no_rule_context", candidates=[])
    matches = [
        package
        for package in packages
        if package.kind == RuleKind.HARD
        and package.lender_qualification == lender_qualification
        and package.rule_context == rule_context
        and package.product == product
        and rule_in_effect(package, as_of_date)
    ]
    if len(matches) == 1:
        return SelectionResult(package=matches[0], reason="unique", candidates=matches)
    if not matches:
        return SelectionResult(package=None, reason="no_match", candidates=[])
    return SelectionResult(package=None, reason="multiple_match", candidates=matches)


def applicable_references(
    packages: list[RuleLike],
    *,
    rule_context: str | None,
    product: str,
    as_of_date: date,
) -> list[RuleLike]:
    """Approved reference lines matching context, product, and interval.

    Judicial-risk references are general legal-calibration hints rather than
    institution-specific approvals, so they do not filter on lender
    qualification. Multiple references can apply at once.
    """
    if not rule_context:
        return []
    return [
        package
        for package in packages
        if package.kind == RuleKind.REFERENCE
        and package.rule_context == rule_context
        and package.product == product
        and rule_in_effect(package, as_of_date)
    ]


# ---------------------------------------------------------------------------
# Code-defined evaluators (pure, Decimal)
# ---------------------------------------------------------------------------


class RedlineState:
    TRIGGERED = "triggered"
    NOT_TRIGGERED = "not_triggered"
    RISK_WARNING = "risk_warning"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


STATE_LABELS = {
    RedlineState.TRIGGERED: "触发硬规则",
    RedlineState.NOT_TRIGGERED: "未触发硬规则",
    RedlineState.RISK_WARNING: "风险提示",
    RedlineState.INSUFFICIENT_DATA: "资料不足",
    RedlineState.NOT_APPLICABLE: "规则不适用",
    RedlineState.INDETERMINATE: "无法确定适用规则",
}

REFERENCE_STATE_LABELS = {
    RedlineState.RISK_WARNING: "触及风险参考线",
    RedlineState.NOT_TRIGGERED: "未触及风险参考线",
    RedlineState.INSUFFICIENT_DATA: "资料不足",
}


@dataclass
class RuleEvaluation:
    state: str
    steps: list[dict]
    metrics: dict
    missing_inputs: list[str]
    reason: str


def _insufficient(missing: list[str]) -> RuleEvaluation:
    return RuleEvaluation(
        state=RedlineState.INSUFFICIENT_DATA,
        steps=[
            {
                "label": "关键输入核验",
                "detail": f"缺少或未确认关键输入：{'、'.join(missing)}",
            }
        ],
        metrics={},
        missing_inputs=missing,
        reason="资料不足：关键输入缺失或未经确认",
    )


def npv(cash_flows: list[Decimal], monthly_rate: Decimal) -> Decimal:
    total = Decimal(0)
    for index, flow in enumerate(cash_flows):
        total += flow / (1 + monthly_rate) ** index
    return total


def irr_monthly(cash_flows: list[Decimal]) -> Decimal | None:
    """Monthly rate r such that NPV(cash_flows, r) == 0 via Decimal bisection.

    Returns None when there is no sign change in [0, 0.5] (no positive rate).
    """
    if npv(cash_flows, Decimal(0)) <= 0:
        return Decimal(0)
    low, high = Decimal(0), Decimal("0.5")
    for _ in range(IRR_MAX_ITERATIONS):
        mid = (low + high) / 2
        if npv(cash_flows, mid) > 0:
            low = mid
        else:
            high = mid
        if high - low < IRR_PRECISION:
            break
    return (low + high) / 2


def equal_installment_payment(principal: Decimal, monthly_rate: Decimal, periods: int) -> Decimal:
    if monthly_rate == 0:
        return principal / periods
    factor = (1 + monthly_rate) ** periods
    return principal * monthly_rate * factor / (factor - 1)


def equal_principal_schedule(
    principal: Decimal, monthly_rate: Decimal, periods: int
) -> list[Decimal]:
    principal_per_period = principal / periods
    payments: list[Decimal] = []
    remaining = principal
    for _ in range(periods):
        interest = remaining * monthly_rate
        payments.append(principal_per_period + interest)
        remaining -= principal_per_period
    return payments


def build_schedule(
    principal: Decimal,
    monthly_rate: Decimal,
    periods: int,
    repayment_method: str,
) -> tuple[str, list[Decimal]] | None:
    """Return (method_label, per-period payments) or None if unsupported."""
    if "等额本息" in repayment_method:
        return "等额本息", [equal_installment_payment(principal, monthly_rate, periods)] * periods
    if "等额本金" in repayment_method:
        return "等额本金", equal_principal_schedule(principal, monthly_rate, periods)
    return None


def _rate_steps(annual_rate_pct: Decimal) -> list[dict]:
    return [
        {
            "label": "年化名义利率",
            "detail": f"{fmt_rate_pct(annual_rate_pct)}%（年化）",
        },
        {
            "label": "月利率",
            "detail": (
                f"{fmt_rate_pct(annual_rate_pct / 12)}% = {fmt_rate_pct(annual_rate_pct)}% ÷ 12"
            ),
        },
    ]


def evaluate_annual_rate_limit(
    annual_rate_pct: Decimal | None, threshold_pct: Decimal
) -> RuleEvaluation:
    if annual_rate_pct is None:
        return _insufficient(["interest_rate"])
    threshold = rate_frac(threshold_pct)
    actual = rate_frac(annual_rate_pct)
    triggered = actual > threshold
    return RuleEvaluation(
        state=RedlineState.TRIGGERED if triggered else RedlineState.NOT_TRIGGERED,
        steps=[
            {
                "label": "年化名义利率",
                "detail": f"{fmt_rate_pct(annual_rate_pct)}%（年化）",
            },
            {
                "label": "规则阈值",
                "detail": f"{fmt_rate_pct(threshold_pct)}%（年化）",
            },
            {
                "label": "比较",
                "detail": (
                    f"{fmt_rate_pct(annual_rate_pct)}% "
                    f"{'＞' if triggered else '≤'} {fmt_rate_pct(threshold_pct)}% → "
                    f"{'触发' if triggered else '未触发'}"
                ),
            },
        ],
        metrics={
            "annual_rate": str(annual_rate_pct),
            "threshold_pct": str(threshold_pct),
        },
        missing_inputs=[],
        reason=("已触发：年化利率超过规则阈值" if triggered else "未触发：年化利率未超过规则阈值"),
    )


def evaluate_lpr_multiple_limit(
    annual_rate_pct: Decimal | None,
    lpr_value_pct: Decimal | None,
    multiplier: Decimal,
) -> RuleEvaluation:
    missing = []
    if annual_rate_pct is None:
        missing.append("interest_rate")
    if lpr_value_pct is None:
        missing.append("lpr")
    if missing:
        return _insufficient(missing)
    limit = rate_frac(lpr_value_pct) * multiplier
    actual = rate_frac(annual_rate_pct)
    triggered = actual > limit
    return RuleEvaluation(
        state=RedlineState.TRIGGERED if triggered else RedlineState.NOT_TRIGGERED,
        steps=[
            {
                "label": "一年期 LPR",
                "detail": f"{fmt_rate_pct(lpr_value_pct)}%（年化）",
            },
            {
                "label": "规则倍数",
                "detail": f"×{format(multiplier, 'f')}",
            },
            {
                "label": "计算上限",
                "detail": (
                    f"{fmt_rate_pct(lpr_value_pct)}% × {format(multiplier, 'f')} = "
                    f"{fmt_rate_pct(limit * 100)}%（年化）"
                ),
            },
            {
                "label": "年化名义利率",
                "detail": f"{fmt_rate_pct(annual_rate_pct)}%（年化）",
            },
            {
                "label": "比较",
                "detail": (
                    f"{fmt_rate_pct(annual_rate_pct)}% "
                    f"{'＞' if triggered else '≤'} {fmt_rate_pct(limit * 100)}% → "
                    f"{'触发' if triggered else '未触发'}"
                ),
            },
        ],
        metrics={
            "annual_rate": str(annual_rate_pct),
            "lpr_pct": str(lpr_value_pct),
            "multiplier": str(multiplier),
            "limit_pct": str(limit * 100),
        },
        missing_inputs=[],
        reason=(
            "已触发：年化利率超过 LPR 倍数上限"
            if triggered
            else "未触发：年化利率未超过 LPR 倍数上限"
        ),
    )


def evaluate_effective_cost(
    *,
    loan_amount: Decimal | None,
    loan_term_months: int | None,
    annual_rate_pct: Decimal | None,
    repayment_method: str | None,
    mandatory_fees: Decimal | None,
    overdue_rate_pct: Decimal | None,
    overdue_days: int,
    threshold_pct: Decimal,
) -> RuleEvaluation:
    missing = []
    required = {"loan_amount", "loan_term", "interest_rate", "repayment_method", "loan_fees"}
    if overdue_days > 0:
        required.add("overdue_interest_rate")
    values: dict[str, Any] = {
        "loan_amount": loan_amount,
        "loan_term": loan_term_months,
        "interest_rate": annual_rate_pct,
        "repayment_method": repayment_method,
        "loan_fees": mandatory_fees,
        "overdue_interest_rate": overdue_rate_pct,
    }
    for key in sorted(required):
        if values[key] is None:
            missing.append(key)
    if missing:
        return _insufficient(missing)
    if loan_term_months <= 0:
        return _insufficient(["loan_term"])
    if loan_amount <= 0:
        return _insufficient(["loan_amount"])

    steps: list[dict] = []
    steps.extend(_rate_steps(annual_rate_pct))
    monthly_rate = rate_frac(annual_rate_pct) / 12
    fees = mandatory_fees or Decimal(0)
    net_proceeds = loan_amount - fees
    if fees < 0 or net_proceeds <= 0:
        return _insufficient(["loan_fees"])
    steps.append(
        {
            "label": "实际可用本金",
            "detail": (
                f"{fmt_money(loan_amount)} - {fmt_money(fees)}（放款时扣除的必要费用）"
                f" = {fmt_money(net_proceeds)}"
            ),
        }
    )

    schedule = build_schedule(loan_amount, monthly_rate, loan_term_months, repayment_method)
    if schedule is None:
        return _insufficient(["repayment_method"])
    method_label, payments = schedule
    total_paid = sum(payments, Decimal(0))
    steps.append(
        {
            "label": "还款计划",
            "detail": (
                f"{method_label}，共 {loan_term_months} 期，每期应还 "
                f"{fmt_money(payments[0])}，合计应还 {fmt_money(total_paid)}"
            ),
        }
    )
    steps.append({"label": "现金流", "detail": _cash_flow_summary(loan_amount, fees, payments)})

    cash_flows = [-net_proceeds] + payments
    monthly_irr = irr_monthly(cash_flows)
    if monthly_irr is None:
        return _insufficient(["loan_amount"])
    effective_cost = monthly_irr * 12 * 100
    steps.append(
        {
            "label": "正常履约综合年化成本",
            "detail": (
                f"现金流内含月利率 {fmt_rate_pct(monthly_irr * 100)}%"
                f"（二分法求解，精度 {format(IRR_PRECISION, 'f')}），"
                f"年化 = {fmt_rate_pct(monthly_irr * 100)}% × 12 = "
                f"{fmt_rate_pct(effective_cost)}%"
            ),
        }
    )

    threshold = rate_frac(threshold_pct)
    normal_triggered = rate_frac(effective_cost) > threshold

    overdue_cost: Decimal | None = None
    if overdue_days > 0:
        overdue_interest = (
            loan_amount * rate_frac(overdue_rate_pct) * overdue_days / OVERDUE_YEAR_DAYS
        )
        overdue_flows = cash_flows + [overdue_interest]
        overdue_monthly = irr_monthly(overdue_flows)
        overdue_cost = overdue_monthly * 12 * 100 if overdue_monthly is not None else None
        steps.append(
            {
                "label": "逾期情景（独立计算）",
                "detail": (
                    f"按本金 {fmt_money(loan_amount)}、罚息利率 {fmt_rate_pct(overdue_rate_pct)}%、"
                    f"逾期 {overdue_days} 天（360 天基数）计算额外逾期利息 "
                    f"{fmt_money(overdue_interest)}；含逾期利息的综合年化成本 "
                    f"{fmt_rate_pct(overdue_cost) if overdue_cost is not None else '—'}%"
                ),
            }
        )

    steps.append(
        {
            "label": "阈值比较",
            "detail": (
                f"正常履约综合年化成本 {fmt_rate_pct(effective_cost)}% 对比阈值 "
                f"{fmt_rate_pct(threshold_pct)}%"
            ),
        }
    )

    overdue_triggered = bool(
        overdue_days > 0 and overdue_cost is not None and rate_frac(overdue_cost) > threshold
    )
    if normal_triggered:
        state = RedlineState.TRIGGERED
        reason = "已触发：正常履约综合年化成本超过规则阈值"
    elif overdue_triggered:
        state = RedlineState.RISK_WARNING
        reason = "风险提示：正常履约成本未超阈值，但逾期情景综合成本超过阈值"
    else:
        state = RedlineState.NOT_TRIGGERED
        reason = "未触发：正常履约与逾期情景综合年化成本均未超过规则阈值"

    metrics: dict[str, Any] = {
        "loan_amount": str(loan_amount),
        "net_proceeds": str(net_proceeds),
        "loan_term_months": loan_term_months,
        "repayment_method": method_label,
        "monthly_payment": str(payments[0]),
        "total_paid": str(total_paid),
        "effective_cost_pct": str(effective_cost),
        "threshold_pct": str(threshold_pct),
        "overdue_days": overdue_days,
    }
    if overdue_cost is not None:
        metrics["overdue_cost_pct"] = str(overdue_cost)
    return RuleEvaluation(
        state=state, steps=steps, metrics=metrics, missing_inputs=[], reason=reason
    )


def _cash_flow_summary(loan_amount: Decimal, fees: Decimal, payments: list[Decimal]) -> str:
    head = f"第0期 -{fmt_money(loan_amount - fees)}（实际可用本金）"
    shown = [f"第{i + 1}期 +{fmt_money(payment)}" for i, payment in enumerate(payments[:3])]
    suffix = "…" if len(payments) > 3 else ""
    return head + "；" + "；".join(shown) + suffix


def evaluate_rule(
    rule: RuleLike,
    *,
    confirmed: dict[str, dict],
    lpr_value_pct: Decimal | None,
) -> RuleEvaluation:
    """Evaluate one rule against confirmed typed values."""
    calc_type = rule.calc_type
    params = rule.params

    def value(key: str) -> Decimal | None:
        return parse_optional_decimal(confirmed.get(key, {}).get("value"))

    def fee_value() -> Decimal | None:
        raw = confirmed.get("loan_fees", {}).get("value")
        if raw is None:
            return None
        return parse_fee_amount(str(raw))

    if calc_type == "annual_rate_limit":
        return evaluate_annual_rate_limit(value("interest_rate"), d(params["threshold_pct"]))
    if calc_type == "lpr_multiple_limit":
        return evaluate_lpr_multiple_limit(
            value("interest_rate"), lpr_value_pct, d(params["multiplier"])
        )
    if calc_type == "effective_cost_limit":
        term = confirmed.get("loan_term", {}).get("value")
        term_months = None
        if term is not None:
            try:
                term_months = int(Decimal(str(term)))
            except (InvalidOperation, ValueError):
                term_months = None
        return evaluate_effective_cost(
            loan_amount=value("loan_amount"),
            loan_term_months=term_months,
            annual_rate_pct=value("interest_rate"),
            repayment_method=confirmed.get("repayment_method", {}).get("value"),
            mandatory_fees=fee_value(),
            overdue_rate_pct=value("overdue_interest_rate"),
            overdue_days=int(params.get("overdue_days", 0)),
            threshold_pct=d(params["threshold_pct"]),
        )
    return _insufficient([f"calc_type:{calc_type}"])


def critical_fields_for(rule: RuleLike) -> set[str]:
    critical = set(CRITICAL_FIELDS.get(rule.calc_type, set()))
    if rule.calc_type == "effective_cost_limit" and int(rule.params.get("overdue_days", 0)) == 0:
        critical.discard("overdue_interest_rate")
    return critical


# ---------------------------------------------------------------------------
# Aggregate evaluation
# ---------------------------------------------------------------------------


@dataclass
class RedlineResult:
    state: str
    selection: SelectionResult
    primary: RuleEvaluation | None
    references: list[tuple[RuleLike, RuleEvaluation]]
    lpr_entry: LprLike | None
    lpr_provisional: bool
    lpr_as_of_date: date
    evaluation_date: date


def evaluate_redline(
    packages: list[RuleLike],
    lpr_entries: list[LprLike],
    *,
    lender_qualification: str,
    rule_context: str | None,
    product: str,
    evaluation_date: date,
    proposed_signing_date: date | None,
    confirmed: dict[str, dict],
) -> RedlineResult:
    """Deterministic aggregate evaluation documented by design section 7.5."""
    selection = select_primary_rule(
        packages,
        lender_qualification=lender_qualification,
        rule_context=rule_context,
        product=product,
        as_of_date=evaluation_date,
    )

    # With a signing date the LPR is selected from published announcements as
    # of that date (only published imports are selectable); without one the
    # evaluation-date estimate is marked provisional.
    lpr_as_of_date = proposed_signing_date or evaluation_date
    lpr_provisional = proposed_signing_date is None
    lpr_entry = select_lpr(lpr_entries, lpr_as_of_date)
    lpr_value_pct = parse_optional_decimal(lpr_entry.value) if lpr_entry else None

    primary = None
    if selection.package:
        primary = evaluate_rule(selection.package, confirmed=confirmed, lpr_value_pct=lpr_value_pct)

    references = []
    for reference in applicable_references(
        packages,
        rule_context=rule_context,
        product=product,
        as_of_date=evaluation_date,
    ):
        evaluation = evaluate_rule(reference, confirmed=confirmed, lpr_value_pct=lpr_value_pct)
        # Reference lines never "trigger" a hard rule; crossing one is a risk
        # warning only, and the outcome is labelled distinctly in the report.
        if reference.kind == RuleKind.REFERENCE and evaluation.state == RedlineState.TRIGGERED:
            evaluation.state = RedlineState.RISK_WARNING
            evaluation.reason = "触及风险参考线：请人工评估司法风险，不构成硬规则结论"
        references.append((reference, evaluation))

    if selection.package and primary is not None:
        state = primary.state
    else:
        state = RedlineState.INDETERMINATE

    return RedlineResult(
        state=state,
        selection=selection,
        primary=primary,
        references=references,
        lpr_entry=lpr_entry,
        lpr_provisional=lpr_provisional,
        lpr_as_of_date=lpr_as_of_date,
        evaluation_date=evaluation_date,
    )


# ---------------------------------------------------------------------------
# Params validation (admin input; calculation types are code-defined)
# ---------------------------------------------------------------------------


def validate_calc_params(calc_type: str, params: dict) -> None:
    spec = CALC_TYPES.get(calc_type)
    if spec is None:
        raise ValueError(f"unknown calc_type: {calc_type}")
    for key, constraint in spec["params"].items():
        if key not in params:
            raise ValueError(f"calc_type {calc_type}: missing parameter {key}")
        raw = params[key]
        if constraint["type"] == "decimal":
            value = parse_optional_decimal(raw)
            if value is None or not is_strictly_positive(value):
                raise ValueError(
                    f"calc_type {calc_type}: parameter {key} must be a positive number"
                )
        else:  # integer
            try:
                number = int(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    f"calc_type {calc_type}: parameter {key} must be an integer"
                ) from None
            if "ge" in constraint and number < constraint["ge"]:
                raise ValueError(
                    f"calc_type {calc_type}: parameter {key} must be >= {constraint['ge']}"
                )
    unknown = set(params) - set(spec["params"])
    if unknown:
        raise ValueError(
            f"calc_type {calc_type}: unknown parameter(s): {', '.join(sorted(unknown))}"
        )


def rule_content_hash(payload: dict) -> str:
    return sha256_json(payload)


def rule_content_payload(rule: RulePackage) -> dict:
    """Hashable payload; content hash and lifecycle status are excluded.

    The digest fingerprints the rule's content (scope, params, thresholds,
    legal basis); lifecycle transitions such as approve/retire never rewrite
    the content hash.
    """
    payload = rule_payload(rule)
    payload.pop("content_hash", None)
    payload.pop("status", None)
    return payload


# ---------------------------------------------------------------------------
# Rule payload / snapshots (DB-facing)
# ---------------------------------------------------------------------------


def rule_payload(rule: RulePackage) -> dict:
    return {
        "code": rule.code,
        "name": rule.name,
        "kind": rule.kind,
        "lender_qualification": rule.lender_qualification,
        "rule_context": rule.rule_context,
        "product": rule.product,
        "effective_from": rule.effective_from.isoformat(),
        "effective_until": rule.effective_until.isoformat() if rule.effective_until else None,
        "calc_type": rule.calc_type,
        "params": rule.params,
        "legal_basis": rule.legal_basis,
        "reviewer": rule.reviewer,
        "reviewed_at": rule.reviewed_at.isoformat(),
        "version": rule.version,
        "status": rule.status,
        "demo_only": rule.demo_only,
        "content_hash": rule.content_hash,
    }


def confirmed_resolutions(db: Session, application_id: str) -> dict[str, dict]:
    """Latest confirmed primary-borrower value per field key.

    Redline inputs are the primary borrower's confirmed proposed-loan values;
    resolutions recorded for another subject role never feed the engine. The
    newest resolution wins. Values are the normalized typed-value dicts with a
    ``manual`` flag so the report can visibly mark entries without a source.
    """
    rows = (
        db.query(Resolution)
        .filter_by(application_id=application_id)
        .order_by(Resolution.created_at.asc(), Resolution.id.asc())
        .all()
    )
    latest: dict[str, dict] = {}
    for resolution in rows:
        if resolution.subject_role not in (None, "primary_borrower"):
            continue
        typed = dict(resolution.typed_value)
        typed["manual"] = resolution.resolution_type == "manual"
        latest[resolution.field_key] = typed
    return latest


def confirmed_rule_context(db: Session, application_id: str) -> str | None:
    confirmation = (
        db.query(RuleContextConfirmation).filter_by(application_id=application_id).first()
    )
    return confirmation.context if confirmation else None


def current_lpr_entries(db: Session) -> list[LprEntry]:
    query = (
        db.query(LprEntry)
        .join(LprImport, LprEntry.import_id == LprImport.id)
        .filter(LprImport.status == LprImportStatus.PUBLISHED)
    )
    if _production_mode():
        # Demo-only synthetic LPR never feeds a production formal report.
        query = query.filter(LprImport.demo_only.is_(False))
    return query.all()


def current_lpr_entry(db: Session, as_of_date: date) -> LprEntry | None:
    return select_lpr(current_lpr_entries(db), as_of_date)  # type: ignore[arg-type]


def _production_mode() -> bool:
    from app.config import settings

    return settings.production


def build_run_snapshots(
    db: Session,
    application: Any,
    actor_id: str,
) -> tuple[dict | None, dict, dict]:
    """Freeze rule, inputs, and evaluation steps into immutable snapshots."""
    packages = db.query(RulePackage).all()
    evaluation_date = datetime.now(UTC).date()
    rule_context = confirmed_rule_context(db, application.id)
    confirmed = confirmed_resolutions(db, application.id)
    result = evaluate_redline(
        packages,
        current_lpr_entries(db),
        lender_qualification=application_lender_qualification(db, application),
        rule_context=rule_context,
        product=application.product,
        evaluation_date=evaluation_date,
        proposed_signing_date=application.proposed_signing_date,
        confirmed=confirmed,
    )

    rule = result.selection.package
    rule_snapshot = rule_payload(rule) if rule else None

    lpr_entry = result.lpr_entry
    input_snapshot: dict = {
        "application": {
            "id": application.id,
            "borrower_name": application.borrower_name,
            "borrower_type": application.borrower_type,
            "product": application.product,
            "proposed_signing_date": (
                application.proposed_signing_date.isoformat()
                if application.proposed_signing_date
                else None
            ),
        },
        "lender_qualification": application_lender_qualification(db, application),
        "rule_context": rule_context,
        "evaluation_date": evaluation_date.isoformat(),
        "lpr": {
            "entry_id": lpr_entry.id if hasattr(lpr_entry, "id") else None,
            "effective_date": (lpr_entry.effective_date.isoformat() if lpr_entry else None),
            "value": lpr_entry.value if lpr_entry else None,
            "provisional": result.lpr_provisional,
            "as_of_date": result.lpr_as_of_date.isoformat(),
        },
        "confirmed_inputs": [
            {
                "field_key": key,
                "value": value.get("value"),
                "raw_text": value.get("raw_text"),
                "manual": value.get("manual", False),
            }
            for key, value in sorted(confirmed.items())
        ],
        "actor_id": actor_id,
        "created_at": datetime.now(UTC).isoformat(),
    }

    result_snapshot: dict = {
        "state": result.state,
        "selection": {
            "reason": result.selection.reason,
            "candidates": [
                {
                    "code": package.code,
                    "version": package.version,
                    "name": package.name,
                    "kind": package.kind,
                }
                for package in result.selection.candidates
            ],
        },
        "primary": _evaluation_payload(result.primary),
        "references": [
            {"rule": rule_payload(reference), "evaluation": _evaluation_payload(evaluation)}
            for reference, evaluation in result.references
        ],
    }
    return rule_snapshot, input_snapshot, result_snapshot


def _evaluation_payload(evaluation: RuleEvaluation | None) -> dict | None:
    if evaluation is None:
        return None
    return {
        "state": evaluation.state,
        "steps": evaluation.steps,
        "metrics": evaluation.metrics,
        "missing_inputs": evaluation.missing_inputs,
        "reason": evaluation.reason,
    }


def application_lender_qualification(db: Session, application: Any) -> str:
    # Deployment-fixed lender profile; per-application explicit product and
    # confirmed rule context come from the application itself.
    from app.config import settings

    return settings.lender_qualification


def run_content_hash(
    rule_snapshot: dict | None, input_snapshot: dict, result_snapshot: dict
) -> str:
    return sha256_json(
        {
            "rule": rule_snapshot,
            "input": input_snapshot,
            "result": result_snapshot,
        }
    )


def mark_runs_stale(db: Session, application_id: str, reason: str) -> None:
    runs = (
        db.query(RedlineRun)
        .filter_by(application_id=application_id, status=RunStatus.CURRENT)
        .all()
    )
    for run in runs:
        run.status = RunStatus.STALE
        run.stale_reason = reason
    db.flush()


# ---------------------------------------------------------------------------
# Demo-only synthetic seeds (never used for production formal reports)
# ---------------------------------------------------------------------------


def demo_lpr_entries() -> list[dict]:
    """Synthetic monthly 1Y LPR values covering 2024-01 .. 2027-12.

    Values are invented for synthetic-material demos only; production LPR must
    be imported by an administrator from official announcements.
    """
    entries: list[dict] = []
    cursor = date(2024, 1, 20)
    end = date(2027, 12, 20)
    quarter = 0
    value = Decimal("3.45")
    while cursor <= end:
        entries.append(
            {
                "effective_date": cursor,
                "tenor": "1Y",
                "value": format(value, "f"),
                "publication_date": cursor,
                "source_url": "demo://synthetic-lpr",
            }
        )
        quarter += 1
        if quarter % 3 == 0:
            value = max(value - Decimal("0.05"), Decimal("2.90"))
        cursor = (
            date(cursor.year, cursor.month + 1, 20)
            if cursor.month < 12
            else date(cursor.year + 1, 1, 20)
        )
    return entries


DEMO_RULES: list[dict] = [
    {
        "code": "DEMO-EFFECTIVE-COST-36",
        "name": "演示硬规则：综合年化成本不超过 36%",
        "kind": RuleKind.HARD,
        "rule_context": DEMO_RULE_CONTEXT,
        "product": DEMO_PRODUCT,
        "effective_from": date(2024, 1, 1),
        "effective_until": None,
        "calc_type": "effective_cost_limit",
        "params": {"threshold_pct": "36", "overdue_days": 90},
        "legal_basis": "演示用合成规则，仅用于合成材料验证工程能力；不代表任何机构已批准的监管红线",
        "reviewer": "演示法务",
        "reviewed_at": date(2026, 1, 1),
    },
    {
        "code": "DEMO-LPR-4X",
        "name": "演示参考线：四倍 LPR 司法风险参考线",
        "kind": RuleKind.REFERENCE,
        "rule_context": DEMO_RULE_CONTEXT,
        "product": DEMO_PRODUCT,
        "effective_from": date(2024, 1, 1),
        "effective_until": None,
        "calc_type": "lpr_multiple_limit",
        "params": {"multiplier": "4"},
        "legal_basis": (
            "民间借贷司法保护上限口径（法释〔2020〕27号，演示口径）；"
            "系统不将其认定为全国统一监管硬上限"
        ),
        "reviewer": "演示法务",
        "reviewed_at": date(2026, 1, 1),
    },
    {
        "code": "DEMO-RATE-24",
        "name": "演示参考线：年化 24% 司法风险参考线",
        "kind": RuleKind.REFERENCE,
        "rule_context": DEMO_RULE_CONTEXT,
        "product": DEMO_PRODUCT,
        "effective_from": date(2024, 1, 1),
        "effective_until": None,
        "calc_type": "annual_rate_limit",
        "params": {"threshold_pct": "24"},
        "legal_basis": (
            "最高人民法院 2017 年金融审判意见中 24% 司法调减表述（演示口径）；"
            "系统不将其无条件等同为全国统一监管硬红线"
        ),
        "reviewer": "演示法务",
        "reviewed_at": date(2026, 1, 1),
    },
]


def seed_demo_data(db: Session) -> None:
    """Create and approve demo-only rules and publish demo LPR once (idempotent)."""
    demo_codes = [spec["code"] for spec in DEMO_RULES]
    # Any existing row with a demo code (draft or otherwise) means the demo
    # data was already customised in this database; never collide on version 1.
    existing_rule = db.query(RulePackage).filter(RulePackage.code.in_(demo_codes)).first()
    if existing_rule is None:
        from app.config import settings

        for spec in DEMO_RULES:
            package = RulePackage(
                code=spec["code"],
                name=spec["name"],
                kind=spec["kind"],
                lender_qualification=settings.lender_qualification,
                rule_context=spec["rule_context"],
                product=spec["product"],
                effective_from=spec["effective_from"],
                effective_until=spec["effective_until"],
                calc_type=spec["calc_type"],
                params=spec["params"],
                legal_basis=spec["legal_basis"],
                reviewer=spec["reviewer"],
                reviewed_at=spec["reviewed_at"],
                version=1,
                status=RuleStatus.APPROVED,
                demo_only=True,
                content_hash="",
                approved_at=datetime.now(UTC),
            )
            package.content_hash = rule_content_hash(rule_content_payload(package))
            db.add(package)
    existing_import = (
        db.query(LprImport).filter_by(demo_only=True, status=LprImportStatus.PUBLISHED).first()
    )
    if existing_import is None:
        entries = demo_lpr_entries()
        batch = LprImport(
            filename="demo-synthetic-lpr.csv",
            source_authority="演示数据（合成，非官方公告）",
            status=LprImportStatus.PUBLISHED,
            demo_only=True,
            row_count=len(entries),
            published_at=datetime.now(UTC),
        )
        for entry in entries:
            batch.entries.append(LprEntry(**entry))
        db.add(batch)
    db.commit()


# ---------------------------------------------------------------------------
# Printable HTML report
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_reference_state(state: str) -> str:
    return _esc(REFERENCE_STATE_LABELS.get(state, state))


def _selection_text(result: dict) -> str:
    reason = result.get("selection", {}).get("reason", "")
    if reason == "unique":
        return "唯一主规则包匹配"
    if reason == "no_rule_context":
        return "未确认规则上下文，无法确定适用规则"
    if reason == "no_match":
        return "无匹配的已批准主规则包，无法确定适用规则"
    if reason == "multiple_match":
        return "多个主规则包匹配，无法确定适用规则"
    return "无法确定适用规则"


def render_printable_html(run: RedlineRun, actor_username: str) -> str:
    rule = run.rule_snapshot or {}
    result = run.result_snapshot
    inputs = run.input_snapshot
    state = result["state"]
    state_label = STATE_LABELS.get(state, state)
    state_class = {
        RedlineState.TRIGGERED: "triggered",
        RedlineState.RISK_WARNING: "warning",
        RedlineState.NOT_TRIGGERED: "safe",
    }.get(state, "neutral")
    stale_banner = (
        f'<div class="stale-banner">本报告已失效（{_esc(run.stale_reason or "输入已变化")}），'
        "仅供历史参考，请重新执行评估。</div>"
        if run.status == RunStatus.STALE or run.stale_reason
        else ""
    )

    primary = result.get("primary") or {}
    step_rows = "".join(
        f"<tr><td>{_esc(step.get('label', ''))}</td><td>{_esc(step.get('detail', ''))}</td></tr>"
        for step in primary.get("steps", [])
    )
    missing = primary.get("missing_inputs", [])
    missing_rows = "".join(
        f"<li>{_esc(RATE_LABELS.get(key, key))}（{_esc(key)}）</li>" for key in missing
    )
    confirmed_rows = "".join(
        f"<li>{_esc(RATE_LABELS.get(item['field_key'], item['field_key']))}"
        f"（{_esc(item['field_key'])}）：{_esc(item.get('value', ''))}"
        f"{'（人工录入，无材料出处）' if item.get('manual') else ''}</li>"
        for item in inputs.get("confirmed_inputs", [])
    )
    reference_rows = "".join(
        f"""
        <tr>
          <td>{_esc(item["rule"]["code"])}（v{_esc(item["rule"]["version"])}）</td>
          <td>{_esc(item["rule"]["name"])}</td>
          <td class="ref-state">{_render_reference_state(item["evaluation"]["state"])}</td>
          <td>{_esc(item["evaluation"].get("reason", ""))}</td>
        </tr>"""
        for item in result.get("references", [])
    )
    lpr = inputs.get("lpr", {})
    lpr_text = (
        f"{_esc(lpr['value'])}%（{_esc(lpr['effective_date'])} 生效）"
        + ("，按评估日期预估（无拟签约日期）" if lpr.get("provisional") else "，按拟签约日期选取")
        if lpr.get("entry_id") is not None or lpr.get("effective_date")
        else "未取到适用 LPR（资料不足）"
    )
    candidates = result.get("selection", {}).get("candidates", [])
    candidate_rows = "".join(
        f"<li>{_esc(item['code'])} v{_esc(item['version'])}（{_esc(item['name'])}）</li>"
        for item in candidates
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>红线评估正式报告 - {_esc(inputs["application"]["borrower_name"])}</title>
<style>
  body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif; margin: 24px; color: #222; }}
  h1 {{ font-size: 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; font-size: 13px; }}
  th {{ background: #f2f2f2; }}
  .triggered {{ color: #c0392b; font-weight: bold; }}
  .warning {{ color: #b88230; font-weight: bold; }}
  .safe {{ color: #2e7d32; font-weight: bold; }}
  .neutral {{ font-weight: bold; }}
  .disclaimer {{ border: 1px solid #d9b38c; background: #fdf3e7; padding: 10px; }}
  .stale-banner {{ border: 1px solid #c0392b; background: #fdecea; padding: 10px; }}
  .stale-banner {{ font-weight: bold; color: #c0392b; }}
  .ref-state {{ font-weight: bold; }}
  .muted {{ color: #777; font-size: 12px; }}
</style>
</head>
<body>
  <h1>红线评估正式报告</h1>
  <div class="disclaimer">
    本报告仅供审批辅助，需人工复核；系统不认定、也不暗示本笔贷款合规或获批。硬规则触发不等同于审批决定。
  </div>
  {stale_banner}
  <p class="muted">
    生成时间：{_esc(run.created_at.isoformat())} · 操作者：{_esc(actor_username)} ·
    报告编号：{_esc(run.id)} · 内容哈希：{_esc(run.content_hash)}
  </p>
  <h2>评估结果</h2>
  <p class="{state_class}">总体状态：{_esc(state_label)}</p>
  <p>{_esc(primary.get("reason", _selection_text(result)))}</p>
  <h2>主规则包</h2>
  {
        f'''
  <p>
    规则：{_esc(rule["name"])}（{_esc(rule["code"])} · v{_esc(rule["version"])}）
    · 类型：{_esc(CALC_LABELS.get(rule["calc_type"], rule["calc_type"]))}
    · 规则上下文：{_esc(rule["rule_context"])} · 产品：{_esc(rule["product"])}
    · 生效区间：{_esc(rule["effective_from"])} 至 {_esc(rule.get("effective_until") or "长期")}
    · 演示规则：{"是" if rule.get("demo_only") else "否"}
  </p>
  <p>法律依据：{_esc(rule["legal_basis"])}</p>
  <p class="muted">法务复核：{_esc(rule["reviewer"])} · {_esc(rule["reviewed_at"])}</p>
  <h3>计算步骤</h3>
  <table>
    <thead><tr><th>步骤</th><th>计算过程</th></tr></thead>
    <tbody>{step_rows or '<tr><td colspan="2">无可执行计算</td></tr>'}</tbody>
  </table>'''
        if rule
        else f"<p>{_esc(_selection_text(result))}。候选：{candidate_rows or '无'}</p>"
    }
  <h2>LPR 时点</h2>
  <p>{lpr_text}</p>
  <h2>关键输入（确认值）</h2>
  <ul>{confirmed_rows or "<li>无已确认关键输入</li>"}</ul>
  <h2>缺失或未确认的关键输入</h2>
  <ul>{missing_rows or "<li>无</li>"}</ul>
  <h2>司法风险参考线（仅提示，不构成硬规则结论）</h2>
  <table>
    <thead><tr><th>参考线</th><th>名称</th><th>状态</th><th>说明</th></tr></thead>
    <tbody>{reference_rows or '<tr><td colspan="4">无适用参考线</td></tr>'}</tbody>
  </table>
  <p class="muted">
    输入快照与结果快照的完整 JSON 可通过 API 获取；
    关键输入、规则上下文或适用规则变化会使本报告失效。
  </p>
</body>
</html>"""
