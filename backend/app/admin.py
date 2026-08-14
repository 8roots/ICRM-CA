from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, update

from app.dependencies import Administrator, Csrf, Db
from app.models import Application, DocumentJob, JobStatus, User, WorkerHeartbeat
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=1024)
    enabled: bool = True


class UpdateUserRequest(BaseModel):
    enabled: bool
    version: int = Field(ge=1)


class ManagedUserResponse(BaseModel):
    id: str
    username: str
    role: str
    enabled: bool
    version: int


class QueueJobResponse(BaseModel):
    id: str
    document_id: str
    application_id: str
    filename: str
    status: str
    attempts: int
    error_code: str | None
    retry_reason: str | None
    created_at: datetime


class WorkerHeartbeatResponse(BaseModel):
    worker_id: str
    hostname: str
    last_seen_at: datetime
    healthy: bool


class QueueResponse(BaseModel):
    by_status: dict[str, int]
    waiting: int
    running: int
    failed: int
    manual_handling: int
    oldest_waiting: QueueJobResponse | None
    recent_failures: list[QueueJobResponse]
    workers: list[WorkerHeartbeatResponse]


def as_queue_job(job: DocumentJob) -> QueueJobResponse:
    return QueueJobResponse(
        id=job.id,
        document_id=job.document_id,
        application_id=job.document.application_id,
        filename=job.document.filename,
        status=job.status,
        attempts=job.attempts,
        error_code=job.error_code,
        retry_reason=job.retry_reason,
        created_at=job.created_at,
    )


@router.get("/queue", response_model=QueueResponse)
def queue_status(db: Db, admin: Administrator) -> QueueResponse:
    """Admin task backlog / failure / retry view with worker heartbeats."""
    rows = (
        db.query(DocumentJob.status, func.count(DocumentJob.id)).group_by(DocumentJob.status).all()
    )
    by_status = {status_value: count for status_value, count in rows}
    oldest_waiting = (
        db.query(DocumentJob)
        .filter(DocumentJob.status == JobStatus.WAITING)
        .order_by(DocumentJob.created_at)
        .first()
    )
    recent_failures = (
        db.query(DocumentJob)
        .filter(DocumentJob.status.in_([JobStatus.FAILED, JobStatus.MANUAL_HANDLING]))
        .order_by(DocumentJob.created_at.desc())
        .limit(50)
        .all()
    )
    now = datetime.now(UTC)
    workers = [
        WorkerHeartbeatResponse(
            worker_id=worker.worker_id,
            hostname=worker.hostname,
            last_seen_at=worker.last_seen_at,
            healthy=(now - worker.last_seen_at.replace(tzinfo=UTC) < timedelta(seconds=120)),
        )
        for worker in db.query(WorkerHeartbeat).all()
    ]
    return QueueResponse(
        by_status=by_status,
        waiting=by_status.get(JobStatus.WAITING, 0),
        running=by_status.get(JobStatus.RUNNING, 0),
        failed=by_status.get(JobStatus.FAILED, 0),
        manual_handling=by_status.get(JobStatus.MANUAL_HANDLING, 0),
        oldest_waiting=as_queue_job(oldest_waiting) if oldest_waiting else None,
        recent_failures=[as_queue_job(job) for job in recent_failures],
        workers=workers,
    )


class AdminApplicationResponse(BaseModel):
    id: str
    borrower_type: str
    borrower_name: str
    product: str
    application_date: date
    proposed_signing_date: date | None
    owner_id: str
    owner_username: str
    lifecycle_state: str
    version: int
    created_at: datetime


@router.get("/applications", response_model=list[AdminApplicationResponse])
def list_admin_applications(db: Db, admin: Administrator) -> list[AdminApplicationResponse]:
    """Admin metadata-only view of all applications (never material content).

    The admin never gains material access; every officer-facing endpoint
    remains scoped to the application owner.
    """
    rows = db.query(Application).order_by(Application.created_at.desc()).all()
    usernames = {user.id: user.username for user in db.query(User).all()}
    return [
        AdminApplicationResponse(
            id=application.id,
            borrower_type=application.borrower_type,
            borrower_name=application.borrower_name,
            product=application.product,
            application_date=application.application_date,
            proposed_signing_date=application.proposed_signing_date,
            owner_id=application.owner_id,
            owner_username=usernames.get(application.owner_id, ""),
            lifecycle_state=application.lifecycle_state,
            version=application.version,
            created_at=application.created_at,
        )
        for application in rows
    ]


@router.get("/users", response_model=list[ManagedUserResponse])
def list_users(db: Db, admin: Administrator) -> list[User]:
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=ManagedUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> User:
    if db.query(User).filter_by(username=payload.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="approval_officer",
        enabled=payload.enabled,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=ManagedUserResponse)
def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> User:
    result = db.execute(
        update(User)
        .where(User.id == user_id, User.version == payload.version)
        .values(enabled=payload.enabled, version=User.version + 1)
    )
    if result.rowcount == 0:
        if db.get(User, user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "Stale version")
    db.commit()
    return db.get(User, user_id)
