"""Read-only candidates, resolution creation, and restricted cloud audit API.

Candidates are immutable: there are no update or delete endpoints, and
corrections create separate :class:`Resolution` rows. Manual resolutions have no
material source and require a reason. Every endpoint is scoped to the
application owner, so another user or an unassigned administrator can neither
view facts nor the restricted cloud audit content.
"""

import hashlib
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.completeness import mark_runs_stale
from app.dependencies import Csrf, CurrentUser, Db
from app.fields import FIELDS, SUBJECT_LABELS, SubjectRole, field_def
from app.idempotency import add_idempotency_record, replay_resource_id
from app.models import (
    Application,
    CandidateFact,
    CloudExtractionCall,
    Document,
    DocumentOutput,
    Resolution,
)
from app.values import normalize_field

router = APIRouter(prefix="/applications", tags=["candidates"])


class SourceRefResponse(BaseModel):
    document_id: str
    output_id: str
    output_version: int
    page_number: int | None
    block_id: str
    block_order: int
    cell_id: str | None = None
    locator: dict | None = None
    bbox: list | None = None
    cell_locator: dict | None = None
    cell_bbox: list | None = None


class CandidateResponse(BaseModel):
    id: str
    document_id: str
    filename: str
    output_id: str
    output_version: int
    field_key: str
    field_label: str
    group: str
    critical: bool
    subject_role: str | None
    subject_label: str | None
    raw_text: str
    typed_value: dict
    confidence: float
    extractor: str
    extractor_version: str
    model_version: str
    prompt_version: str | None
    source_refs: list[SourceRefResponse]


class ResolutionResponse(BaseModel):
    id: str
    application_id: str
    candidate_id: str | None
    field_key: str
    field_label: str
    subject_role: str | None
    resolution_type: str
    typed_value: dict
    no_material_source: bool
    reason: str | None
    actor_id: str
    created_at: datetime


class CloudCallResponse(BaseModel):
    id: str
    status: str
    error_code: str | None
    model: str
    prompt_version: str
    redaction_version: str
    source_refs: list[dict]
    redacted_request: dict
    redacted_response: dict | None
    created_at: datetime


class ResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_type: Literal["selected", "corrected", "manual"]
    field_key: str
    subject_role: SubjectRole | None = None
    candidate_id: str | None = None
    value: str = Field(default="", max_length=500)
    reason: str | None = Field(default=None, max_length=1000)


def owned_application(db: Db, application_id: str, owner_id: str) -> Application:
    application = (
        db.query(Application).filter_by(id=application_id, owner_id=owner_id).first()
    )
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


def as_source_ref(ref: dict) -> SourceRefResponse:
    return SourceRefResponse(
        document_id=ref.get("document_id"),
        output_id=ref.get("output_id"),
        output_version=ref.get("output_version"),
        page_number=ref.get("page_number"),
        block_id=ref.get("block_id"),
        block_order=ref.get("block_order"),
        cell_id=ref.get("cell_id"),
        locator=ref.get("locator"),
        bbox=ref.get("bbox"),
        cell_locator=ref.get("cell_locator"),
        cell_bbox=ref.get("cell_bbox"),
    )


def as_candidate(
    candidate: CandidateFact, filename: str, output_version: int
) -> CandidateResponse:
    definition = FIELDS.get(candidate.field_key)
    return CandidateResponse(
        id=candidate.id,
        document_id=candidate.document_id,
        filename=filename,
        output_id=candidate.output_id,
        output_version=output_version,
        field_key=candidate.field_key,
        field_label=definition.label if definition else candidate.field_key,
        group=definition.group.value if definition else "",
        critical=definition.critical if definition else False,
        subject_role=candidate.subject_role,
        subject_label=SUBJECT_LABELS.get(candidate.subject_role)
        if candidate.subject_role
        else None,
        raw_text=candidate.raw_text,
        typed_value=candidate.typed_value,
        confidence=candidate.confidence,
        extractor=candidate.extractor,
        extractor_version=candidate.extractor_version,
        model_version=candidate.model_version,
        prompt_version=candidate.prompt_version,
        source_refs=[as_source_ref(ref) for ref in candidate.source_refs],
    )


def as_resolution(resolution: Resolution) -> ResolutionResponse:
    definition = FIELDS.get(resolution.field_key)
    return ResolutionResponse(
        id=resolution.id,
        application_id=resolution.application_id,
        candidate_id=resolution.candidate_id,
        field_key=resolution.field_key,
        field_label=definition.label if definition else resolution.field_key,
        subject_role=resolution.subject_role,
        resolution_type=resolution.resolution_type,
        typed_value=resolution.typed_value,
        no_material_source=resolution.resolution_type == "manual",
        reason=resolution.reason,
        actor_id=resolution.actor_id,
        created_at=resolution.created_at,
    )


