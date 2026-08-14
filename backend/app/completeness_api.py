"""Application completeness: live draft, confirmations, mappings, formal runs.

Everything here is scoped to the application owner. The live draft is computed
on read from confirmed inputs; a formal run freezes template + inputs +
results into an immutable snapshot with a content hash. Any change to
classification, mapping, waiver, or evidence review marks the current run
stale, and a newer published template version also makes it stale.
"""

import hashlib
from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.audit import (
    CLASSIFICATION_CONFIRMED,
    COMPLETENESS_RUN_CREATED,
    COMPLETENESS_VIEWED,
    MAPPING_CREATED,
    MAPPING_DELETED,
    WAIVER_CREATED,
    record_audit,
    request_correlation_id,
)
from app.classification import CATEGORY_LABELS, MaterialCategory
from app.completeness import (
    CONDITION_LABELS,
    NOT_APPLICABLE_REASONS,
    STATE_LABELS,
    build_run_snapshots,
    classification_candidates_by_document,
    condition_context,
    confirmed_category_by_document,
    evaluate_items,
    mappings_by_item_code,
    mark_runs_stale,
    reason_for_state,
    render_printable_html,
    run_content_hash,
    seal_present_documents,
    signature_present_documents,
    template_payload,
    waiver_item_codes,
)
from app.dependencies import Csrf, CurrentUser, Db
from app.idempotency import add_idempotency_record, replay_resource_id
from app.lifecycle_guard import require_mutable
from app.models import (
    Application,
    ChecklistItem,
    ClassificationConfirmation,
    CompletenessRun,
    CompletenessTemplate,
    Document,
    DocumentChecklistMapping,
    MaterialClassificationCandidate,
    RunStatus,
    TemplateStatus,
    User,
    WaiverRecord,
)

router = APIRouter(prefix="/applications", tags=["completeness"])


class ClassificationCandidateResponse(BaseModel):
    category: str
    category_label: str
    confidence: float
    method: str
    method_version: str


class CompletenessDocumentResponse(BaseModel):
    id: str
    filename: str
    confirmed_category: str | None
    classification_candidates: list[ClassificationCandidateResponse]
    seal_confirmed: bool
    signature_confirmed: bool


class CompletenessItemResponse(BaseModel):
    id: str
    code: str
    label: str
    category: str
    category_label: str
    order: int
    requires_seal: bool
    requires_signature: bool
    condition: dict | None
    condition_label: str | None
    state: str
    state_label: str
    evidence_document_ids: list[str]
    reason: str


class MappingResponse(BaseModel):
    id: str
    document_id: str
    document_filename: str
    item_id: str
    item_code: str
    item_label: str
    actor_id: str
    created_at: datetime


class WaiverResponse(BaseModel):
    id: str
    item_id: str
    item_code: str
    item_label: str
    reason: str
    actor_id: str
    created_at: datetime


class RunSummaryResponse(BaseModel):
    id: str
    created_at: datetime
    status: str
    stale: bool
    stale_reason: str | None
    content_hash: str
    template_code: str
    template_version: int
    actor_id: str


class LiveDraftResponse(BaseModel):
    template: dict | None
    no_template_reason: str | None
    items: list[CompletenessItemResponse]
    documents: list[CompletenessDocumentResponse]
    mappings: list[MappingResponse]
    waivers: list[WaiverResponse]
    condition_context: dict[str, bool]
    latest_run: RunSummaryResponse | None
    formal_run_blocked_reason: str | None


class ConfirmClassificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: MaterialCategory


class CreateMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    item_id: str


class CreateWaiverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str
    reason: str = Field(min_length=1, max_length=1000)


def owned_application(db: Db, application_id: str, owner_id: str) -> Application:
    application = db.query(Application).filter_by(id=application_id, owner_id=owner_id).first()
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


def published_applicable_template(db: Db, application: Application) -> CompletenessTemplate | None:
    return (
        db.query(CompletenessTemplate)
        .filter_by(
            product=application.product,
            borrower_type=application.borrower_type,
            status=TemplateStatus.PUBLISHED,
        )
        .first()
    )


def document_in_application(db: Db, application_id: str, document_id: str) -> Document:
    document = db.query(Document).filter_by(id=document_id, application_id=application_id).first()
    if not document:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Document not in application")
    return document


