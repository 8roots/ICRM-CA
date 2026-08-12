import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Header, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.dependencies import Csrf, CurrentUser, Db
from app.idempotency import add_idempotency_record, replay_resource_id
from app.material_formats import FORMATS, MANUAL_EXTENSIONS
from app.models import (
    Application,
    Document,
    DocumentJob,
    JobStatus,
    ProcessingStep,
    ProcessingStepName,
    ReviewStatus,
)

router = APIRouter(tags=["documents"])

PROCESSING_STEPS = tuple(ProcessingStepName)


@dataclass
class DocumentLimits:
    max_material_bytes: int
    max_application_bytes: int
    max_application_materials: int


class StepResponse(BaseModel):
    name: str
    status: str
    error_code: str | None


class JobResponse(BaseModel):
    id: str
    document_id: str
    status: str
    attempts: int
    error_code: str | None
    retry_reason: str | None
    steps: list[StepResponse]


class DocumentResponse(BaseModel):
    id: str
    application_id: str
    filename: str
    declared_mime: str
    size_bytes: int
    sha256: str
    processing_status: str
    review_status: str
    jobs: list[JobResponse]


class UploadResponse(BaseModel):
    document: DocumentResponse
    job: JobResponse


class UploadLimitExceeded(Exception):
    pass


class HashingLimitedReader:
    def __init__(self, stream, limit: int) -> None:
        self.stream = stream
        self.limit = limit
        self.size = 0
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self.stream.read(size)
        self.size += len(chunk)
        self.digest.update(chunk)
        if self.size > self.limit:
            raise UploadLimitExceeded
        return chunk


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000)
    selected_steps: list[ProcessingStepName] = Field(min_length=1)


def upload_request_hash(sha256: str, filename: str, mime: str) -> str:
    return hashlib.sha256(f"{sha256}\0{filename}\0{mime}".encode()).hexdigest()


def as_job(job: DocumentJob) -> JobResponse:
    return JobResponse(
        id=job.id,
        document_id=job.document_id,
        status=job.status,
        attempts=job.attempts,
        error_code=job.error_code,
        retry_reason=job.retry_reason,
        steps=[
            StepResponse(name=s.name, status=s.status, error_code=s.error_code) for s in job.steps
        ],
    )


def as_document(document: Document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        application_id=document.application_id,
        filename=document.filename,
        declared_mime=document.declared_mime,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        processing_status=document.processing_status,
        review_status=document.review_status,
        jobs=[as_job(job) for job in document.jobs],
    )


def owned_application(
    db: Db, application_id: str, owner_id: str, *, lock: bool = False
) -> Application:
    query = db.query(Application).filter_by(id=application_id, owner_id=owner_id)
    application = query.with_for_update().first() if lock else query.first()
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


def existing_upload(
    db: Db,
    application_id: str,
    actor_id: str,
    operation: str,
    idempotency_key: str,
    request_hash: str,
    sha256: str,
) -> Document | None:
    replay_id = replay_resource_id(db, actor_id, operation, idempotency_key, request_hash)
    if replay_id:
        return db.get(Document, replay_id)
    duplicate = db.query(Document).filter_by(application_id=application_id, sha256=sha256).first()
    if duplicate:
        add_idempotency_record(
            db, actor_id, operation, idempotency_key, request_hash, duplicate.id
        )
    return duplicate


def enforce_application_capacity(
    db: Db, application_id: str, limits: DocumentLimits, incoming_size: int
) -> None:
    count, total = (
        db.query(func.count(Document.id), func.coalesce(func.sum(Document.size_bytes), 0))
        .filter_by(application_id=application_id)
        .one()
    )
    if count >= limits.max_application_materials:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Application material count limit exceeded",
        )
    if total + incoming_size > limits.max_application_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "Application material size limit exceeded",
        )


