import pytest

from app.paddle_engine import (
    MODEL_ARTIFACTS,
    ocr_blocks_from_result,
    seal_candidates_from_results,
    verify_artifacts,
)


def official_ocr_result() -> dict:
    return {
        "input_path": None,
        "page_index": None,
        "model_settings": {"use_doc_preprocessor": False},
        "rec_texts": ["某某企业年度财务报表", "", "营业收入壹仟万元"],
        "rec_scores": [1.0, 0.99, 0.999],
        "rec_polys": [
            [[40, 60], [460, 60], [460, 100], [40, 100]],
            [[40, 130], [460, 130], [460, 170], [40, 170]],
            [[40, 200], [360, 200], [360, 240], [40, 240]],
        ],
    }


def test_ocr_result_maps_to_contract_blocks_in_page_coordinates() -> None:
    blocks = ocr_blocks_from_result(official_ocr_result())

    assert len(blocks) == 2
    first, second = blocks
    assert first.text == "某某企业年度财务报表"
    assert first.kind == "paragraph"
    assert first.extraction_method == "ocr"
    assert first.confidence == 1.0
    assert first.bbox == (40, 60, 460, 100)
    assert first.order == 0
    assert second.text == "营业收入壹仟万元"
    assert second.order == 1


def test_ocr_result_without_texts_yields_no_blocks() -> None:
    assert ocr_blocks_from_result({"rec_texts": [], "rec_scores": [], "rec_polys": []}) == ()
    assert ocr_blocks_from_result({}) == ()


def test_seal_results_map_to_one_candidate_per_page_union_bbox() -> None:
    det_result = {
        "dt_polys": [
            [[320, 38], [461, 38], [461, 88], [320, 88]],
            [[158, 468], [280, 468], [280, 510], [158, 510]],
        ],
    }
    outcomes = [
        {"rec_text": "某某小额贷款有限公司", "rec_score": 0.99},
        {"rec_text": "合同专用章", "rec_score": 0.95},
        {"rec_text": "", "rec_score": 0.0},
    ]

    candidates = seal_candidates_from_results(det_result, outcomes)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.text == "某某小额贷款有限公司合同专用章"
    assert candidate.bbox == (158, 38, 461, 510)
    assert candidate.confidence == pytest.approx(0.97)


def test_seal_results_without_detections_or_texts_yield_no_candidates() -> None:
    assert seal_candidates_from_results({"dt_polys": []}, []) == ()
    assert seal_candidates_from_results({}, []) == ()
    assert seal_candidates_from_results(
        {"dt_polys": [[[0, 0], [10, 0], [10, 10], [0, 10]]]},
        [{"rec_text": "", "rec_score": 0.0}],
    ) == ()


def test_verify_artifacts_passes_only_with_matching_sidecar(tmp_path) -> None:
    for artifact in MODEL_ARTIFACTS:
        model_dir = tmp_path / f"{artifact.name}_infer"
        model_dir.mkdir()
        for name in artifact.files:
            model_dir.joinpath(name).write_text("data")
        model_dir.joinpath(".icrm_sha256").write_text(artifact.sha256 + "\n")

    verify_artifacts(tmp_path)


def test_verify_artifacts_fails_fast_when_corrupted(tmp_path) -> None:
    first = MODEL_ARTIFACTS[0]
    model_dir = tmp_path / f"{first.name}_infer"
    model_dir.mkdir()
    for name in first.files:
        model_dir.joinpath(name).write_text("data")
    model_dir.joinpath(".icrm_sha256").write_text("0" * 64)

    with pytest.raises(RuntimeError, match="absent or corrupted"):
        verify_artifacts(tmp_path)


def test_verify_artifacts_fails_fast_when_missing(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="absent or corrupted"):
        verify_artifacts(tmp_path)
