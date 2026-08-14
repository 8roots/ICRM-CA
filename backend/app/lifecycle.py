"""Application lifecycle state machine and ownership/permissions.

States: draft → processing → pending_review → review_complete → archived.

- ``processing``/``pending_review`` are computed from running jobs.
- ``review_complete`` requires no running jobs and a non-stale latest formal
  redline and completeness report; visible gaps (missing materials, risk
  flags, insufficient data) do not block completion.
- Reopening (from review_complete or archived) always requires a reason.
- Archiving is the normal-user end state; only an administrator may hard
  delete, in two phases (reason, then confirmation token).
- Reassignment is metadata-only: the admin never gains material access, and
  the previous owner's access ends immediately because every officer query is
  scoped by ``owner_id``.
"""

import hmac
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import (
    APPLICATION_ARCHIVED,
    APPLICATION_COMPLETED,
    APPLICATION_HARD_DELETE_REQUESTED,
    APPLICATION_HARD_DELETED,
    APPLICATION_REASSIGNED,
    APPLICATION_REOPENED,
    record_audit,
    request_correlation_id,
)
from app.completeness_api import published_applicable_template
from app.completeness_api import run_staleness as completeness_staleness
from app.dependencies import Administrator, Csrf, CurrentUser, Db
from app.lifecycle_guard import MUTABLE_STATES, has_active_jobs
from app.models import (
    Application,
    ApplicationTombstone,
    CompletenessRun,
    Document,
    HardDeleteRequest,
    LifecycleState,
    RedlineRun,
    User,
)
from app.redline_api import run_staleness as redline_staleness
from app.security import random_token, token_hash

router = APIRouter(tags=["lifecycle"])

HARD_DELETE_TOKEN_MINUTES = 10


def completion_blockers(db: Session, application: Application) -> list[str]:
    """Why the application cannot yet be marked 辅助审查完成."""
    blockers: list[str] = []
    if has_active_jobs(db, application.id):
        blockers.append("running_jobs")
    latest_redline = (
        db.query(RedlineRun)
        .filter_by(application_id=application.id)
        .order_by(RedlineRun.created_at.desc())
        .first()
    )
    if latest_redline is None:
        blockers.append("missing_redline_report")
    else:
        stale, _ = redline_staleness(latest_redline, db, application)
        if stale:
            blockers.append("stale_redline_report")
    latest_completeness = (
        db.query(CompletenessRun)
        .filter_by(application_id=application.id)
        .order_by(CompletenessRun.created_at.desc())
        .first()
    )
    if latest_completeness is None:
        blockers.append("missing_completeness_report")
    else:
        template = published_applicable_template(db, application)
        stale, _ = completeness_staleness(latest_completeness, template)
        if stale:
            blockers.append("stale_completeness_report")
    return blockers


class LifecycleResponse(BaseModel):
    state: str
    version: int
    editable: bool
    can_complete: bool
    can_archive: bool
    can_reopen: bool
    completion_blockers: list[str]


class StateChangeRequest(BaseModel):
    version: int = Field(ge=1)


class ReopenRequest(BaseModel):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class ReassignRequest(BaseModel):
    version: int = Field(ge=1)
    owner_id: str = Field(min_length=1, max_length=36)


class HardDeleteRequestPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class HardDeleteConfirmation(BaseModel):
    confirmation_token: str = Field(min_length=1, max_length=100)


class HardDeleteRequestResponse(BaseModel):
    confirmation_token: str
    expires_at: datetime


def owned_application(
    db: Db, application_id: str, owner_id: str, *, lock: bool = False
) -> Application:
    query = db.query(Application).filter_by(id=application_id, owner_id=owner_id)
    application = query.with_for_update().first() if lock else query.first()
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


def bump_version(db: Session, application: Application) -> Application:
    application.version += 1
    return application


