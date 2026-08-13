"""Golden extraction measurement: recall/precision of local deterministic rules.

Runs the local rule extractor over synthetic/public golden materials and
reports per-field and overall recall/precision. Exits non-zero when the agreed
thresholds are not met (core-field recall >= 0.9, precision >= 0.95).

Usage:
    cd backend && uv run python scripts/evaluate_extraction.py
"""

import sys
from dataclasses import asdict
from pathlib import Path

from app.extraction import extract_from_output
from app.models import DocumentBlock, DocumentOutput, DocumentPage, TableCell
from app.parsing import ParsedOutput
from app.structured import parse_structured

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# Expected normalized values per field for each golden material.
GOLDEN = [
    (
        "golden_corporate.md",
        {
            "corporate_name": {"示例企业有限公司"},
            "uscc": {"91330100MA27XW1234"},
            "legal_representative": {"王小明"},
            "registered_capital": {"50000000"},
            "loan_amount": {"8000000"},
            "loan_term": {"24"},
            "loan_purpose": {"补充流动资金"},
            "proposed_signing_date": {"2026-08-07"},
            "interest_rate": {"3.85"},
            "repayment_method": {"等额本息"},
            "interest_method": {"按月计息"},
            "loan_fees": {"评估费、公证费"},
            "shareholder": {"陈明"},
            "credit_query_count": {"3"},
            "credit_report_date": {"2026-07-15"},
            "credit_balance": {"2000000"},
            "guarantor": {"李四"},
            "guarantee_method": {"连带责任保证"},
            "collateral_value": {"15000000"},
            "collateral_type": {"杭州市西湖区某房产"},
            "collateral_certificate": {"杭房权证西字第123456号"},
            "financial_statement_item": {"营业收入", "营业成本", "净利润"},
            "transaction_item": {"杭州某贸易公司", "某供应商"},
        },
    ),
    (
        "golden_individual.md",
        {
            "personal_name": {"张伟"},
            "id_card": {"330102199001011234"},
            "loan_amount": {"300000"},
            "loan_term": {"36"},
            "interest_rate": {"4.2"},
        },
    ),
]

MIN_RECALL = 0.9
MIN_PRECISION = 0.95


def parsed_to_orm(parsed: ParsedOutput) -> DocumentOutput:
    output = DocumentOutput(
        id="golden",
        document_id="golden-document",
        version=1,
        status=parsed.status,
        parser_version=parsed.parser_version,
        model_version=parsed.model_version,
    )
    for parsed_page in parsed.pages:
        page = DocumentPage(
            number=parsed_page.number,
            width=parsed_page.width,
            height=parsed_page.height,
            status=parsed_page.status,
            error_code=parsed_page.error_code,
        )
        for parsed_block in parsed_page.blocks:
            block = DocumentBlock(
                order=parsed_block.order,
                kind=parsed_block.kind,
                text=parsed_block.text,
                x0=parsed_block.bbox[0] if parsed_block.bbox else None,
                y0=parsed_block.bbox[1] if parsed_block.bbox else None,
                x1=parsed_block.bbox[2] if parsed_block.bbox else None,
                y1=parsed_block.bbox[3] if parsed_block.bbox else None,
                extraction_method=parsed_block.extraction_method,
                confidence=parsed_block.confidence,
                locator=asdict(parsed_block.locator) if parsed_block.locator else None,
            )
            for parsed_cell in parsed_block.cells:
                block.cells.append(
                    TableCell(
                        row_index=parsed_cell.row,
                        column_index=parsed_cell.column,
                        text=parsed_cell.text,
                        x0=parsed_cell.bbox[0] if parsed_cell.bbox else None,
                        y0=parsed_cell.bbox[1] if parsed_cell.bbox else None,
                        x1=parsed_cell.bbox[2] if parsed_cell.bbox else None,
                        y1=parsed_cell.bbox[3] if parsed_cell.bbox else None,
                        locator=asdict(parsed_cell.locator) if parsed_cell.locator else None,
                    )
                )
            page.blocks.append(block)
        output.pages.append(page)
    return output


def extract(fixture: str) -> dict[str, set[str]]:
    parsed = parse_structured(fixture, (FIXTURES / fixture).open("rb"))
    seeds = extract_from_output(parsed_to_orm(parsed))
    found: dict[str, set[str]] = {}
    for seed in seeds:
        found.setdefault(seed.field_key, set()).add(seed.typed_value.value)
    return found


def evaluate() -> tuple[bool, list[tuple[str, float, float]]]:
    rows: list[tuple[str, float, float]] = []
    ok = True
    for fixture, expected in GOLDEN:
        found = extract(fixture)
        for field_key, expected_values in expected.items():
            extracted = found.get(field_key, set())
            matched = extracted & expected_values
            recall = len(matched) / len(expected_values)
            precision = len(matched) / len(extracted) if extracted else 0.0
            rows.append((f"{fixture}:{field_key}", recall, precision))
            if recall < MIN_RECALL or precision < MIN_PRECISION:
                ok = False
    return ok, rows


def main() -> None:
    ok, rows = evaluate()
    print(f"{'field':<45}{'recall':>8}{'precision':>10}")
    print("-" * 63)
    for label, recall, precision in rows:
        below = recall < MIN_RECALL or precision < MIN_PRECISION
        flag = "  <-- below threshold" if below else ""
        print(f"{label:<45}{recall:>8.2f}{precision:>10.2f}{flag}")
    print("-" * 63)
    print(f"thresholds: recall >= {MIN_RECALL}, precision >= {MIN_PRECISION}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
