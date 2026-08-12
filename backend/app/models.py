import uuid
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


class JobStatus(StrEnum):
    WAITING = "waiting"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    MANUAL_HANDLING = "manual_handling"
    NOT_APPLICABLE = "not_applicable"


class ProcessingStepName(StrEnum):
    VALIDATION = "validation"
    PARSING_OCR = "parsing_ocr"
    STRUCTURE_EXTRACTION = "structure_extraction"
    SEAL_DETECTION = "seal_detection"
    CLASSIFICATION = "classification"
    CANDIDATE_EXTRACTION = "candidate_extraction"


class ReviewStatus(StrEnum):
    NOT_READY = "not_ready"
    PENDING_REVIEW = "pending_review"
    REVIEW_COMPLETE = "review_complete"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class LoginSession(Base):
    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user: Mapped[User] = relationship()


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    borrower_type: Mapped[str] = mapped_column(String(20))
    borrower_name: Mapped[str] = mapped_column(String(200))
    product: Mapped[str] = mapped_column(String(100))
    application_date: Mapped[date] = mapped_column(Date)
    proposed_signing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    lifecycle_state: Mapped[str] = mapped_column(String(40), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("application_id", "sha256"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    extension: Mapped[str] = mapped_column(String(20))
    declared_mime: Mapped[str] = mapped_column(String(150))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(255), unique=True)
    processing_status: Mapped[str] = mapped_column(String(30), default=JobStatus.WAITING)
    review_status: Mapped[str] = mapped_column(String(30), default=ReviewStatus.NOT_READY)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    jobs: Mapped[list["DocumentJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentJob(Base):
    __tablename__ = "document_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), default=JobStatus.WAITING, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    retry_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    document: Mapped[Document] = relationship(back_populates="jobs")
    steps: Mapped[list["ProcessingStep"]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class ProcessingStep(Base):
    __tablename__ = "processing_steps"
    __table_args__ = (UniqueConstraint("job_id", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("document_jobs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default=JobStatus.WAITING)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    job: Mapped[DocumentJob] = relationship(back_populates="steps")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("actor_id", "operation", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(36))
