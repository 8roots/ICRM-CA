"""Local PaddleOCR image-analysis engine backed by pinned model artifacts.

The engine runs two official PaddleOCR pipelines against a rendered page:

- the general OCR pipeline (PP-OCRv5 mobile det/rec) for text blocks;
- the official seal text detection model (PP-OCRv4 mobile seal det) plus text
  recognition for seal candidates, in the seal pipeline's flat mode, i.e. one
  candidate per page whose bounding box covers every detected seal text line.

Model tarballs are downloaded once at image build time (see
``scripts/download_models.py``), verified against pinned SHA-256 values, and
extracted into ``ICRM_MODELS_DIR``. The engine always passes explicit local
``model_dir`` paths so PaddleX can never download or update models at runtime;
``verify_artifacts`` fails startup when artifacts are absent or corrupted.
"""

import logging
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from app.parsing import Analysis, BlockResult, SealResult

logging.getLogger("paddleocr").setLevel(logging.WARNING)
logging.getLogger("paddlex").setLevel(logging.WARNING)

PADDLE_VERSION = "paddleocr-3.7.0"
DET_MODEL = "PP-OCRv5_mobile_det"
REC_MODEL = "PP-OCRv5_mobile_rec"
SEAL_MODEL = "PP-OCRv4_mobile_seal_det"
CROP_MARGIN = 6


@dataclass(frozen=True)
class ModelArtifact:
    name: str
    sha256: str
    files: tuple[str, ...] = ("inference.yml", "inference.json", "inference.pdiparams")


MODEL_ARTIFACTS = (
    ModelArtifact(DET_MODEL, "50446e5d01ac2a73d5319c89513281f6578414c888c602f9af13f93feefffc58"),
    ModelArtifact(REC_MODEL, "566b9512b34e34a9f0db54d87b51fa5a0b9ed2cf1ab7e49728cc0b8b5a64f414"),
    ModelArtifact(SEAL_MODEL, "cc245c1b0ab275dd16ee7a85f10e1d5fae33ad78ddb52d0de58442b4fa2afddc"),
)


def verify_artifacts(models_dir: str | Path) -> None:
    """Raise when a pinned model artifact is absent or its checksum mismatches."""
    models_dir = Path(models_dir)
    missing = []
    for artifact in MODEL_ARTIFACTS:
        model_dir = models_dir / f"{artifact.name}_infer"
        sidecar = model_dir / ".icrm_sha256"
        files_present = all((model_dir / name).is_file() for name in artifact.files)
        if not files_present or not sidecar.is_file():
            missing.append(artifact.name)
            continue
        if sidecar.read_text().strip() != artifact.sha256:
            missing.append(artifact.name)
    if missing:
        raise RuntimeError(
            "pinned OCR model artifacts are absent or corrupted: " + ", ".join(missing)
        )


def _bbox_of_poly(poly) -> tuple[float, float, float, float]:
    points = np.asarray(poly, dtype=float)
    return (
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    )


def _union_bbox(polys) -> tuple[float, float, float, float]:
    boxes = [_bbox_of_poly(poly) for poly in polys]
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def ocr_blocks_from_result(result: dict) -> tuple[BlockResult, ...]:
    """Map an official PaddleOCR pipeline result dict to contract blocks."""
    texts = result.get("rec_texts") or []
    scores = result.get("rec_scores") or []
    polys = result.get("rec_polys") or []
    blocks = []
    order = 0
    for text, score, poly in zip(texts, scores, polys):
        text = str(text).strip()
        if not text:
            continue
        blocks.append(
            BlockResult(
                order=order,
                kind="paragraph",
                text=text,
                bbox=_bbox_of_poly(poly),
                extraction_method="ocr",
                confidence=float(score),
            )
        )
        order += 1
    return tuple(blocks)


def seal_candidates_from_results(
    det_result: dict, rec_outcomes: list[dict]
) -> tuple[SealResult, ...]:
    """Map official seal detection and per-crop recognition results to candidates."""
    polys = [np.asarray(poly) for poly in det_result.get("dt_polys") or []]
    if not polys:
        return ()
    texts = []
    scores = []
    for rec in rec_outcomes:
        text = str(rec.get("rec_text", "")).strip()
        score = float(rec.get("rec_score", 0.0))
        if text:
            texts.append(text)
            scores.append(score)
    if not texts:
        return ()
    return (
        SealResult(
            text="".join(texts),
            bbox=_union_bbox(polys),
            confidence=sum(scores) / len(scores),
        ),
    )


def _as_bgr_array(image: bytes) -> np.ndarray:
    rgb = np.asarray(Image.open(BytesIO(image)).convert("RGB"))
    return rgb[:, :, ::-1].copy()


def _crop(array: np.ndarray, poly) -> np.ndarray:
    points = np.asarray(poly)
    x0 = max(int(points[:, 0].min()) - CROP_MARGIN, 0)
    y0 = max(int(points[:, 1].min()) - CROP_MARGIN, 0)
    x1 = min(int(points[:, 0].max()) + CROP_MARGIN, array.shape[1])
    y1 = min(int(points[:, 1].max()) + CROP_MARGIN, array.shape[0])
    return array[y0:y1, x0:x1]


class PaddleEngine:
    """Image-analysis engine backed by pinned local PaddleOCR models."""

    def __init__(
        self,
        models_dir: str | Path,
        *,
        device: str = "cpu",
        enable_mkldnn: bool = False,
        cpu_threads: int = 1,
    ) -> None:
        from paddleocr import PaddleOCR, TextDetection, TextRecognition

        verify_artifacts(models_dir)
        models_dir = Path(models_dir)
        self.ocr = PaddleOCR(
            text_detection_model_name=DET_MODEL,
            text_detection_model_dir=str(models_dir / f"{DET_MODEL}_infer"),
            text_recognition_model_name=REC_MODEL,
            text_recognition_model_dir=str(models_dir / f"{REC_MODEL}_infer"),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device=device,
            enable_mkldnn=enable_mkldnn,
            cpu_threads=cpu_threads,
        )
        self.seal_det = TextDetection(
            model_name=SEAL_MODEL,
            model_dir=str(models_dir / f"{SEAL_MODEL}_infer"),
            device=device,
            enable_mkldnn=enable_mkldnn,
            cpu_threads=cpu_threads,
        )
        self.seal_rec = TextRecognition(
            model_name=REC_MODEL,
            model_dir=str(models_dir / f"{REC_MODEL}_infer"),
            device=device,
            enable_mkldnn=enable_mkldnn,
            cpu_threads=cpu_threads,
        )
        self.version = f"{PADDLE_VERSION}-{DET_MODEL}-{REC_MODEL}-{SEAL_MODEL}"

    def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
        array = _as_bgr_array(image)
        blocks = self._ocr_blocks(array) if run_ocr else ()
        seals = self._seal_candidates(array)
        return Analysis(blocks=blocks, seals=seals)

    def _ocr_blocks(self, array: np.ndarray) -> tuple[BlockResult, ...]:
        result = next(iter(self.ocr.predict(array)))
        return ocr_blocks_from_result(result.json["res"])

    def _seal_candidates(self, array: np.ndarray) -> tuple[SealResult, ...]:
        det = next(iter(self.seal_det.predict(array, batch_size=1)))
        det_result = det.json["res"]
        polys = [np.asarray(poly) for poly in det_result.get("dt_polys") or []]
        if not polys:
            return ()
        crops = [_crop(array, poly) for poly in polys]
        outcomes = [
            outcome.json["res"]
            for outcome in self.seal_rec.predict(crops, batch_size=len(crops))
        ]
        return seal_candidates_from_results(det_result, outcomes)