def item_in_template(db: Db, template: CompletenessTemplate, item_id: str) -> ChecklistItem:
    item = db.query(ChecklistItem).filter_by(id=item_id, template_id=template.id).first()
    if not item:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Item not in the applicable template",
        )
    return item


def as_mapping(mapping: DocumentChecklistMapping) -> MappingResponse:
    return MappingResponse(
        id=mapping.id,
        document_id=mapping.document_id,
        document_filename=mapping.document.filename,
        item_id=mapping.item_id,
        item_code=mapping.item.code,
        item_label=mapping.item.label,
        actor_id=mapping.actor_id,
        created_at=mapping.created_at,
    )


def as_waiver(waiver: WaiverRecord) -> WaiverResponse:
    return WaiverResponse(
        id=waiver.id,
        item_id=waiver.item_id,
        item_code=waiver.item.code,
        item_label=waiver.item.label,
        reason=waiver.reason,
        actor_id=waiver.actor_id,
        created_at=waiver.created_at,
    )


def run_staleness(
    run: CompletenessRun, current_template: CompletenessTemplate | None
) -> tuple[bool, str | None]:
    """Effective staleness: stored flag plus a newer published template version."""
    stale_reason = run.stale_reason
    template_changed = bool(
        current_template
        and current_template.id != run.template_id
        and run.status == RunStatus.CURRENT
    )
    if template_changed:
        stale_reason = "template_changed"
    return run.status == RunStatus.STALE or template_changed, stale_reason


def as_run_summary(
    run: CompletenessRun, current_template: CompletenessTemplate | None
) -> RunSummaryResponse:
    stale, stale_reason = run_staleness(run, current_template)
    return RunSummaryResponse(
        id=run.id,
        created_at=run.created_at,
        status=run.status,
        stale=stale,
        stale_reason=stale_reason,
        content_hash=run.content_hash,
        template_code=run.template_snapshot.get("code", ""),
        template_version=run.template_snapshot.get("version", 0),
        actor_id=run.actor_id,
    )


def _run_evaluation(
    db: Db, application: Application, template: CompletenessTemplate
) -> tuple[list[dict], dict[str, str], set[str], set[str], dict[str, bool]]:
    confirmed_category = confirmed_category_by_document(db, application.id)
    candidates = classification_candidates_by_document(db, application.id)
    mappings = mappings_by_item_code(db, application.id)
    seal_present = seal_present_documents(db, application.id)
    signature_present = signature_present_documents(db, application.id)
    waivers = waiver_item_codes(db, application.id)
    context = condition_context(db, application.id)
    states = evaluate_items(
        template.items,
        waivers=waivers,
        condition_context=context,
        mappings=mappings,
        confirmed_category=confirmed_category,
        classification_candidates=candidates,
        seal_present=seal_present,
        signature_present=signature_present,
    )
    reasons = NOT_APPLICABLE_REASONS
    items = [
        {
            "id": item.id,
            "code": item.code,
            "label": item.label,
            "category": item.category,
            "category_label": CATEGORY_LABELS.get(MaterialCategory(item.category), item.category),
            "order": item.order,
            "requires_seal": item.requires_seal,
            "requires_signature": item.requires_signature,
            "condition": item.condition,
            "condition_label": (
                CONDITION_LABELS.get(item.condition.get("requires")) if item.condition else None
            ),
            "state": states[item.code].value,
            "state_label": STATE_LABELS[states[item.code]],
            "evidence_document_ids": sorted(mappings.get(item.code, set())),
            "reason": reason_for_state(item, states[item.code], reasons),
        }
        for item in template.items
    ]
    return items, confirmed_category, seal_present, signature_present, context