@router.get("/applications/{application_id}/lifecycle", response_model=LifecycleResponse)
def get_lifecycle(application_id: str, db: Db, user: CurrentUser) -> LifecycleResponse:
    if user.role == "administrator":
        application = db.get(Application, application_id)
        if not application:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    else:
        application = owned_application(db, application_id, user.id)
    state = LifecycleState(application.lifecycle_state)
    blockers = completion_blockers(db, application)
    return LifecycleResponse(
        state=state.value,
        version=application.version,
        editable=state in MUTABLE_STATES,
        can_complete=state == LifecycleState.PENDING_REVIEW,
        can_archive=state
        in {
            LifecycleState.DRAFT,
            LifecycleState.PENDING_REVIEW,
            LifecycleState.REVIEW_COMPLETE,
        },
        can_reopen=state in {LifecycleState.REVIEW_COMPLETE, LifecycleState.ARCHIVED},
        completion_blockers=blockers,
    )


@router.post("/applications/{application_id}/complete", response_model=LifecycleResponse)
def complete_application(
    application_id: str,
    payload: StateChangeRequest,
    request: Request,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
) -> LifecycleResponse:
    application = owned_application(db, application_id, user.id, lock=True)
    if application.version != payload.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "Stale version")
    state = LifecycleState(application.lifecycle_state)
    if state != LifecycleState.PENDING_REVIEW:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Only pending_review applications can be completed, not {state.value}"
        )
    blockers = completion_blockers(db, application)
    if blockers:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Application cannot be completed: " + ", ".join(blockers),
        )
    application.lifecycle_state = LifecycleState.REVIEW_COMPLETE
    application.completed_at = datetime.now(UTC)
    bump_version(db, application)
    record_audit(
        db,
        event_type=APPLICATION_COMPLETED,
        actor=user,
        resource_type="application",
        resource_id=application.id,
        correlation_id=request_correlation_id(request),
    )
    db.commit()
    return get_lifecycle(application_id, db, user)


@router.post("/applications/{application_id}/reopen", response_model=LifecycleResponse)
def reopen_application(
    application_id: str,
    payload: ReopenRequest,
    request: Request,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
) -> LifecycleResponse:
    application = owned_application(db, application_id, user.id, lock=True)
    if application.version != payload.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "Stale version")
    state = LifecycleState(application.lifecycle_state)
    if state not in {LifecycleState.REVIEW_COMPLETE, LifecycleState.ARCHIVED}:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Application in {state.value} cannot be reopened"
        )
    application.lifecycle_state = LifecycleState.PENDING_REVIEW
    application.completed_at = None
    application.archived_at = None
    bump_version(db, application)
    record_audit(
        db,
        event_type=APPLICATION_REOPENED,
        actor=user,
        resource_type="application",
        resource_id=application.id,
        correlation_id=request_correlation_id(request),
        metadata={"reason": payload.reason},
    )
    db.commit()
    return get_lifecycle(application_id, db, user)


@router.post("/applications/{application_id}/archive", response_model=LifecycleResponse)
def archive_application(
    application_id: str,
    payload: StateChangeRequest,
    request: Request,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
) -> LifecycleResponse:
    application = owned_application(db, application_id, user.id, lock=True)
    if application.version != payload.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "Stale version")
    state = LifecycleState(application.lifecycle_state)
    if state == LifecycleState.PROCESSING or has_active_jobs(db, application.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Application cannot be archived while jobs are running"
        )
    if state == LifecycleState.ARCHIVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Application is already archived")
    application.lifecycle_state = LifecycleState.ARCHIVED
    application.archived_at = datetime.now(UTC)
    bump_version(db, application)
    record_audit(
        db,
        event_type=APPLICATION_ARCHIVED,
        actor=user,
        resource_type="application",
        resource_id=application.id,
        correlation_id=request_correlation_id(request),
    )
    db.commit()
    return get_lifecycle(application_id, db, user)


@router.post("/applications/{application_id}/reassign", response_model=LifecycleResponse)
def reassign_application(
    application_id: str,
    payload: ReassignRequest,
    request: Request,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> LifecycleResponse:
    """Admin metadata-only reassignment; the admin gains no material access.

    The previous owner loses access immediately because every officer query
    filters by ``owner_id``.
    """
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    if application.version != payload.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "Stale version")
    new_owner = db.get(User, payload.owner_id)
    if not new_owner or new_owner.role != "approval_officer":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Owner must be an approval officer"
        )
    old_owner = db.get(User, application.owner_id)
    application.owner_id = new_owner.id
    bump_version(db, application)
    record_audit(
        db,
        event_type=APPLICATION_REASSIGNED,
        actor=admin,
        resource_type="application",
        resource_id=application.id,
        correlation_id=request_correlation_id(request),
        metadata={
            "from_owner_id": old_owner.id if old_owner else None,
            "from_owner_username": old_owner.username if old_owner else None,
            "to_owner_id": new_owner.id,
            "to_owner_username": new_owner.username,
        },
    )
    db.commit()
    return get_lifecycle(application_id, db, admin)


