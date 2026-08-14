"""Lifecycle mutability guard shared by every input-mutating endpoint.

Kept free of imports from the completeness/redline API modules so those
modules can use it without a circular import. The full state machine lives in
``app.lifecycle``.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Document, DocumentJob, JobStatus, LifecycleState

MUTABLE_STATES = {
    LifecycleState.DRAFT,
    LifecycleState.PROCESSING,
    LifecycleState.PENDING_REVIEW,
}


def require_mutable(db: Session, application: Application) -> Application:
    """Reject input mutations outside draft/processing/pending_review."""
    if LifecycleState(application.lifecycle_state) not in MUTABLE_STATES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Application is {application.lifecycle_state} and cannot be modified",
        )
    return application


def has_active_jobs(db: Session, application_id: str) -> bool:
    return (
        db.scalar(
            select(DocumentJob.id)
            .join(Document)
            .where(
                Document.application_id == application_id,
                DocumentJob.status.in_([JobStatus.WAITING, JobStatus.RUNNING]),
            )
            .limit(1)
        )
        is not None
    )