@router.get("/{application_id}/completeness", response_model=LiveDraftResponse)
def get_completeness(
    application_id: str, request: Request, db: Db, user: CurrentUser
) -> LiveDraftResponse:
    application = owned_application(db, application_id, user.id)
    record_audit(
        db,
        event_type=COMPLETENESS_VIEWED,
        actor=user,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
        dedupe_minutes=5,
    )
    db.commit()
    template = published_applicable_template(db, application)
    if template is None:
        return LiveDraftResponse(
            template=None,
            no_template_reason="没有适用于该产品与主借款人类型的已发布模板，无法生成正式报告",
            items=[],
            documents=[],
            mappings=[],
            waivers=[],
            condition_context=condition_context(db, application_id),
            latest_run=None,
            formal_run_blocked_reason="无已发布适用模板",
        )

    items, confirmed_category, seal_present, signature_present, context = _run_evaluation(
        db, application, template
    )
    documents = (
        db.query(Document)
        .filter_by(application_id=application_id)
        .order_by(Document.created_at)
        .all()
    )
    candidate_rows = (
        db.query(MaterialClassificationCandidate)
        .join(Document, MaterialClassificationCandidate.document_id == Document.id)
        .filter(Document.application_id == application_id)
        .all()
    )
    candidates_by_document: dict[str, list[ClassificationCandidateResponse]] = {}
    for candidate in sorted(candidate_rows, key=lambda item: (-item.confidence, item.category)):
        candidates_by_document.setdefault(candidate.document_id, []).append(
            ClassificationCandidateResponse(
                category=candidate.category,
                category_label=CATEGORY_LABELS.get(
                    MaterialCategory(candidate.category), candidate.category
                ),
                confidence=candidate.confidence,
                method=candidate.method,
                method_version=candidate.method_version,
            )
        )
    document_responses = [
        CompletenessDocumentResponse(
            id=document.id,
            filename=document.filename,
            confirmed_category=confirmed_category.get(document.id),
            classification_candidates=candidates_by_document.get(document.id, []),
            seal_confirmed=document.id in seal_present,
            signature_confirmed=document.id in signature_present,
        )
        for document in documents
    ]
    mappings_rows = (
        db.query(DocumentChecklistMapping)
        .filter_by(application_id=application_id)
        .order_by(DocumentChecklistMapping.created_at)
        .all()
    )
    waiver_rows = (
        db.query(WaiverRecord)
        .filter_by(application_id=application_id)
        .order_by(WaiverRecord.created_at)
        .all()
    )
    latest_run = (
        db.query(CompletenessRun)
        .filter_by(application_id=application_id)
        .order_by(CompletenessRun.created_at.desc())
        .first()
    )
    blocked = (
        "生产模式拒绝使用演示模板生成正式报告"
        if template.demo_only and request.app.state.production
        else None
    )
    return LiveDraftResponse(
        template=template_payload(template),
        no_template_reason=None,
        items=[CompletenessItemResponse(**item) for item in items],
        documents=document_responses,
        mappings=[as_mapping(mapping) for mapping in mappings_rows],
        waivers=[as_waiver(waiver) for waiver in waiver_rows],
        condition_context=context,
        latest_run=as_run_summary(latest_run, template) if latest_run else None,
        formal_run_blocked_reason=blocked,
    )


