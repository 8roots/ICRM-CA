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
    outputs: Mapped[list["DocumentOutput"]] = relationship(
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


class DocumentOutput(Base):
    __tablename__ = "document_outputs"
    __table_args__ = (UniqueConstraint("document_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    parser_version: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    document: Mapped[Document] = relationship(back_populates="outputs")
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="output", cascade="all, delete-orphan", order_by="DocumentPage.number"
    )
    reviews: Mapped[list["EvidenceReview"]] = relationship(
        back_populates="output", cascade="all, delete-orphan", order_by="EvidenceReview.created_at"
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("output_id", "number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    output_id: Mapped[str] = mapped_column(
        ForeignKey("document_outputs.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column(Integer)
    width: Mapped[float] = mapped_column()
    height: Mapped[float] = mapped_column()
    status: Mapped[str] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    output: Mapped[DocumentOutput] = relationship(back_populates="pages")
    blocks: Mapped[list["DocumentBlock"]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="DocumentBlock.order"
    )
    seals: Mapped[list["SealCandidate"]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )


class DocumentBlock(Base):
    __tablename__ = "document_blocks"
    __table_args__ = (UniqueConstraint("page_id", "order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(
        ForeignKey("document_pages.id", ondelete="CASCADE"), index=True
    )
    order: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    text: Mapped[str] = mapped_column(Text)
    x0: Mapped[float] = mapped_column()
    y0: Mapped[float] = mapped_column()
    x1: Mapped[float] = mapped_column()
    y1: Mapped[float] = mapped_column()
    extraction_method: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    page: Mapped[DocumentPage] = relationship(back_populates="blocks")
    cells: Mapped[list["TableCell"]] = relationship(
        back_populates="block", cascade="all, delete-orphan"
    )


class TableCell(Base):
    __tablename__ = "table_cells"
    __table_args__ = (UniqueConstraint("block_id", "row_index", "column_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    block_id: Mapped[str] = mapped_column(
        ForeignKey("document_blocks.id", ondelete="CASCADE"), index=True
    )
    row_index: Mapped[int] = mapped_column(Integer)
    column_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    x0: Mapped[float | None] = mapped_column(nullable=True)
    y0: Mapped[float | None] = mapped_column(nullable=True)
    x1: Mapped[float | None] = mapped_column(nullable=True)
    y1: Mapped[float | None] = mapped_column(nullable=True)
    block: Mapped[DocumentBlock] = relationship(back_populates="cells")


class SealCandidate(Base):
    __tablename__ = "seal_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    page_id: Mapped[str] = mapped_column(
        ForeignKey("document_pages.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    x0: Mapped[float] = mapped_column()
    y0: Mapped[float] = mapped_column()
    x1: Mapped[float] = mapped_column()
    y1: Mapped[float] = mapped_column()
    confidence: Mapped[float] = mapped_column()
    model_version: Mapped[str] = mapped_column(String(100))
    page: Mapped[DocumentPage] = relationship(back_populates="seals")


class EvidenceReview(Base):
    __tablename__ = "evidence_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    output_id: Mapped[str] = mapped_column(
        ForeignKey("document_outputs.id", ondelete="CASCADE"), index=True
    )
    seal_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("seal_candidates.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    output: Mapped[DocumentOutput] = relationship(back_populates="reviews")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("actor_id", "operation", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(36))
