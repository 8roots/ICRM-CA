"""Golden extraction measurements are wired to report recall/precision."""

from scripts.evaluate_extraction import MIN_PRECISION, MIN_RECALL, evaluate


def test_golden_extraction_meets_recall_and_precision_thresholds() -> None:
    ok, rows = evaluate()
    assert ok, "\n".join(
        f"{label}: recall={recall:.2f} precision={precision:.2f}"
        for label, recall, precision, *_ in rows
        if recall < MIN_RECALL or precision < MIN_PRECISION
    )
    assert rows
    assert all(recall >= MIN_RECALL for _, recall, _, _, _, _ in rows)
    assert all(precision >= MIN_PRECISION for _, _, precision, _, _, _ in rows)