@router.post(
    "/applications/{application_id}/hard-delete-requests",
    response_model=HardDeleteRequestResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_hard_delete(
    application_id: str,
    payload: HardDeleteRequestPayload,
    request: Request,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> HardDeleteRequestResponse:
    """Phase one: record the reason and issue a short-lived confirmation token."""
    if not db.get(Application, application_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    token = random_token()
    hard_delete_request = HardDeleteRequest(
        application_id=application_id,
        token_hash=token_hash(token),
        reason=payload.reason,
        actor_id=admin.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=HARD_DELETE_TOKEN_MINUTES),
    )
    db.add(hard_delete_request)
    db.flush()
    record_audit(
        db,
        event_type=APPLICATION_HARD_DELETE_REQUESTED,
        actor=admin,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
        metadata={"reason": payload.reason},
    )
    db.commit()
    return HardDeleteRequestResponse(
        confirmation_token=token,
        expires_at=hard_delete_request.expires_at,
    )


@router.post(
    "/applications/{application_id}/hard-delete",
    status_code=status.HTTP_204_NO_CONTENT,
)
def confirm_hard_delete(
    application_id: str,
    payload: HardDeleteConfirmation,
    request: Request,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> None:
    """Phase two: with the confirmation token, delete originals + derivatives.

    The database rows are removed first (cascading over outputs, candidates,
    resolutions, runs, confirmations, and restricted cloud audit). Original
    material objects are then removed from MinIO; any object that cannot be
    removed is recorded on the tombstone so the partial failure stays visible
    and recoverable. Only the non-sensitive tombstone row remains.
    """
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    hard_delete_request = (
        db.query(HardDeleteRequest)
        .filter_by(application_id=application_id)
        .order_by(HardDeleteRequest.created_at.desc())
        .first()
    )
    if not hard_delete_request:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No hard-delete request exists; request one with a reason first",
        )
    if not _constant_time_equals(hard_delete_request.token_hash, payload.confirmation_token):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid confirmation token")
    expires_at = hard_delete_request.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC):
        raise HTTPException(status.HTTP_409_CONFLICT, "Confirmation token expired; request again")
    if hard_delete_request.actor_id != admin.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Confirmation token belongs to another admin"
        )

    object_keys = [
        key
        for (key,) in db.query(Document.object_key)
        .filter_by(application_id=application_id)
        .all()
    ]
    db.delete(application)  # cascades over all sensitive derivatives
    # The tombstone is created in the same transaction as the row deletion and
    # initially lists every original object as pending, so any interruption
    # (crash, object-store outage) leaves a visible, recoverable record of
    # what still needs purging. MinIO objects are removed after commit.
    db.add(
        ApplicationTombstone(
            id=application_id,
            deleted_by=admin.id,
            deleted_at=datetime.now(UTC),
            reason=hard_delete_request.reason,
            remaining_object_keys=object_keys,
        )
    )
    record_audit(
        db,
        event_type=APPLICATION_HARD_DELETED,
        actor=admin,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
        metadata={
            "reason": hard_delete_request.reason,
            "remaining_object_keys": object_keys,
        },
    )
    db.commit()

    # Best-effort original purge; failures are recorded on the tombstone so
    # they stay visible and can be recovered by an operator.
    remaining: list[str] = []
    objects = request.app.state.object_store
    for key in object_keys:
        try:
            objects.delete(key)
        except Exception:
            remaining.append(key)
    if remaining != object_keys:
        with request.app.state.database.session() as db:
            tombstone = db.get(ApplicationTombstone, application_id)
            if tombstone:
                tombstone.remaining_object_keys = remaining
            db.commit()


def _constant_time_equals(expected_hash: str, token: str) -> bool:
    return hmac.compare_digest(expected_hash, token_hash(token))
