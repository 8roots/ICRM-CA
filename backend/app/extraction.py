"""Local deterministic extraction of field candidates from parsed materials.

Rule-based extraction runs first and never calls the cloud. It produces
immutable :class:`CandidateFact` seeds carrying the raw text, a normalized
typed value, confidence, and source references back to the parsed output.
Tables keep row-level detail: bank transaction rows and financial statement
items become one candidate per row.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.fields import FIELDS, SubjectRole, ValueType
from app.models import DocumentBlock, DocumentOutput, DocumentPage, TableCell
from app.redaction import IDENTIFIER_PATTERNS
from app.values import (
    TypedValue,
    normalize_amount,
    normalize_date,
    normalize_field,
    normalize_integer,
    normalize_rate,
    normalize_text,
)

LOCAL_EXTRACTOR_VERSION = "icrm-local-rules-1"

AMOUNT_VALUE = r"[0-9][0-9,]*(?:\.[0-9]+)?\s*(?:亿元|万元|千元|元|亿|万)?"
DATE_VALUE = r"(?:20\d{2}|19\d{2})[-年/](?:0?[1-9]|1[0-2])[-月/](?:[12][0-9]|3[01]|0?[1-9])日?"


def _loan_term_normalize(text: str) -> TypedValue | None:
    return normalize_integer(text, default_unit="月")


@dataclass
class CandidateSeed:
    field_key: str
    raw_text: str
    typed_value: TypedValue
    confidence: float
    source_refs: list[dict]
    subject_role: SubjectRole | None = None
    identifiers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rule:
    field_key: str
    pattern: re.Pattern
    confidence: float
    subject: SubjectRole | None = None
    normalize: Callable[[str], TypedValue | None] | None = None
    raw_group: int = 1


PARAGRAPH_RULES: tuple[Rule, ...] = (
    Rule(
        "corporate_name",
        re.compile(r"(?:公司名称|企业名称|借款人名称)\s*[:：]\s*(.+)$", re.MULTILINE),
        0.92,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "corporate_name",
        re.compile(
            r"^([\u4e00-\u9fa5A-Za-z0-9（）()]{2,40}(?:有限公司|有限责任公司|股份有限公司|合伙企业))$",
            re.MULTILINE,
        ),
        0.8,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "uscc",
        re.compile(
            r"(?:统一社会信用代码|信用代码|社会信用代码)\s*[:：]?\s*([0-9A-HJ-NPQRTUWXY]{18})"
        ),
        0.97,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "legal_representative",
        re.compile(r"法定代表人\s*[:：]\s*(\S{2,20})"),
        0.93,
        SubjectRole.LEGAL_REPRESENTATIVE,
    ),
    Rule(
        "registered_capital",
        re.compile(r"注册资本\s*[:：]?\s*(" + AMOUNT_VALUE + r")"),
        0.9,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "personal_name",
        re.compile(r"(?:借款人|申请人|姓名|客户姓名)\s*[:：]\s*(\S{2,20})"),
        0.88,
    ),
    Rule(
        "id_card",
        re.compile(r"(?:身份证号|证件号码|身份证号码)\s*[:：]?\s*(\d{17}[\dXx])"),
        0.95,
    ),
    Rule(
        "loan_amount",
        re.compile(
            r"(?:拟贷款金额|贷款金额|借款金额|融资金额|申请金额)\s*[:：]?\s*(" + AMOUNT_VALUE + r")"
        ),
        0.92,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "loan_term",
        re.compile(r"(?:贷款期限|借款期限)\s*[:：]?\s*(\d+\s*(?:年|个月|月))"),
        0.9,
        SubjectRole.PRIMARY_BORROWER,
        _loan_term_normalize,
    ),
    Rule(
        "loan_purpose",
        re.compile(r"(?:贷款用途|借款用途|资金用途)\s*[:：]\s*(.+)$", re.MULTILINE),
        0.85,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "proposed_signing_date",
        re.compile(r"拟签约日期\s*[:：]?\s*(" + DATE_VALUE + r")"),
        0.9,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "interest_rate",
        re.compile(
            r"((?:年利率|月利率|日利率|名义利率|执行利率|贷款利率|利率)\s*[:：]?\s*"
            r"([0-9]+(?:\.[0-9]+)?\s*%))"
        ),
        0.92,
        SubjectRole.PRIMARY_BORROWER,
        raw_group=2,
    ),
    Rule(
        "repayment_method",
        re.compile(
            r"还款方式\s*[:：]\s*(等额本息|等额本金|先息后本|一次性还本付息|按月付息到期还本|利随本清)"
        ),
        0.92,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "interest_method",
        re.compile(
            r"计息方式\s*[:：]\s*(按月计息|按季计息|按年计息|到期一次还本付息|利随本清|按月付息)"
        ),
        0.88,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "loan_fees",
        re.compile(r"(?:必要费用|各项费用|贷款费用)\s*[:：]\s*(.+)$", re.MULTILINE),
        0.8,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "marital_status",
        re.compile(r"婚姻状况\s*[:：]\s*(已婚|未婚|离异|丧偶)"),
        0.92,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "shareholder",
        re.compile(r"股东\s*[:：]\s*(\S{2,20})"),
        0.85,
        SubjectRole.SHAREHOLDER,
    ),
    Rule(
        "collateral_certificate",
        re.compile(r"(?:权证号|不动产权证号|产权证号)\s*[:：]?\s*(\S{4,40})"),
        0.85,
    ),
    Rule(
        "credit_query_count",
        re.compile(r"(?:查询次数|查询记录)\s*[:：]?\s*(\d+)\s*次?"),
        0.8,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "credit_report_date",
        re.compile(r"报告日期\s*[:：]?\s*(" + DATE_VALUE + r")"),
        0.85,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "credit_line",
        re.compile(r"(?:授信额度|授信总额|授信余额)\s*[:：]?\s*(" + AMOUNT_VALUE + r")"),
        0.85,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "credit_balance",
        re.compile(r"(?:负债余额|贷款余额|未结清余额)\s*[:：]?\s*(" + AMOUNT_VALUE + r")"),
        0.85,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "overdue_amount",
        re.compile(r"(?:逾期金额|当前逾期)\s*[:：]?\s*(" + AMOUNT_VALUE + r")"),
        0.8,
        SubjectRole.PRIMARY_BORROWER,
    ),
    Rule(
        "guarantor",
        re.compile(r"(?:保证人|担保人)\s*[:：]\s*(\S{2,20})"),
        0.9,
        SubjectRole.GUARANTOR,
    ),
    Rule(
        "guarantee_method",
        re.compile(r"保证方式\s*[:：]\s*(连带责任保证|一般保证|连带责任|一般责任)"),
        0.9,
    ),
    Rule(
        "collateral_type",
        re.compile(r"(?:抵押物|抵押财产|担保物)\s*[:：]\s*(.+)$", re.MULTILINE),
        0.8,
    ),
    Rule(
        "collateral_value",
        re.compile(r"(?:评估价值|评估值|评估价)\s*[:：]?\s*(" + AMOUNT_VALUE + r")"),
        0.85,
    ),
)


KEY_VALUE_LABELS: dict[str, tuple[str, Callable[[str], TypedValue | None], float]] = {
    "统一社会信用代码": ("uscc", normalize_text, 0.95),
    "信用代码": ("uscc", normalize_text, 0.9),
    "身份证号": ("id_card", normalize_text, 0.93),
    "证件号码": ("id_card", normalize_text, 0.93),
    "身份证号码": ("id_card", normalize_text, 0.93),
    "法定代表人": ("legal_representative", normalize_text, 0.9),
    "注册资本": ("registered_capital", lambda t: normalize_amount(t), 0.88),
    "公司名称": ("corporate_name", normalize_text, 0.9),
    "企业名称": ("corporate_name", normalize_text, 0.9),
    "姓名": ("personal_name", normalize_text, 0.85),
    "借款人": ("personal_name", normalize_text, 0.85),
    "贷款金额": ("loan_amount", lambda t: normalize_amount(t), 0.9),
    "借款金额": ("loan_amount", lambda t: normalize_amount(t), 0.9),
    "申请金额": ("loan_amount", lambda t: normalize_amount(t), 0.9),
    "年利率": ("interest_rate", lambda t: normalize_rate(f"年利率{t}"), 0.9),
    "月利率": ("interest_rate", lambda t: normalize_rate(f"月利率{t}"), 0.9),
    "名义利率": ("interest_rate", lambda t: normalize_rate(f"年利率{t}"), 0.9),
    "执行利率": ("interest_rate", lambda t: normalize_rate(f"年利率{t}"), 0.9),
    "贷款利率": ("interest_rate", lambda t: normalize_rate(f"年利率{t}"), 0.9),
    "贷款期限": ("loan_term", _loan_term_normalize, 0.88),
    "借款期限": ("loan_term", _loan_term_normalize, 0.88),
    "还款方式": ("repayment_method", normalize_text, 0.9),
    "贷款用途": ("loan_purpose", normalize_text, 0.85),
    "借款用途": ("loan_purpose", normalize_text, 0.85),
    "拟签约日期": ("proposed_signing_date", normalize_date, 0.9),
    "报告日期": ("credit_report_date", normalize_date, 0.85),
    "评估价值": ("collateral_value", lambda t: normalize_amount(t), 0.85),
    "评估值": ("collateral_value", lambda t: normalize_amount(t), 0.85),
    "抵押物": ("collateral_type", normalize_text, 0.8),
    "权证号": ("collateral_certificate", normalize_text, 0.85),
    "不动产权证号": ("collateral_certificate", normalize_text, 0.85),
    "计息方式": ("interest_method", normalize_text, 0.88),
    "必要费用": ("loan_fees", normalize_text, 0.8),
    "各项费用": ("loan_fees", normalize_text, 0.8),
    "贷款费用": ("loan_fees", normalize_text, 0.8),
    "婚姻状况": ("marital_status", normalize_text, 0.92),
    "股东": ("shareholder", normalize_text, 0.85),
    "查询次数": ("credit_query_count", lambda t: normalize_integer(t), 0.8),
    "保证人": ("guarantor", normalize_text, 0.88),
    "担保人": ("guarantor", normalize_text, 0.88),
    "保证方式": ("guarantee_method", normalize_text, 0.88),
    "授信额度": ("credit_line", lambda t: normalize_amount(t), 0.85),
    "授信总额": ("credit_line", lambda t: normalize_amount(t), 0.85),
    "负债余额": ("credit_balance", lambda t: normalize_amount(t), 0.85),
    "贷款余额": ("credit_balance", lambda t: normalize_amount(t), 0.85),
    "逾期金额": ("overdue_amount", lambda t: normalize_amount(t), 0.8),
    "当前逾期": ("overdue_amount", lambda t: normalize_amount(t), 0.8),
}

LABEL_RE = re.compile(
    r"^\s*(统一社会信用代码|信用代码|身份证号|证件号码|身份证号码|法定代表人|注册资本|公司名称|企业名称"
    r"|姓名|借款人|贷款金额|借款金额|申请金额|年利率|月利率|名义利率|执行利率|贷款利率|贷款期限|借款期限"
    r"|还款方式|计息方式|贷款用途|借款用途|拟签约日期|报告日期|评估价值|评估值|抵押物|权证号|不动产权证号"
    r"|保证人|担保人|保证方式|授信额度|授信总额|负债余额|贷款余额|逾期金额|当前逾期|查询次数|婚姻状况|股东"
    r"|必要费用|各项费用|贷款费用)"
    r"\s*[:：]?\s*$"
)


SUBJECT_KEYWORDS = (
    ("法定代表人", SubjectRole.LEGAL_REPRESENTATIVE),
    ("法人代表", SubjectRole.LEGAL_REPRESENTATIVE),
    ("股东", SubjectRole.SHAREHOLDER),
    ("配偶", SubjectRole.SPOUSE),
    ("妻子", SubjectRole.SPOUSE),
    ("丈夫", SubjectRole.SPOUSE),
    ("保证人", SubjectRole.GUARANTOR),
    ("担保人", SubjectRole.GUARANTOR),
    ("抵押人", SubjectRole.COLLATERAL_OWNER),
    ("权属人", SubjectRole.COLLATERAL_OWNER),
)

FINANCIAL_HEADERS = ("科目", "项目", "项目名称")
FINANCIAL_VALUE_HEADERS = ("金额", "本期金额", "期末余额", "年初余额", "本年累计", "上年同期")
TRANSACTION_HEADERS = ("交易日期", "发生日期", "日期")
TRANSACTION_COUNTERPARTY = ("交易对手", "对方户名", "对方名称", "摘要", "用途", "备注")


def _subject_for(context: str, default: SubjectRole | None) -> SubjectRole | None:
    for keyword, role in SUBJECT_KEYWORDS:
        if keyword in context:
            return role
    return default


def _identifiers_in(text: str) -> list[str]:
    found: list[str] = []
    for pattern in IDENTIFIER_PATTERNS:
        found.extend(pattern.findall(text))
    return found


def _source_ref(
    output: DocumentOutput, page: DocumentPage, block: DocumentBlock, cell: TableCell | None = None
) -> dict:
    ref: dict = {
        "document_id": output.document_id,
        "output_id": output.id,
        "output_version": output.version,
        "page_number": page.number,
        "block_id": block.id,
        "block_order": block.order,
        "locator": block.locator,
    }
    if block.x0 is not None:
        ref["bbox"] = [block.x0, block.y0, block.x1, block.y1]
    if cell is not None:
        ref["cell_id"] = cell.id
        ref["cell_locator"] = cell.locator
        if cell.x0 is not None:
            ref["cell_bbox"] = [cell.x0, cell.y0, cell.x1, cell.y1]
    return ref


def _normalize_rule(rule: Rule, text: str) -> TypedValue | None:
    if rule.normalize is not None:
        return rule.normalize(text)
    field = FIELDS.get(rule.field_key)
    if field is None:
        return normalize_text(text)
    return normalize_field(field, text)


def _apply_rule(rule: Rule, text: str, ref: dict) -> CandidateSeed | None:
    match = rule.pattern.search(text)
    if not match:
        return None
    raw = match.group(rule.raw_group).strip()
    typed = _normalize_rule(rule, match.group(1))
    if typed is None:
        return None
    line_context = text[: match.start()].rsplit("\n", 1)[-1]
    return CandidateSeed(
        field_key=rule.field_key,
        raw_text=raw,
        typed_value=typed,
        confidence=rule.confidence,
        subject_role=(
            _subject_for(
                line_context,
                FIELDS.get(rule.field_key).default_subject
                if FIELDS.get(rule.field_key) is not None
                else None,
            )
            if rule.subject is None
            else rule.subject
        ),
        source_refs=[ref],
        identifiers=_identifiers_in(raw),
    )


def _header_matches(header: str, names: tuple[str, ...]) -> bool:
    return any(header == name or header.startswith(name) for name in names)


def _scan_table_rows(
    output: DocumentOutput,
    page: DocumentPage,
    block: DocumentBlock,
    cells: list[TableCell],
) -> list[CandidateSeed]:
    """Key-value rows plus financial/transaction row-level candidates."""
    seeds: list[CandidateSeed] = []
    grid: dict[int, dict[int, TableCell]] = {}
    for cell in cells:
        grid.setdefault(cell.row_index, {})[cell.column_index] = cell
    rows = sorted(grid)
    if not rows:
        return seeds
    header_cells = grid[rows[0]]
    header_map = {
        cell.text.strip(): column for column, cell in header_cells.items() if cell.text.strip()
    }

    def cell_text(row_index: int, column_index: int) -> str:
        cell = grid.get(row_index, {}).get(column_index)
        return cell.text.strip() if cell else ""

    is_financial = any(_header_matches(h, FINANCIAL_HEADERS) for h in header_map) and any(
        _header_matches(h, FINANCIAL_VALUE_HEADERS) for h in header_map
    )
    is_transaction = (
        any(_header_matches(h, TRANSACTION_HEADERS) for h in header_map)
        and any(_header_matches(h, TRANSACTION_COUNTERPARTY) for h in header_map)
        and any(_header_matches(h, FINANCIAL_VALUE_HEADERS) for h in header_map)
    )

    for row_index in rows:
        row_cells = sorted(grid[row_index].items())
        ref = _source_ref(output, page, block)
        row_text = " ".join(cell_text(row_index, column) for column, _ in row_cells)

        first_text = row_cells[0][1].text.strip() if row_cells else ""
        label_match = LABEL_RE.match(first_text)
        if label_match and len(row_cells) > 1:
            label = label_match.group(1)
            field_key, normalize, confidence = KEY_VALUE_LABELS[label]
            value_cell = next((cell for _, cell in row_cells[1:] if cell.text.strip()), None)
            if value_cell:
                typed = normalize(value_cell.text.strip())
                if typed:
                    typed.raw_text = value_cell.text.strip()
                    field = FIELDS.get(field_key)
                    seeds.append(
                        CandidateSeed(
                            field_key=field_key,
                            raw_text=value_cell.text.strip(),
                            typed_value=typed,
                            confidence=confidence,
                            subject_role=_subject_for(
                                row_text, field.default_subject if field else None
                            ),
                            source_refs=[{**ref, "cell_id": value_cell.id}],
                            identifiers=_identifiers_in(value_cell.text.strip()),
                        )
                    )

        if row_index == rows[0]:
            continue
        if is_financial:
            subject_col = next(
                (
                    col
                    for header, col in header_map.items()
                    if _header_matches(header, FINANCIAL_HEADERS)
                ),
                None,
            )
            value_col = next(
                (
                    col
                    for header, col in header_map.items()
                    if _header_matches(header, FINANCIAL_VALUE_HEADERS)
                ),
                None,
            )
            if subject_col is not None and value_col is not None:
                subject_text = cell_text(row_index, subject_col)
                amount_text = cell_text(row_index, value_col)
                if subject_text:
                    seeds.append(
                        CandidateSeed(
                            field_key="financial_statement_item",
                            raw_text=f"{subject_text} {amount_text}".strip(),
                            typed_value=TypedValue(
                                type=ValueType.ROW,
                                value=subject_text,
                                raw_text=f"{subject_text} {amount_text}".strip(),
                                columns={
                                    header: cell_text(row_index, column)
                                    for header, column in header_map.items()
                                },
                            ),
                            confidence=0.75,
                            subject_role=_subject_for(row_text, SubjectRole.PRIMARY_BORROWER),
                            source_refs=[{**ref, "cell_id": grid[row_index][subject_col].id}],
                        )
                    )
        elif is_transaction:
            date_col = next(
                (
                    col
                    for header, col in header_map.items()
                    if _header_matches(header, TRANSACTION_HEADERS)
                ),
                None,
            )
            counterparty_col = next(
                (
                    col
                    for header, col in header_map.items()
                    if _header_matches(header, TRANSACTION_COUNTERPARTY)
                ),
                None,
            )
            amount_col = next(
                (
                    col
                    for header, col in header_map.items()
                    if _header_matches(header, FINANCIAL_VALUE_HEADERS)
                ),
                None,
            )
            if date_col is not None and amount_col is not None:
                date_text = cell_text(row_index, date_col)
                amount_text = cell_text(row_index, amount_col)
                counterparty_text = (
                    cell_text(row_index, counterparty_col) if counterparty_col is not None else ""
                )
                primary = counterparty_text or date_text
                date_value = normalize_date(date_text)
                amount_value = normalize_amount(amount_text)
                seeds.append(
                    CandidateSeed(
                        field_key="transaction_item",
                        raw_text=row_text,
                        typed_value=TypedValue(
                            type=ValueType.ROW,
                            value=primary,
                            raw_text=row_text,
                            currency=amount_value.currency if amount_value else None,
                            unit=amount_value.unit if amount_value else None,
                            date=date_value.date if date_value else None,
                            columns={
                                header: cell_text(row_index, column)
                                for header, column in header_map.items()
                            },
                        ),
                        confidence=0.75,
                        subject_role=_subject_for(row_text, SubjectRole.PRIMARY_BORROWER),
                        source_refs=[{**ref, "cell_id": grid[row_index][date_col].id}],
                    )
                )
    return seeds


def extract_from_output(output: DocumentOutput) -> list[CandidateSeed]:
    """Run the local deterministic rules over one parsed output."""
    seeds: list[CandidateSeed] = []
    for page in output.pages:
        for block in page.blocks:
            ref = _source_ref(output, page, block)
            if block.kind != "table":
                for rule in PARAGRAPH_RULES:
                    seed = _apply_rule(rule, block.text, ref)
                    if seed:
                        seeds.append(seed)
            else:
                for rule in PARAGRAPH_RULES:
                    seed = _apply_rule(rule, block.text, ref)
                    if seed:
                        seeds.append(seed)
                for cell in block.cells:
                    cell_ref = {**ref, "cell_id": cell.id}
                    for rule in PARAGRAPH_RULES:
                        seed = _apply_rule(rule, cell.text, cell_ref)
                        if seed and seed.field_key in {"uscc", "id_card", "legal_representative"}:
                            seeds.append(seed)
                seeds.extend(_scan_table_rows(output, page, block, block.cells))
    return seeds
