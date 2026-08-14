import hashlib
from datetime import date
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.audit import (
    APPLICATION_CREATED,
    APPLICATION_UPDATED,
    APPLICATION_VIEWED,
    record_audit,
    request_correlation_id,
)
from app.dependencies import Csrf, CurrentUser, Db
from app.lifecycle_guard import require_mutable
from app.models import Application, IdempotencyRecord, User
from app.redline import mark_runs_stale as mark_redline_runs_stale

router = APIRouter(prefix="/applications", tags=["applications"])


class PrimaryBorrower(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["corporate", "individual"]
    name: str = Field(min_length=1, max_length=200)


class ApplicationFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_borrower: PrimaryBorrower
    product: str = Field(min_length=1, max_length=100)
    application_date: date
    proposed_signing_date: date | None = None


class UpdateApplicationRequest(ApplicationFields):
    version: int = Field(ge=1)


class ApplicationResponse(ApplicationFields):
    id: str
    owner_id: str
    lifecycle_state: str
    version: int


def as_response(application: Application) -> ApplicationResponse:
    return ApplicationResponse(
        id=application.id,
        primary_borrower=PrimaryBorrower(
            type=application.borrower_type,
            name=application.borrower_name,
        ),
        product=application.product,
        application_date=application.application_date,
        proposed_signing_date=application.proposed_signing_date,
        owner_id=application.owner_id,
        lifecycle_state=application.lifecycle_state,
        version=application.version,
    )


def require_officer(user: User) -> None:
    if user.role != "approval_officer":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Approval officer role required")


@router.get("", response_model=list[ApplicationResponse])
def list_applications(db: Db, user: CurrentUser) -> list[ApplicationResponse]:
    require_officer(user)
    applications = (
        db.query(Application)
        .filter_by(owner_id=user.id)
        .order_by(Application.created_at.desc())
        .all()
    )
    return [as_response(application) for application in applications]


@router.post("", response_model=ApplicationResponse, status_code=status.HTTP_201_CREATED)
def create_application(
    payload: ApplicationFields,
    request: Request,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> ApplicationResponse:
    require_officer(user)
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    existing = (
        db.query(IdempotencyRecord)
        .filter_by(
            actor_id=user.id,
            operation="create_application",
            key=idempotency_key,
        )
        .first()
    )
    if existing:
        if existing.request_hash != request_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key payload mismatch")
        application = db.get(Application, existing.resource_id)
        response.status_code = status.HTTP_200_OK
        return as_response(application)

    application = Application(
        borrower_type=payload.primary_borrower.type,
        borrower_name=payload.primary_borrower.name,
        product=payload.product,
        application_date=payload.application_date,
        proposed_signing_date=payload.proposed_signing_date,
        owner_id=user.id,
    )
    db.add(application)
    db.flush()
    db.add(
        IdempotencyRecord(
            actor_id=user.id,
            operation="create_application",
            key=idempotency_key,
            request_hash=request_hash,
            resource_id=application.id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        winner = (
            db.query(IdempotencyRecord)
            .filter_by(
                actor_id=user.id,
                operation="create_application",
                key=idempotency_key,
            )
            .one()
        )
        if winner.request_hash != request_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key payload mismatch")
        response.status_code = status.HTTP_200_OK
        return as_response(db.get(Application, winner.resource_id))
    db.refresh(application)
    record_audit(
        db,
        event_type=APPLICATION_CREATED,
        actor=user,
        resource_type="application",
        resource_id=application.id,
        correlation_id=request_correlation_id(request),
    )
    db.commit()
    return as_response(application)


@router.get("/{application_id}", response_model=ApplicationResponse)
def get_application(
    application_id: str, request: Request, db: Db, user: CurrentUser
) -> ApplicationResponse:
    require_officer(user)
    application = db.query(Application).filter_by(id=application_id, owner_id=user.id).first()
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    record_audit(
        db,
        event_type=APPLICATION_VIEWED,
        actor=user,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
        dedupe_minutes=5,
    )
    db.commit()
    return as_response(application)


@router.put("/{application_id}", response_model=ApplicationResponse)
def update_application(
    application_id: str,
    payload: UpdateApplicationRequest,
    request: Request,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
) -> ApplicationResponse:
    require_officer(user)
    owned = db.query(Application).filter_by(id=application_id, owner_id=user.id).first()
    if not owned:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    require_mutable(db, owned)
    result = db.execute(
        update(Application)
        .where(
            Application.id == application_id,
            Application.owner_id == user.id,
            Application.version == payload.version,
        )
        .values(
            borrower_type=payload.primary_borrower.type,
            borrower_name=payload.primary_borrower.name,
            product=payload.product,
            application_date=payload.application_date,
            proposed_signing_date=payload.proposed_signing_date,
            version=Application.version + 1,
        )
    )
    if result.rowcount == 0:
        owned = db.query(Application).filter_by(id=application_id, owner_id=user.id).first()
        if not owned:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "Stale version")
    # Product and proposed signing date feed redline rule selection and LPR
    # timing, so changing them invalidates any current formal redline report.
    mark_redline_runs_stale(db, application_id, "application_change")
    record_audit(
        db,
        event_type=APPLICATION_UPDATED,
        actor=user,
        resource_type="application",
        resource_id=application_id,
        correlation_id=request_correlation_id(request),
    )
    db.commit()
    return as_response(db.get(Application, application_id))
