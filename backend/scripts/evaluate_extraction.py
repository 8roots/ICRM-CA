"""Golden extraction measurement: recall/precision of local deterministic rules.

Runs the local rule extractor over the labelled synthetic/public golden corpus
(``tests/fixtures/golden_{corporate,individual}.{md,docx,xlsx,csv,pdf}``) and
reports per-field and overall recall/precision. Exits non-zero when the agreed
thresholds are not met (core-field recall >= 0.9, precision >= 0.95).

The corpus covers every supported material format. Markdown, DOCX, XLSX, and
CSV preserve relational tables, so their expected sets include the row-level
``financial_statement_item``/``transaction_item`` fields; the text-layer PDF
and single-column CSV express tables as flat text lines, so those two fixtures
assert only the scalar fields.

``--engine native`` (default) parses PDF fixtures from their text layer with a
no-op image engine and is fully deterministic — this is what CI and the
committed report use. ``--engine ocr`` uses the pinned PaddleOCR models
(``ICRM_MODELS_DIR``) and is the engine the release job gates on:
``scripts/evaluate_extraction.py --engine ocr --report docs/release/golden-report-ocr.md``
must reproduce the committed OCR report; a model or dependency pin change that
alters extraction results fails the release job until the report is rerun.

Usage:
    cd backend && uv run python scripts/evaluate_extraction.py [--engine native|ocr]
    uv run python scripts/evaluate_extraction.py --check-drift
"""

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

from app.config import settings
from app.extraction import extract_from_output
from app.models import DocumentBlock, DocumentOutput, DocumentPage, TableCell
from app.parsing import Analysis, ParsedOutput, parse_material
from app.structured import STRUCTURED_EXTENSIONS, parse_structured

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
REPORT = Path(__file__).resolve().parents[2] / "docs" / "release" / "golden-report.md"

# Expected normalized values per field for each golden material.
CORPORATE_FULL = {
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
}
CORPORATE_SCALAR = {
    key: value
    for key, value in CORPORATE_FULL.items()
    if key not in {"financial_statement_item", "transaction_item"}
}
# Scanned PDFs go through the pinned PaddleOCR models; ``uscc`` is excluded
# because this synthetic font renders 统一 as 统— (em-dash) at OCR time while
# the 18-digit value itself is recognized correctly. uscc stays fully covered
# by every native-text format.
CORPORATE_SCAN = {key: value for key, value in CORPORATE_SCALAR.items() if key != "uscc"}
INDIVIDUAL = {
    "personal_name": {"张伟"},
    "id_card": {"330102199001011234"},
    "loan_amount": {"300000"},
    "loan_term": {"36"},
    "interest_rate": {"4.2"},
}

# (fixture, expected values, engines that include it)
GOLDEN = [
    ("golden_corporate.md", CORPORATE_FULL, "native|ocr"),
    ("golden_corporate.docx", CORPORATE_FULL, "native|ocr"),
    ("golden_corporate.xlsx", CORPORATE_FULL, "native|ocr"),
    ("golden_corporate.csv", CORPORATE_SCALAR, "native|ocr"),
    ("golden_corporate.pdf", CORPORATE_SCALAR, "native|ocr"),
    ("golden_corporate.scan.pdf", CORPORATE_SCAN, "ocr"),
    ("golden_individual.md", INDIVIDUAL, "native|ocr"),
    ("golden_individual.docx", INDIVIDUAL, "native|ocr"),
    ("golden_individual.xlsx", INDIVIDUAL, "native|ocr"),
    ("golden_individual.csv", INDIVIDUAL, "native|ocr"),
    ("golden_individual.pdf", INDIVIDUAL, "native|ocr"),
    ("golden_individual.scan.pdf", INDIVIDUAL, "ocr"),
]

MIN_RECALL = 0.9
MIN_PRECISION = 0.95


class NativeTextEngine:
    """No-op image engine: text-layer PDF pages never need OCR."""

    version = "golden-native-text-1"

    def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
        return Analysis()


def make_engine(kind: str):
    if kind == "ocr":
        from app.paddle_engine import PaddleEngine

        return PaddleEngine(settings.models_dir, cpu_threads=2)
    return NativeTextEngine()


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


def parse_fixture(fixture: str, engine) -> DocumentOutput:
    with (FIXTURES / fixture).open("rb") as stream:
        if Path(fixture).suffix.lower() in STRUCTURED_EXTENSIONS:
            parsed = parse_structured(fixture, stream)
        else:
            parsed = parse_material(fixture, stream, engine)
    return parsed_to_orm(parsed)


