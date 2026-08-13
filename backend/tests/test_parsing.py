import io

import pytest
from PIL import Image

from app.parsing import (
    Analysis,
    BlockResult,
    CellResult,
    SealResult,
    parse_material,
    scale_analysis,
)


class RecordingEngine:
    version = "fake-ocr-1"

    def __init__(self) -> None:
        self.ocr_requests: list[bool] = []

    def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
        self.ocr_requests.append(run_ocr)
        return Analysis()


def blank_pdf() -> bytes:
    import pymupdf

    pdf = pymupdf.open()
    pdf.new_page(width=300, height=200)
    return pdf.tobytes()


def mixed_pdf() -> bytes:
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=200)
    page.insert_text((30, 50), "Native first page")
    pdf.new_page(width=300, height=200)
    return pdf.tobytes()


def text_pdf() -> bytes:
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=200)
    page.insert_text((30, 50), "Synthetic borrower statement")
    return pdf.tobytes()


def image_bytes(format_name: str) -> bytes:
    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (120, 80), "white").save(output, format=format_name)
    return output.getvalue()


def test_text_pdf_uses_native_blocks_without_unnecessary_ocr() -> None:
    engine = RecordingEngine()

    parsed = parse_material("statement.pdf", io.BytesIO(text_pdf()), engine)

    assert engine.ocr_requests == [False]
    assert parsed.status == "success"
    assert parsed.parser_version
    assert len(parsed.pages) == 1
    page = parsed.pages[0]
    assert (page.number, page.width, page.height, page.status) == (1, 300, 200, "success")
    assert any(
        isinstance(block, BlockResult)
        and block.text == "Synthetic borrower statement"
        and block.extraction_method == "pdf_text"
        and block.bbox[0] >= 0
        for block in page.blocks
    )


def test_scanned_pdf_uses_ocr_and_converts_pixel_coordinates_to_page_coordinates() -> None:
    class OcrEngine(RecordingEngine):
        def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
            self.ocr_requests.append(run_ocr)
            return Analysis(
                blocks=(BlockResult(0, "paragraph", "扫描文字", (20, 40, 140, 80), "ocr", 0.9),),
                seals=(SealResult("印章候选", (120, 60, 220, 150), 0.8),),
            )

    engine = OcrEngine()
    parsed = parse_material("scan.pdf", io.BytesIO(blank_pdf()), engine)

    assert engine.ocr_requests == [True]
    assert parsed.pages[0].blocks[0].bbox == (10, 20, 70, 40)
    assert parsed.pages[0].seals[0].bbox == (60, 30, 110, 75)


def test_failed_page_keeps_other_pages_reviewable_without_logging_source_text(caplog) -> None:
    class PartialEngine(RecordingEngine):
        def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
            self.ocr_requests.append(run_ocr)
            if run_ocr:
                raise RuntimeError("synthetic OCR failure")
            return Analysis()

    parsed = parse_material("mixed.pdf", io.BytesIO(mixed_pdf()), PartialEngine())

    assert parsed.status == "partial_success"
    assert parsed.pages[0].status == "success"
    assert parsed.pages[0].blocks[0].text == "Native first page"
    assert parsed.pages[1].status == "failed"
    assert parsed.pages[1].error_code == "page_analysis_failed"
    assert "Native first page" not in caplog.text


def test_table_cells_survive_coordinate_scaling_and_round_trip() -> None:
    analysis = Analysis(
        blocks=(
            BlockResult(
                0,
                "table",
                "流水明细",
                (20, 40, 200, 120),
                "ocr",
                0.9,
                cells=(
                    CellResult(0, 0, "日期", (22, 44, 80, 60)),
                    CellResult(0, 1, "金额", (82, 44, 190, 60)),
                    CellResult(1, 0, "2026-08-01", (22, 64, 80, 110)),
                ),
            ),
        ),
    )

    scaled = scale_analysis(analysis, 2.0, 2.0)

    assert scaled.blocks[0].bbox == (10, 20, 100, 60)
    assert scaled.blocks[0].cells[0].bbox == (11, 22, 40, 30)
    assert scaled.blocks[0].cells[1].text == "金额"
    assert scaled.blocks[0].cells[2].row == 1
    assert scaled.blocks[0].cells[2].column == 0


def test_multi_page_tiff_keeps_other_pages_reviewable_on_failure() -> None:
    class PartialImageEngine(RecordingEngine):
        def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
            self.ocr_requests.append(run_ocr)
            return Analysis(
                blocks=(
                    BlockResult(
                        0, "paragraph", "本页文字", (10, 20, 70, 40), "ocr", 0.9
                    ),
                ),
            )

    multi = io.BytesIO()
    second = Image.new("RGB", (120, 80), "white")
    Image.new("RGB", (120, 80), "white").save(
        multi, format="TIFF", save_all=True, append_images=[second]
    )
    multi.seek(0)

    class FailSecondPage(PartialImageEngine):
        def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
            self.ocr_requests.append(run_ocr)
            if len(self.ocr_requests) == 2:
                raise RuntimeError("synthetic second page failure")
            return Analysis(
                blocks=(
                    BlockResult(
                        0, "paragraph", "本页文字", (10, 20, 70, 40), "ocr", 0.9
                    ),
                ),
            )

    engine = FailSecondPage()
    parsed = parse_material("pages.tiff", multi, engine)

    assert parsed.status == "partial_success"
    assert parsed.pages[0].status == "success"
    assert parsed.pages[0].blocks[0].text == "本页文字"
    assert parsed.pages[1].status == "failed"
    assert parsed.pages[1].error_code == "page_analysis_failed"

@pytest.mark.parametrize(
    ("filename", "format_name"),
    [("scan.png", "PNG"), ("photo.jpg", "JPEG"), ("pages.tiff", "TIFF")],
)
def test_image_formats_use_the_same_page_block_and_seal_contract(
    filename: str, format_name: str
) -> None:
    class ImageEngine(RecordingEngine):
        def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
            self.ocr_requests.append(run_ocr)
            return Analysis(
                blocks=(BlockResult(0, "paragraph", "识别文字", (10, 20, 70, 40), "ocr", 0.9),),
                seals=(SealResult("印章文字候选", (60, 30, 110, 75), 0.8),),
            )

    engine = ImageEngine()
    parsed = parse_material(filename, io.BytesIO(image_bytes(format_name)), engine)

    assert engine.ocr_requests == [True]
    assert parsed.status == "success"
    assert len(parsed.pages) == 1
    page = parsed.pages[0]
    assert (page.number, page.width, page.height) == (1, 120, 80)
    assert page.blocks[0].bbox == (10, 20, 70, 40)
    assert page.blocks[0].extraction_method == "ocr"
    assert page.seals[0].bbox == (60, 30, 110, 75)
