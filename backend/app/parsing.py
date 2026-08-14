from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

import pymupdf
from PIL import Image

PARSER_VERSION = f"PyMuPDF-{pymupdf.__version__}"

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
PARSABLE_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS
RENDER_SCALE = 2
CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class Locator:
    """Format-native source reference for a parsed block or table cell.

    Exactly the fields for one format are populated: DOCX uses
    ``paragraph_path``, XLSX uses ``sheet`` plus ``cell_range``/``cell``, CSV
    uses ``row``/``column``/``column_name``, Markdown uses ``heading_path``
    and ``line_start``/``line_end``. PDF/image blocks carry no locator: their
    page and bounding box are already native.
    """

    kind: str  # "docx" | "xlsx" | "csv" | "markdown"
    paragraph_path: str | None = None
    sheet: str | None = None
    cell_range: str | None = None
    cell: str | None = None
    row: int | None = None
    column: int | None = None
    column_name: str | None = None
    encoding: str | None = None
    heading_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None


@dataclass(frozen=True)
class CellResult:
    row: int
    column: int
    text: str
    bbox: tuple[float, float, float, float] | None = None
    locator: Locator | None = None


@dataclass(frozen=True)
class BlockResult:
    order: int
    kind: str
    text: str
    bbox: tuple[float, float, float, float] | None
    extraction_method: str
    confidence: float | None = None
    cells: tuple[CellResult, ...] = ()
    locator: Locator | None = None


@dataclass(frozen=True)
class SealResult:
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float


@dataclass(frozen=True)
class Analysis:
    blocks: tuple[BlockResult, ...] = ()
    seals: tuple[SealResult, ...] = ()


class ImageAnalysisEngine(Protocol):
    version: str

    def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis: ...


@dataclass(frozen=True)
class PageResult:
    number: int | None
    width: float | None
    height: float | None
    status: str
    blocks: tuple[BlockResult, ...] = ()
    seals: tuple[SealResult, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class ParsedOutput:
    parser_version: str
    model_version: str
    status: str
    pages: tuple[PageResult, ...] = field(default_factory=tuple)


def copy_stream(stream, destination) -> None:
    while chunk := stream.read(CHUNK_SIZE):
        destination.write(chunk)
    destination.flush()


def render_preview(filename: str, stream, page_number: int) -> bytes:
    extension = Path(filename).suffix.lower()
    with NamedTemporaryFile() as material:
        copy_stream(stream, material)
        if extension == ".pdf":
            with pymupdf.open(material.name) as pdf:
                if page_number < 1 or page_number > len(pdf):
                    raise ValueError("page_not_found")
                pixmap = pdf[page_number - 1].get_pixmap(
                    matrix=pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE), alpha=False
                )
                return pixmap.tobytes("png")
        if extension in IMAGE_EXTENSIONS:
            image = Image.open(material.name)
            if page_number < 1 or page_number > getattr(image, "n_frames", 1):
                raise ValueError("page_not_found")
            image.seek(page_number - 1)
            output = BytesIO()
            image.convert("RGB").save(output, format="PNG")
            return output.getvalue()
    raise ValueError("unsupported_parser_format")


def scale_analysis(analysis: Analysis, scale_x: float, scale_y: float) -> Analysis:
    def bbox(values: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return (values[0] / scale_x, values[1] / scale_y, values[2] / scale_x, values[3] / scale_y)

    def cells(
        cell_results: tuple[CellResult, ...],
    ) -> tuple[CellResult, ...]:
        return tuple(
            CellResult(
                cell.row,
                cell.column,
                cell.text,
                bbox(cell.bbox) if cell.bbox else None,
                cell.locator,
            )
            for cell in cell_results
        )

    return Analysis(
        blocks=tuple(
            BlockResult(
                block.order,
                block.kind,
                block.text,
                bbox(block.bbox),
                block.extraction_method,
                block.confidence,
                cells(block.cells),
                block.locator,
            )
            for block in analysis.blocks
        ),
        seals=tuple(
            SealResult(seal.text, bbox(seal.bbox), seal.confidence) for seal in analysis.seals
        ),
    )


def parse_material(filename: str, stream, engine: ImageAnalysisEngine) -> ParsedOutput:
    extension = Path(filename).suffix.lower()
    if extension not in PARSABLE_EXTENSIONS:
        raise ValueError("unsupported_parser_format")
    with NamedTemporaryFile() as material:
        copy_stream(stream, material)
        if extension in IMAGE_EXTENSIONS:
            return _parse_images(material.name, engine)
        return _parse_pdf(material.name, engine)


def _parse_images(path: str, engine: ImageAnalysisEngine) -> ParsedOutput:
    pages = []
    with Image.open(path) as image:
        for page_number in range(1, getattr(image, "n_frames", 1) + 1):
            image.seek(page_number - 1)
            rendered = image.convert("RGB")
            try:
                encoded = BytesIO()
                rendered.save(encoded, format="PNG")
                analysis = engine.analyze(encoded.getvalue(), run_ocr=True)
                pages.append(
                    PageResult(
                        number=page_number,
                        width=float(rendered.width),
                        height=float(rendered.height),
                        status="success",
                        blocks=analysis.blocks,
                        seals=analysis.seals,
                    )
                )
            except Exception:
                pages.append(
                    PageResult(
                        number=page_number,
                        width=float(rendered.width),
                        height=float(rendered.height),
                        status="failed",
                        error_code="page_analysis_failed",
                    )
                )
    return _finish_output(pages, engine.version)


def _parse_pdf(path: str, engine: ImageAnalysisEngine) -> ParsedOutput:
    pages = []
    with pymupdf.open(path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            native_blocks = []
            for order, raw in enumerate(page.get_text("blocks")):
                text = raw[4].strip()
                if text:
                    native_blocks.append(
                        BlockResult(
                            order=order,
                            kind="paragraph",
                            text=text,
                            bbox=tuple(float(value) for value in raw[:4]),
                            extraction_method="pdf_text",
                        )
                    )
            run_ocr = not any(block.text.strip() for block in native_blocks)
            try:
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(RENDER_SCALE, RENDER_SCALE), alpha=False
                )
                analysis = engine.analyze(pixmap.tobytes("png"), run_ocr=run_ocr)
                analysis = scale_analysis(
                    analysis,
                    pixmap.width / page.rect.width,
                    pixmap.height / page.rect.height,
                )
                pages.append(
                    PageResult(
                        number=page_number,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        status="success",
                        blocks=tuple(native_blocks) if not run_ocr else analysis.blocks,
                        seals=analysis.seals,
                    )
                )
            except Exception:
                pages.append(
                    PageResult(
                        number=page_number,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        status="failed",
                        blocks=tuple(native_blocks),
                        error_code="page_analysis_failed",
                    )
                )
    return _finish_output(pages, engine.version)


def _finish_output(pages: list[PageResult], model_version: str) -> ParsedOutput:
    failed_pages = sum(page.status == "failed" for page in pages)
    output_status = "success"
    if failed_pages:
        output_status = "failed" if failed_pages == len(pages) else "partial_success"
    return ParsedOutput(
        parser_version=PARSER_VERSION,
        model_version=model_version,
        status=output_status,
        pages=tuple(pages),
    )
