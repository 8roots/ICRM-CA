import hashlib
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import Csrf, CurrentUser, Db
from app.idempotency import add_idempotency_record, replay_resource_id
from app.models import (
    Application,
    Document,
    DocumentOutput,
    DocumentPage,
    EvidenceReview,
    SealCandidate,
)
from app.parsing import IMAGE_EXTENSIONS, render_preview

router = APIRouter(tags=["document-outputs"])


def document_format(extension: str) -> str:
    if extension == ".pdf":
        return "pdf"
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension == ".docx":
        return "docx"
    if extension == ".xlsx":
        return "xlsx"
    if extension == ".csv":
        return "csv"
    if extension in {".md", ".markdown"}:
        return "markdown"
    return "other"


class CellResponse(BaseModel):
    id: str
    row: int
    column: int
    text: str
    bbox: tuple[float, float, float, float] | None
    locator: dict | None


class BlockResponse(BaseModel):
    id: str
    order: int
    kind: str
    text: str
    bbox: tuple[float, float, float, float] | None
    extraction_method: str
    confidence: float | None
    cells: list[CellResponse]
    locator: dict | None


class SealCandidateResponse(BaseModel):
    id: str
    text: str
    bbox: tuple[float, float, float, float]
    confidence: float
    model_version: str


class PageResponse(BaseModel):
    id: str
    number: int | None
    width: float | None
    height: float | None
    status: str
    error_code: str | None
    blocks: list[BlockResponse]
    seals: list[SealCandidateResponse]


class EvidenceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["seal_presence", "signature_presence"]
    status: Literal["present", "absent", "uncertain"]
    seal_candidate_id: str | None = None
    reason: str = Field(min_length=1, max_length=1000)


class EvidenceReviewResponse(BaseModel):
    id: str
    output_id: str
    seal_candidate_id: str | None
    kind: str
    status: str
    reason: str
    actor_id: str
    created_at: datetime


class OutputResponse(BaseModel):
    id: str
    document_id: str
    format: str
    version: int
    status: str
    parser_version: str
    model_version: str
    pages: list[PageResponse]


def owned_document(db: Db, document_id: str, owner_id: str) -> Document:
    document = (
        db.query(Document)
        .join(Application)
        .filter(Document.id == document_id, Application.owner_id == owner_id)
        .first()
    )
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


def owned_output(db: Db, output_id: str, owner_id: str) -> DocumentOutput:
    output = (
        db.query(DocumentOutput)
        .join(Document)
        .join(Application)
        .filter(DocumentOutput.id == output_id, Application.owner_id == owner_id)
        .first()
    )
    if not output:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document output not found")
    return output


def as_review(review: EvidenceReview) -> EvidenceReviewResponse:
    return EvidenceReviewResponse.model_validate(review, from_attributes=True)


def as_output(output: DocumentOutput) -> OutputResponse:
    return OutputResponse(
        id=output.id,
        document_id=output.document_id,
        format=document_format(output.document.extension),
        version=output.version,
        status=output.status,
        parser_version=output.parser_version,
        model_version=output.model_version,
        pages=[
            PageResponse(
                id=page.id,
                number=page.number,
                width=page.width,
                height=page.height,
                status=page.status,
                error_code=page.error_code,
                blocks=[
                    BlockResponse(
                        id=block.id,
                        order=block.order,
                        kind=block.kind,
                        text=block.text,
                        bbox=(
                            (block.x0, block.y0, block.x1, block.y1)
                            if block.x0 is not None
                            else None
                        ),
                        extraction_method=block.extraction_method,
                        confidence=block.confidence,
                        cells=[
                            CellResponse(
                                id=cell.id,
                                row=cell.row_index,
                                column=cell.column_index,
                                text=cell.text,
                                bbox=(
                                    (cell.x0, cell.y0, cell.x1, cell.y1)
                                    if cell.x0 is not None
                                    else None
                                ),
                                locator=cell.locator,
                            )
                            for cell in block.cells
                        ],
                        locator=block.locator,
                    )
                    for block in page.blocks
                ],
                seals=[
                    SealCandidateResponse(
                        id=seal.id,
                        text=seal.text,
                        bbox=(seal.x0, seal.y0, seal.x1, seal.y1),
                        confidence=seal.confidence,
                        model_version=seal.model_version,
                    )
                    for seal in page.seals
                ],
            )
            for page in output.pages
        ],
    )


@router.get("/documents/{document_id}/outputs", response_model=list[OutputResponse])
def list_outputs(document_id: str, db: Db, user: CurrentUser) -> list[OutputResponse]:
    document = owned_document(db, document_id, user.id)
    return [as_output(output) for output in sorted(document.outputs, key=lambda item: item.version)]


@router.get(
    "/document-outputs/{output_id}/reviews", response_model=list[EvidenceReviewResponse]
)
def list_reviews(output_id: str, db: Db, user: CurrentUser) -> list[EvidenceReviewResponse]:
    output = owned_output(db, output_id, user.id)
    return [as_review(review) for review in output.reviews]


@router.post(
    "/document-outputs/{output_id}/reviews",
    response_model=EvidenceReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_review(
    output_id: str,
    payload: EvidenceReviewRequest,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> EvidenceReviewResponse:
    output = owned_output(db, output_id, user.id)
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    operation = f"create_evidence_review:{output_id}"
    replay_id = replay_resource_id(db, user.id, operation, idempotency_key, request_hash)
    if replay_id:
        return as_review(db.get(EvidenceReview, replay_id))
    if payload.kind == "seal_presence":
        candidate = (
            db.query(SealCandidate)
            .join(DocumentPage)
            .filter(
                SealCandidate.id == payload.seal_candidate_id,
                DocumentPage.output_id == output.id,
            )
            .first()
        )
        if not candidate:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Seal candidate required")
    elif payload.seal_candidate_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Signature presence is recorded manually without a seal candidate",
        )
    review = EvidenceReview(
        output_id=output.id,
        seal_candidate_id=payload.seal_candidate_id,
        kind=payload.kind,
        status=payload.status,
        reason=payload.reason,
        actor_id=user.id,
    )
    db.add(review)
    db.flush()
    add_idempotency_record(
        db, user.id, operation, idempotency_key, request_hash, review.id
    )
    db.commit()
    return as_review(review)


@router.get("/documents/{document_id}/pages/{page_number}/image")
def page_image(
    document_id: str,
    page_number: int,
    request: Request,
    db: Db,
    user: CurrentUser,
) -> Response:
    document = owned_document(db, document_id, user.id)
    source = request.app.state.object_store.open(document.object_key)
    try:
        content = render_preview(document.filename, source, page_number)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    finally:
        source.close()
    return Response(content, media_type="image/png")