@router.post(
    "/{application_id}/documents/{document_id}/classification",
    response_model=CompletenessDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def confirm_classification(
    application_id: str,
    document_id: str,
    payload: ConfirmClassificationRequest,
    request: Request,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> CompletenessDocumentResponse:
    application = owned_application(db, application_id, user.id)
    require_mutable(db, application)
    document = document_in_application(db, application_id, document_id)
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    operation = f"confirm_classification:{document_id}"
    replay_id = replay_resource_id(db, user.id, operation, idempotency_key, request_hash)
    if replay_id:
        response.status_code = status.HTTP_200_OK
        confirmation = db.get(ClassificationConfirmation, replay_id)
        return _document_response(db, document, confirmation)
    confirmation = db.query(ClassificationConfirmation).filter_by(document_id=document_id).first()
    if confirmation:
        confirmation.category = payload.category.value
        confirmation.actor_id = user.id
    else:
        confirmation = ClassificationConfirmation(
            application_id=application_id,
            document_id=document_id,
            category=payload.category.value,
            actor_id=user.id,
        )
        db.add(confirmation)
    db.flush()
    add_idempotency_record(db, user.id, operation, idempotency_key, request_hash, confirmation.id)
    mark_runs_stale(db, application_id, "classification_change")
    record_audit(
        db,
        event_type=CLASSIFICATION_CONFIRMED,
        actor=user,
        resource_type="document",
        resource_id=document_id,
        correlation_id=request_correlation_id(request),
        metadata={"application_id": application_id, "category": confirmation.category},
    )
    db.commit()
    return _document_response(db, document, confirmation)


def _document_response(
    db: Db,
    document: Document,
    confirmation: ClassificationConfirmation | None,
) -> CompletenessDocumentResponse:
    candidates = (
        db.query(MaterialClassificationCandidate)
        .filter_by(document_id=document.id)
        .order_by(MaterialClassificationCandidate.confidence.desc())
        .all()
    )
    return CompletenessDocumentResponse(
        id=document.id,
        filename=document.filename,
        confirmed_category=confirmation.category if confirmation else None,
        classification_candidates=[
            ClassificationCandidateResponse(
                category=candidate.category,
                category_label=CATEGORY_LABELS.get(
                    MaterialCategory(candidate.category), candidate.category
                ),
                confidence=candidate.confidence,
                method=candidate.method,
                method_version=candidate.method_version,
            )
            for candidate in candidates
        ],
        seal_confirmed=document.id in seal_present_documents(db, document.application_id),
        signature_confirmed=document.id in signature_present_documents(db, document.application_id),
    )


@router.post(
    "/{application_id}/mappings",
    response_model=MappingResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_mapping(
    application_id: str,
    payload: CreateMappingRequest,
    request: Request,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> MappingResponse:
    application = owned_application(db, application_id, user.id)
    require_mutable(db, application)
    template = published_applicable_template(db, application)
    if template is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No published applicable template")
    document = document_in_application(db, application_id, payload.document_id)
    item = item_in_template(db, template, payload.item_id)
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    operation = f"create_mapping:{application_id}"
    replay_id = replay_resource_id(db, user.id, operation, idempotency_key, request_hash)
    if replay_id and db.get(DocumentChecklistMapping, replay_id):
        response.status_code = status.HTTP_200_OK
        return as_mapping(db.get(DocumentChecklistMapping, replay_id))
    existing = (
        db.query(DocumentChecklistMapping)
        .filter_by(
            application_id=application_id,
            document_id=document.id,
            item_id=item.id,
        )
        .first()
    )
    if existing:
        response.status_code = status.HTTP_200_OK
        return as_mapping(existing)
    mapping = DocumentChecklistMapping(
        application_id=application_id,
        document_id=document.id,
        item_id=item.id,
        actor_id=user.id,
    )
    db.add(mapping)
    db.flush()
    add_idempotency_record(db, user.id, operation, idempotency_key, request_hash, mapping.id)
    mark_runs_stale(db, application_id, "mapping_change")
    record_audit(
        db,
        event_type=MAPPING_CREATED,
        actor=user,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
        metadata={"mapping_id": mapping.id, "document_id": document.id, "item_id": item.id},
    )
    db.commit()
    return as_mapping(mapping)


@router.delete("/{application_id}/mappings/{mapping_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mapping(
    application_id: str,
    mapping_id: str,
    request: Request,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
) -> None:
    application = owned_application(db, application_id, user.id)
    require_mutable(db, application)
    mapping = (
        db.query(DocumentChecklistMapping)
        .filter_by(id=mapping_id, application_id=application_id)
        .first()
    )
    if not mapping:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mapping not found")
    db.delete(mapping)
    mark_runs_stale(db, application_id, "mapping_change")
    record_audit(
        db,
        event_type=MAPPING_DELETED,
        actor=user,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
        metadata={"mapping_id": mapping_id, "document_id": mapping.document_id},
    )
    db.commit()


@router.post(
    "/{application_id}/waivers",
    response_model=WaiverResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_waiver(
    application_id: str,
    payload: CreateWaiverRequest,
    request: Request,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> WaiverResponse:
    application = owned_application(db, application_id, user.id)
    require_mutable(db, application)
    template = published_applicable_template(db, application)
    if template is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No published applicable template")
    item = item_in_template(db, template, payload.item_id)
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    operation = f"create_waiver:{application_id}"
    replay_id = replay_resource_id(db, user.id, operation, idempotency_key, request_hash)
    if replay_id:
        response.status_code = status.HTTP_200_OK
        return as_waiver(db.get(WaiverRecord, replay_id))
    existing = (
        db.query(WaiverRecord).filter_by(application_id=application_id, item_id=item.id).first()
    )
    if existing:
        response.status_code = status.HTTP_200_OK
        return as_waiver(existing)
    waiver = WaiverRecord(
        application_id=application_id,
        item_id=item.id,
        reason=payload.reason.strip(),
        actor_id=user.id,
    )
    db.add(waiver)
    db.flush()
    add_idempotency_record(db, user.id, operation, idempotency_key, request_hash, waiver.id)
    mark_runs_stale(db, application_id, "waiver_change")
    record_audit(
        db,
        event_type=WAIVER_CREATED,
        actor=user,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
        metadata={"waiver_id": waiver.id, "item_id": item.id},
    )
    db.commit()
    return as_waiver(waiver)


class RunDetailResponse(BaseModel):
    id: str
    application_id: str
    template_snapshot: dict
    input_snapshot: dict
    result_snapshot: dict
    content_hash: str
    status: str
    stale: bool
    stale_reason: str | None
    actor_id: str
    created_at: datetime


def as_run_detail(
    run: CompletenessRun, current_template: CompletenessTemplate | None = None
) -> RunDetailResponse:
    stale, stale_reason = run_staleness(run, current_template)
    return RunDetailResponse(
        id=run.id,
        application_id=run.application_id,
        template_snapshot=run.template_snapshot,
        input_snapshot=run.input_snapshot,
        result_snapshot=run.result_snapshot,
        content_hash=run.content_hash,
        status=run.status,
        stale=stale,
        stale_reason=stale_reason,
        actor_id=run.actor_id,
        created_at=run.created_at,
    )


@router.post(
    "/{application_id}/completeness-runs",
    response_model=RunDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_completeness_run(
    application_id: str,
    request: Request,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> RunDetailResponse:
    application = owned_application(db, application_id, user.id)
    require_mutable(db, application)
    template = published_applicable_template(db, application)
    if template is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "No published applicable template; cannot create a formal report",
        )
    if request.app.state.production and template.demo_only:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Production mode rejects demo templates for formal reports",
        )
    request_hash = hashlib.sha256(f"{application_id}:{template.id}".encode()).hexdigest()
    operation = f"create_completeness_run:{application_id}"
    replay_id = replay_resource_id(db, user.id, operation, idempotency_key, request_hash)
    if replay_id:
        response.status_code = status.HTTP_200_OK
        return as_run_detail(db.get(CompletenessRun, replay_id))
    template_snapshot, input_snapshot, result_snapshot = build_run_snapshots(
        db, application, template, user.id
    )
    run = CompletenessRun(
        application_id=application_id,
        template_id=template.id,
        template_snapshot=template_snapshot,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        content_hash=run_content_hash(template_snapshot, input_snapshot, result_snapshot),
        status=RunStatus.CURRENT,
        actor_id=user.id,
    )
    mark_runs_stale(db, application_id, "new_run")
    db.add(run)
    db.flush()
    add_idempotency_record(db, user.id, operation, idempotency_key, request_hash, run.id)
    record_audit(
        db,
        event_type=COMPLETENESS_RUN_CREATED,
        actor=user,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
        metadata={
            "run_id": run.id,
            "template_id": template.id,
            "template_code": template.code,
            "template_version": template.version,
        },
    )
    db.commit()
    return as_run_detail(run)


@router.get("/{application_id}/completeness-runs", response_model=list[RunSummaryResponse])
def list_completeness_runs(
    application_id: str, db: Db, user: CurrentUser
) -> list[RunSummaryResponse]:
    application = owned_application(db, application_id, user.id)
    template = published_applicable_template(db, application)
    runs = (
        db.query(CompletenessRun)
        .filter_by(application_id=application_id)
        .order_by(CompletenessRun.created_at.desc())
        .all()
    )
    return [as_run_summary(run, template) for run in runs]


def owned_run(db: Db, application_id: str, run_id: str) -> CompletenessRun:
    run = db.query(CompletenessRun).filter_by(id=run_id, application_id=application_id).first()
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Completeness run not found")
    return run


@router.get("/{application_id}/completeness-runs/{run_id}", response_model=RunDetailResponse)
def get_completeness_run(
    application_id: str, run_id: str, db: Db, user: CurrentUser
) -> RunDetailResponse:
    application = owned_application(db, application_id, user.id)
    template = published_applicable_template(db, application)
    return as_run_detail(owned_run(db, application_id, run_id), template)


@router.get("/{application_id}/completeness-runs/{run_id}/printable")
def printable_completeness_run(
    application_id: str, run_id: str, db: Db, user: CurrentUser
) -> Response:
    owned_application(db, application_id, user.id)
    run = owned_run(db, application_id, run_id)
    actor = db.get(User, run.actor_id)
    html = render_printable_html(run, actor.username if actor else run.actor_id)
    return Response(content=html, media_type="text/html; charset=utf-8")
