"""Append-only business audit trail.

``record_audit`` is the single write path; every call inserts a row and no
API in this module (or anywhere else) mutates or deletes existing events.
Event content is restricted to non-sensitive metadata. Restricted cloud
audit content (redacted requests/responses) stays in ``CloudExtractionCall``
and is served only by the separately authorized cloud-calls endpoint.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.dependencies import Administrator, CurrentUser, Db
from app.models import Application, AuditEvent, Document, User

router = APIRouter(tags=["audit"])

# Event type vocabulary (snake_case, dot-separated).
AUTH_LOGIN = "auth.login"
AUTH_LOGIN_FAILED = "auth.login_failed"
AUTH_LOGOUT = "auth.logout"
APPLICATION_CREATED = "application.created"
APPLICATION_UPDATED = "application.updated"
APPLICATION_COMPLETED = "application.completed"
APPLICATION_REOPENED = "application.reopened"
APPLICATION_ARCHIVED = "application.archived"
APPLICATION_REASSIGNED = "application.reassigned"
APPLICATION_HARD_DELETE_REQUESTED = "application.hard_delete_requested"
APPLICATION_HARD_DELETED = "application.hard_deleted"
DOCUMENT_UPLOADED = "document.uploaded"
DOCUMENT_DOWNLOADED = "document.downloaded"
DOCUMENT_VIEWED = "document.viewed"
RESOLUTION_CREATED = "resolution.created"
EVIDENCE_REVIEW_CREATED = "evidence_review.created"
RULE_CONTEXT_CONFIRMED = "rule_context.confirmed"
CLASSIFICATION_CONFIRMED = "classification.confirmed"
MAPPING_CREATED = "mapping.created"
MAPPING_DELETED = "mapping.deleted"
WAIVER_CREATED = "waiver.created"
COMPLETENESS_RUN_CREATED = "completeness.run_created"
REDLINE_RUN_CREATED = "redline.run_created"
TEMPLATE_PUBLISHED = "template.published"
TEMPLATE_RETIRED = "template.retired"
RULE_APPROVED = "rule.approved"
RULE_RETIRED = "rule.retired"
LPR_PUBLISHED = "lpr.published"
CLOUD_CALL = "cloud.call_recorded"
JOB_RETRIED = "job.retried"
APPLICATION_VIEWED = "application.viewed"
DOCUMENTS_VIEWED = "documents.viewed"
CANDIDATES_VIEWED = "candidates.viewed"
RESOLUTIONS_VIEWED = "resolutions.viewed"
COMPLETENESS_VIEWED = "completeness.viewed"
REDLINE_VIEWED = "redline.viewed"


def request_correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def record_audit(
    db: Session,
    *,
    event_type: str,
    actor: User | None = None,
    actor_username: str | None = None,
    resource_type: str,
    resource_id: str | None = None,
    correlation_id: str | None = None,
    metadata: dict | None = None,
    dedupe_minutes: int = 0,
) -> AuditEvent:
    """Insert one append-only audit event.

    With ``dedupe_minutes`` > 0, an identical event (same actor, type, and
    resource) within the window is skipped. This keeps high-frequency view
    events meaningful without flooding the trail from polling clients.
    """
    if dedupe_minutes > 0 and actor is not None:
        cutoff = datetime.now(UTC) - timedelta(minutes=dedupe_minutes)
        recent = (
            db.query(AuditEvent.id)
            .filter(
                AuditEvent.actor_id == actor.id,
                AuditEvent.event_type == event_type,
                AuditEvent.resource_type == resource_type,
                AuditEvent.resource_id == resource_id,
                AuditEvent.created_at >= cutoff,
            )
            .first()
        )
        if recent:
            return db.get(AuditEvent, recent.id)
    event = AuditEvent(
        event_type=event_type,
        actor_id=actor.id if actor else None,
        actor_username=actor.username if actor else actor_username,
        resource_type=resource_type,
        resource_id=resource_id,
        correlation_id=correlation_id,
        details=metadata or {},
    )
    db.add(event)
    db.flush()
    return event


class AuditEventResponse(BaseModel):
    id: str
    event_type: str
    actor_id: str | None
    actor_username: str | None
    resource_type: str
    resource_id: str | None
    correlation_id: str | None
    metadata: dict
    created_at: datetime


def as_event(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        id=event.id,
        event_type=event.event_type,
        actor_id=event.actor_id,
        actor_username=event.actor_username,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        correlation_id=event.correlation_id,
        metadata=event.details,
        created_at=event.created_at,
    )


@router.get("/audit/events", response_model=list[AuditEventResponse])
def list_audit_events(
    db: Db,
    admin: Administrator,
    event_type: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AuditEventResponse]:
    query = db.query(AuditEvent)
    if event_type:
        query = query.filter(AuditEvent.event_type == event_type)
    if resource_type:
        query = query.filter(AuditEvent.resource_type == resource_type)
    if resource_id:
        query = query.filter(AuditEvent.resource_id == resource_id)
    if actor_id:
        query = query.filter(AuditEvent.actor_id == actor_id)
    events = (
        query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [as_event(event) for event in events]


@router.get("/applications/{application_id}/audit-events", response_model=list[AuditEventResponse])
def list_application_audit_events(
    application_id: str,
    db: Db,
    user: CurrentUser,
) -> list[AuditEventResponse]:
    """Application-scoped audit for the owner; administrators see everything."""
    if user.role != "administrator":
        application = db.query(Application).filter_by(id=application_id, owner_id=user.id).first()
        if not application:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    document_ids = [
        document.id
        for document in db.query(Document.id).filter_by(application_id=application_id).all()
    ]
    resource_ids = [application_id, *document_ids]
    events = (
        db.query(AuditEvent)
        .filter(
            AuditEvent.resource_id.in_(resource_ids),
            or_(AuditEvent.resource_type == "application", AuditEvent.resource_type == "document"),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(500)
        .all()
    )
    return [as_event(event) for event in events]