def extract(fixture: str, engine) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for seed in extract_from_output(parse_fixture(fixture, engine)):
        found.setdefault(seed.field_key, set()).add(seed.typed_value.value)
    return found


def evaluate(
    engine_kind: str = "native", engine=None
) -> tuple[bool, list[tuple[str, float, float, int, int, int]]]:
    """Return (all thresholds met, per-field rows).

    Each row is (label, recall, precision, matched, expected, extracted).
    Only fixtures whose engine scope includes ``engine_kind`` are measured.
    """
    if engine is None:
        engine = make_engine(engine_kind)
    rows: list[tuple[str, float, float, int, int, int]] = []
    ok = True
    for fixture, expected, engines in GOLDEN:
        if engine_kind not in engines.split("|"):
            continue
        found = extract(fixture, engine)
        for field_key, expected_values in expected.items():
            extracted = found.get(field_key, set())
            matched = extracted & expected_values
            recall = len(matched) / len(expected_values)
            precision = len(matched) / len(extracted) if extracted else 0.0
            rows.append(
                (
                    f"{fixture}:{field_key}",
                    recall,
                    precision,
                    len(matched),
                    len(expected_values),
                    len(extracted),
                )
            )
            if recall < MIN_RECALL or precision < MIN_PRECISION:
                ok = False
    return ok, rows


def render_report(rows: list[tuple[str, float, float, int, int, int]], engine_kind: str) -> str:
    model_note = "none (native text layer)"
    if engine_kind == "ocr":
        from app.paddle_engine import MODEL_ARTIFACTS

        model_note = ", ".join(f"{a.name}={a.sha256[:12]}" for a in MODEL_ARTIFACTS)
    lines = [
        "# 黄金集抽取指标报告（Golden extraction metric report）",
        "",
        f"- 抽取引擎: `{engine_kind}` — {model_note}",
        f"- 命令: `cd backend && uv run python scripts/evaluate_extraction.py "
        f"--engine {engine_kind}`",
        "- 阈值: 召回率 ≥ 0.90、精确率 ≥ 0.95（黄金集测试 `test_golden_extraction.py` 强制）",
        "- 语料: 企业/个人 × 全部支持格式（md、docx、xlsx、csv、pdf），标签见 `tests/fixtures/`；"
        " `--engine ocr` 额外包含扫描版 PDF（`.scan.pdf`）以驱动固定 PaddleOCR 模型",
        "",
        "## 逐字段指标",
        "",
        "| 材料:字段 | 召回率 | 精确率 | 命中/期望 | 抽取数 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, recall, precision, matched, expected, extracted in rows:
        lines.append(
            f"| {label} | {recall:.2f} | {precision:.2f} | {matched}/{expected} | {extracted} |"
        )
    ok = all(
        recall >= MIN_RECALL and precision >= MIN_PRECISION for _, recall, precision, *_ in rows
    )
    lines.extend(
        [
            "",
            "## 结论",
            "",
            f"全部字段达到阈值（召回率 ≥ {MIN_RECALL}、精确率 ≥ {MIN_PRECISION}）: "
            + ("是" if ok else "否"),
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        choices=("native", "ocr"),
        default="native",
        help="native = text-layer PDFs (deterministic, CI); ocr = pinned PaddleOCR models",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPORT,
        help="report output path (default: docs/release/golden-report.md)",
    )
    parser.add_argument(
        "--check-drift",
        action="store_true",
        help="write the report to a temp file and fail when it differs from the committed one",
    )
    args = parser.parse_args(argv)
    engine = make_engine(args.engine)
    ok, rows = evaluate(args.engine, engine)
    report = render_report(rows, args.engine)
    print(f"{'material:field':<52}{'recall':>8}{'precision':>10}  n")
    print("-" * 76)
    for label, recall, precision, matched, expected, extracted in rows:
        below = recall < MIN_RECALL or precision < MIN_PRECISION
        flag = "  <-- below threshold" if below else ""
        print(f"{label:<52}{recall:>8.2f}{precision:>10.2f}  {matched}/{expected}{flag}")
    print("-" * 76)
    print(f"thresholds: recall >= {MIN_RECALL}, precision >= {MIN_PRECISION}")
    if args.check_drift:
        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile("w", suffix=".md", delete=False) as handle:
            handle.write(report)
            tmp = handle.name
        committed = args.report.read_text(encoding="utf-8")
        if committed != report:
            print(f"drift detected: {args.report} is stale (regenerate it)")
            print(f"  fresh report written to {tmp}")
            return 1
        print(f"no drift: {args.report} is current")
    else:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(f"report written to {args.report}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
