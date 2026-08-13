import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import Database
from app.models import (
    Application,
    Document,
    DocumentJob,
    JobStatus,
    ReviewStatus,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def claim_next_job(db: Session, worker_id: str) -> DocumentJob | None:
    job = db.scalar(
        select(DocumentJob)
        .where(DocumentJob.status == JobStatus.WAITING, DocumentJob.available_at <= utcnow())
        .order_by(DocumentJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not job:
        return None
    job.status = JobStatus.RUNNING
    job.claimed_by = worker_id
    job.claimed_at = utcnow()
    job.claim_token = str(uuid.uuid4())
    job.attempts += 1
    job.document.processing_status = JobStatus.RUNNING
    for step in job.steps:
        if step.status == JobStatus.WAITING:
            step.status = JobStatus.RUNNING
            step.started_at = utcnow()
            step.error_code = None
    db.commit()
    return job


def renew_claim(database: Database, job_id: str, claim_token: str) -> bool:
    with database.session() as db:
        result = db.execute(
            update(DocumentJob)
            .where(
                DocumentJob.id == job_id,
                DocumentJob.status == JobStatus.RUNNING,
                DocumentJob.claim_token == claim_token,
            )
            .values(claimed_at=utcnow())
        )
        db.commit()
        return result.rowcount == 1


def recover_stale_jobs(db: Session, stale_after: timedelta) -> int:
    cutoff = utcnow() - stale_after
    jobs = db.scalars(
        select(DocumentJob)
        .where(DocumentJob.status == JobStatus.RUNNING, DocumentJob.claimed_at < cutoff)
        .with_for_update(skip_locked=True)
    ).all()
    for job in jobs:
        exhausted = job.attempts >= 3
        job.status = JobStatus.FAILED if exhausted else JobStatus.WAITING
        job.error_code = "worker_crash_attempts_exhausted" if exhausted else None
        if not exhausted:
            job.available_at = utcnow() + timedelta(seconds=2 ** (job.attempts - 1))
        job.claimed_by = None
        job.claimed_at = None
        job.claim_token = None
        for step in job.steps:
            if step.status == JobStatus.RUNNING:
                step.status = JobStatus.FAILED if exhausted else JobStatus.WAITING
                step.error_code = job.error_code
        job.document.processing_status = job.status
    db.commit()
    return len(jobs)


def finish_job(
    db: Session,
    job: DocumentJob,
    outcome: JobStatus,
    *,
    claim_token: str | None = None,
    error_code: str | None = None,
    transient: bool = False,
) -> bool:
    token = claim_token or job.claim_token
    db.refresh(job)
    if not token or job.status != JobStatus.RUNNING or job.claim_token != token:
        return False
    now = utcnow()
    retry = transient and job.attempts < 3
    job.status = JobStatus.WAITING if retry else outcome
    job.error_code = error_code
    job.claimed_by = None
    job.claimed_at = None
    job.claim_token = None
    if retry:
        job.available_at = now + timedelta(seconds=2 ** (job.attempts - 1))
    for step in job.steps:
        if step.status == JobStatus.RUNNING:
            step.status = JobStatus.WAITING if retry else outcome
            step.error_code = error_code
            if not retry:
                step.finished_at = now
    job.document.processing_status = job.status
    job.document.review_status = (
        ReviewStatus.PENDING_REVIEW
        if outcome in {JobStatus.SUCCESS, JobStatus.PARTIAL_SUCCESS, JobStatus.MANUAL_HANDLING}
        else ReviewStatus.NOT_READY
    )
    db.flush()
    has_active_jobs = db.scalar(
        select(DocumentJob.id)
        .join(Document)
        .where(
            Document.application_id == job.document.application_id,
            DocumentJob.status.in_([JobStatus.WAITING, JobStatus.RUNNING]),
        )
        .limit(1)
    )
    if not has_active_jobs:
        application = db.get(Application, job.document.application_id)
        application.lifecycle_state = "pending_review"
    db.commit()
    return True