def as_cloud_call(call: CloudExtractionCall) -> CloudCallResponse:
    return CloudCallResponse(
        id=call.id,
        status=call.status,
        error_code=call.error_code,
        model=call.model,
        prompt_version=call.prompt_version,
        redaction_version=call.redaction_version,
        source_refs=call.source_refs,
        redacted_request=call.redacted_request,
        redacted_response=call.redacted_response,
        created_at=call.created_at,
    )


@router.get("/{application_id}/candidates", response_model=list[CandidateResponse])
def list_candidates(application_id: str, db: Db, user: CurrentUser) -> list[CandidateResponse]:
    owned_application(db, application_id, user.id)
    rows = (
        db.query(CandidateFact, Document.filename, DocumentOutput.version)
        .join(Document, CandidateFact.document_id == Document.id)
        .join(DocumentOutput, CandidateFact.output_id == DocumentOutput.id)
        .filter(Document.application_id == application_id)
        .order_by(CandidateFact.confidence.desc(), CandidateFact.created_at)
        .all()
    )
    return [as_candidate(candidate, filename, version) for candidate, filename, version in rows]


@router.get("/{application_id}/resolutions", response_model=list[ResolutionResponse])
def list_resolutions(application_id: str, db: Db, user: CurrentUser) -> list[ResolutionResponse]:
    owned_application(db, application_id, user.id)
    resolutions = (
        db.query(Resolution)
        .filter_by(application_id=application_id)
        .order_by(Resolution.created_at)
        .all()
    )
    return [as_resolution(resolution) for resolution in resolutions]


def _candidate_in_application(
    db: Db, application_id: str, candidate_id: str
) -> CandidateFact:
    candidate = (
        db.query(CandidateFact)
        .join(Document)
        .filter(
            CandidateFact.id == candidate_id,
            Document.application_id == application_id,
        )
        .first()
    )
    if not candidate:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Candidate not found in this application"
        )
    return candidate


@router.post(
    "/{application_id}/resolutions",
    response_model=ResolutionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_resolution(
    application_id: str,
    payload: ResolutionRequest,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> ResolutionResponse:
    owned_application(db, application_id, user.id)
    definition = field_def(payload.field_key)
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    operation = f"create_resolution:{application_id}"
    replay_id = replay_resource_id(db, user.id, operation, idempotency_key, request_hash)
    if replay_id:
        response.status_code = status.HTTP_200_OK
        return as_resolution(db.get(Resolution, replay_id))

    candidate = None
    if payload.resolution_type in {"selected", "corrected"}:
        if not payload.candidate_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "candidate_id is required"
            )
        candidate = _candidate_in_application(db, application_id, payload.candidate_id)
        if candidate.field_key != payload.field_key:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "candidate field does not match the resolution field",
            )
    else:
        if payload.candidate_id is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "manual resolutions must not reference a candidate",
            )
        if not payload.reason or not payload.reason.strip():
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "manual resolutions require a reason",
            )
    if payload.resolution_type in {"corrected", "manual"} and not payload.value.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "value is required"
        )

    if payload.resolution_type == "selected":
        typed_value = candidate.typed_value
    else:
        default_currency = None
        default_unit = None
        if candidate is not None:
            default_currency = candidate.typed_value.get("currency")
            default_unit = candidate.typed_value.get("unit")
        typed = normalize_field(
            definition,
            payload.value,
            default_currency=default_currency,
            default_unit=default_unit,
        )
        if typed is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "value cannot be normalized"
            )
        typed.raw_text = payload.value.strip()
        typed_value = typed.model_dump_stored()

    resolution = Resolution(
        application_id=application_id,
        candidate_id=candidate.id if candidate else None,
        field_key=payload.field_key,
        subject_role=(
            payload.subject_role
            or (candidate.subject_role if candidate else None)
            or definition.default_subject
        ),
        resolution_type=payload.resolution_type,
        typed_value=typed_value,
        reason=payload.reason,
        actor_id=user.id,
    )
    db.add(resolution)
    db.flush()
    add_idempotency_record(
        db, user.id, operation, idempotency_key, request_hash, resolution.id
    )
    # Resolutions feed completeness condition context (e.g. collateral presence),
    # so they invalidate any current formal completeness report.
    mark_runs_stale(db, application_id, "condition_context_change")
    db.commit()
    return as_resolution(resolution)


@router.get("/{application_id}/cloud-calls", response_model=list[CloudCallResponse])
def list_cloud_calls(application_id: str, db: Db, user: CurrentUser) -> list[CloudCallResponse]:
    owned_application(db, application_id, user.id)
    calls = (
        db.query(CloudExtractionCall)
        .filter_by(application_id=application_id)
        .order_by(CloudExtractionCall.created_at.desc())
        .all()
    )
    return [as_cloud_call(call) for call in calls]