def document_job(document: Document, job_status: JobStatus, error_code: str | None) -> DocumentJob:
    job = DocumentJob(document=document, status=job_status, error_code=error_code)
    for step_name in PROCESSING_STEPS:
        is_validation = step_name == ProcessingStepName.VALIDATION
        job.steps.append(
            ProcessingStep(
                name=step_name,
                status=job_status if is_validation else JobStatus.NOT_APPLICABLE,
                error_code=error_code if is_validation else None,
            )
        )
    return job


def update_application_lifecycle(db: Db, application: Application) -> None:
    has_active_job = (
        db.query(DocumentJob.id)
        .join(Document)
        .filter(
            Document.application_id == application.id,
            DocumentJob.status.in_([JobStatus.WAITING, JobStatus.RUNNING]),
        )
        .first()
    )
    application.lifecycle_state = "processing" if has_active_job else "pending_review"


@router.post(
    "/applications/{application_id}/documents",
    response_model=UploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_document(
    application_id: str,
    response: Response,
    request: Request,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    file: Annotated[UploadFile, File()],
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> UploadResponse:
    owned_application(db, application_id, user.id)
    limits = request.app.state.document_limits
    extension = Path(file.filename or "material").suffix.lower()
    object_key = f"{application_id}/{uuid.uuid4()}"
    stream = HashingLimitedReader(
        file.file,
        limits.max_material_bytes,
    )
    try:
        request.app.state.object_store.put(object_key, stream, -1)
    except UploadLimitExceeded:
        while chunk := file.file.read(1024 * 1024):
            stream.size += len(chunk)
            stream.digest.update(chunk)
        request.app.state.object_store.delete(object_key)
        filename = (file.filename or "material")[:255]
        mime = file.content_type or "application/octet-stream"
        sha256 = stream.digest.hexdigest()
        request_hash = upload_request_hash(sha256, filename, mime)
        operation = f"upload_document:{application_id}"
        application = owned_application(db, application_id, user.id, lock=True)
        existing = existing_upload(
            db,
            application_id,
            user.id,
            operation,
            idempotency_key,
            request_hash,
            sha256,
        )
        if existing:
            db.commit()
            response.status_code = status.HTTP_200_OK
            return UploadResponse(document=as_document(existing), job=as_job(existing.jobs[-1]))
        enforce_application_capacity(db, application_id, limits, stream.size)
        document = Document(
            application_id=application_id,
            filename=filename,
            extension=extension,
            declared_mime=mime,
            size_bytes=stream.size,
            sha256=sha256,
            object_key=object_key,
            processing_status=JobStatus.FAILED,
            review_status=ReviewStatus.PENDING_REVIEW,
        )
        job = document_job(document, JobStatus.FAILED, "material_size_limit_exceeded")
        db.add(document)
        db.flush()
        add_idempotency_record(
            db,
            user.id,
            operation,
            idempotency_key,
            request_hash,
            document.id,
        )
        update_application_lifecycle(db, application)
        db.commit()
        return UploadResponse(document=as_document(document), job=as_job(job))
    try:
        size = stream.size
        sha256 = stream.digest.hexdigest()
        filename = (file.filename or "material")[:255]
        mime = file.content_type or "application/octet-stream"
        request_hash = upload_request_hash(sha256, filename, mime)
        operation = f"upload_document:{application_id}"
        application = owned_application(db, application_id, user.id, lock=True)
        existing = existing_upload(
            db,
            application_id,
            user.id,
            operation,
            idempotency_key,
            request_hash,
            sha256,
        )
        if existing:
            request.app.state.object_store.delete(object_key)
            db.commit()
            response.status_code = status.HTTP_200_OK
            return UploadResponse(document=as_document(existing), job=as_job(existing.jobs[-1]))
        enforce_application_capacity(db, application_id, limits, size)

        document = Document(
            application_id=application_id,
            filename=filename,
            extension=extension,
            declared_mime=mime,
            size_bytes=size,
            sha256=sha256,
            object_key=object_key,
        )
        error_code = MANUAL_EXTENSIONS.get(extension)
        if extension not in FORMATS and not error_code:
            error_code = "unsupported_format"
        initial_status = JobStatus.MANUAL_HANDLING if error_code else JobStatus.WAITING
        document.processing_status = initial_status
        document.review_status = (
            ReviewStatus.PENDING_REVIEW if error_code else ReviewStatus.NOT_READY
        )
        job = document_job(document, initial_status, error_code)
        db.add(document)
        db.flush()
        add_idempotency_record(
            db,
            user.id,
            operation,
            idempotency_key,
            request_hash,
            document.id,
        )
        update_application_lifecycle(db, application)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            request.app.state.object_store.delete(object_key)
            replay_id = replay_resource_id(
                db, user.id, operation, idempotency_key, request_hash
            )
            replay = db.get(Document, replay_id) if replay_id else None
            if replay:
                response.status_code = status.HTTP_200_OK
                return UploadResponse(document=as_document(replay), job=as_job(replay.jobs[-1]))
            winner = (
                db.query(Document).filter_by(application_id=application_id, sha256=sha256).one()
            )
            add_idempotency_record(
                db,
                user.id,
                operation,
                idempotency_key,
                request_hash,
                winner.id,
            )
            db.commit()
            response.status_code = status.HTTP_200_OK
            return UploadResponse(document=as_document(winner), job=as_job(winner.jobs[-1]))
        return UploadResponse(document=as_document(document), job=as_job(job))
    except Exception:
        db.rollback()
        request.app.state.object_store.delete(object_key)
        raise


@router.get("/applications/{application_id}/documents", response_model=list[DocumentResponse])
def list_documents(application_id: str, db: Db, user: CurrentUser) -> list[DocumentResponse]:
    owned_application(db, application_id, user.id)
    documents = (
        db.query(Document)
        .filter_by(application_id=application_id)
        .order_by(Document.created_at)
        .all()
    )
    return [as_document(document) for document in documents]


@router.get("/documents/{document_id}/jobs", response_model=list[JobResponse])
def list_jobs(document_id: str, db: Db, user: CurrentUser) -> list[JobResponse]:
    document = (
        db.query(Document)
        .join(Application)
        .filter(Document.id == document_id, Application.owner_id == user.id)
        .first()
    )
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return [as_job(job) for job in document.jobs]


@router.post("/jobs/{job_id}/retry", response_model=JobResponse)
def retry_job(
    job_id: str,
    payload: RetryRequest,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> JobResponse:
    job = (
        db.query(DocumentJob)
        .join(Document)
        .join(Application)
        .filter(DocumentJob.id == job_id, Application.owner_id == user.id)
        .first()
    )
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    replay_id = replay_resource_id(
        db, user.id, f"retry_job:{job_id}", idempotency_key, request_hash
    )
    if replay_id:
        return as_job(job)
    if job.status not in {JobStatus.FAILED, JobStatus.MANUAL_HANDLING}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed jobs can be retried")
    selected = {step.value for step in payload.selected_steps}
    failed_steps = {
        step.name
        for step in job.steps
        if step.status in {JobStatus.FAILED, JobStatus.MANUAL_HANDLING}
    }
    if not selected <= failed_steps:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed steps can be retried")
    job.status = JobStatus.WAITING
    job.error_code = None
    job.retry_reason = payload.reason
    job.attempts = 0
    for step in job.steps:
        if step.name in selected:
            step.status = JobStatus.WAITING
            step.error_code = None
    job.document.processing_status = JobStatus.WAITING
    job.document.review_status = ReviewStatus.NOT_READY
    application = db.get(Application, job.document.application_id)
    application.lifecycle_state = "processing"
    add_idempotency_record(
        db,
        user.id,
        f"retry_job:{job_id}",
        idempotency_key,
        request_hash,
        job.document_id,
    )
    db.commit()
    return as_job(job)
