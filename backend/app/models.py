import uuid
from datetime import UTC, date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
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


class TemplateStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class ItemState(StrEnum):
    SATISFIED = "satisfied"
    MISSING = "missing"
    PENDING_CONFIRMATION = "pending_confirmation"
    NOT_APPLICABLE = "not_applicable"
    MANUALLY_WAIVED = "manually_waived"


class RunStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"


class RuleKind(StrEnum):
    HARD = "hard"
    REFERENCE = "reference"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


class LprImportStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


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
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[float | None] = mapped_column(nullable=True)
    height: Mapped[float | None] = mapped_column(nullable=True)
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
    x0: Mapped[float | None] = mapped_column(nullable=True)
    y0: Mapped[float | None] = mapped_column(nullable=True)
    x1: Mapped[float | None] = mapped_column(nullable=True)
    y1: Mapped[float | None] = mapped_column(nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    locator: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
    locator: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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


class CandidateFact(Base):
    """Immutable, source-backed field candidate produced by an extractor.

    Candidates are never edited or deleted through the APIs; corrections and
    manual values are recorded as separate :class:`Resolution` rows.
    """

    __tablename__ = "candidate_facts"
    __table_args__ = (
        UniqueConstraint(
            "output_id",
            "field_key",
            "subject_role",
            "raw_text",
            "extractor",
            name="uq_candidate_fact_signature",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    output_id: Mapped[str] = mapped_column(
        ForeignKey("document_outputs.id", ondelete="CASCADE"), index=True
    )
    block_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_blocks.id", ondelete="CASCADE"), nullable=True
    )
    cell_id: Mapped[str | None] = mapped_column(
        ForeignKey("table_cells.id", ondelete="CASCADE"), nullable=True
    )
    subject_role: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    field_key: Mapped[str] = mapped_column(String(60), index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    typed_value: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column()
    extractor: Mapped[str] = mapped_column(String(30))
    extractor_version: Mapped[str] = mapped_column(String(100))
    model_version: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_refs: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    document: Mapped[Document] = relationship()


class Resolution(Base):
    """A rule-usable value explicitly created by the application owner.

    ``selected`` copies an existing candidate, ``corrected`` fixes one, and
    ``manual`` has no material source and must carry a reason.
    """

    __tablename__ = "resolutions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_facts.id", ondelete="CASCADE"), nullable=True
    )
    field_key: Mapped[str] = mapped_column(String(60), index=True)
    subject_role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    resolution_type: Mapped[str] = mapped_column(String(20))
    typed_value: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    candidate: Mapped[CandidateFact | None] = relationship()


class CloudExtractionCall(Base):
    """Restricted audit record for one cloud extraction attempt.

    Only redacted request/response content and metadata are stored; normal logs
    never contain source, prompt, or response bodies.
    """

    __tablename__ = "cloud_extraction_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    output_id: Mapped[str] = mapped_column(
        ForeignKey("document_outputs.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100))
    redaction_version: Mapped[str] = mapped_column(String(100))
    source_refs: Mapped[list] = mapped_column(JSON)
    redacted_request: Mapped[dict] = mapped_column(JSON)
    redacted_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CompletenessTemplate(Base):
    """A versioned completeness checklist keyed by product x borrower type.

    Versions are immutable once published; changes go through copy-to-change
    which creates a new draft version with the same ``code``.
    """

    __tablename__ = "completeness_templates"
    __table_args__ = (UniqueConstraint("code", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str] = mapped_column(String(200))
    product: Mapped[str] = mapped_column(String(100), index=True)
    borrower_type: Mapped[str] = mapped_column(String(20), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=TemplateStatus.DRAFT, index=True)
    demo_only: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    items: Mapped[list["ChecklistItem"]] = relationship(
        back_populates="template", cascade="all, delete-orphan", order_by="ChecklistItem.order"
    )


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    __table_args__ = (UniqueConstraint("template_id", "code"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    template_id: Mapped[str] = mapped_column(
        ForeignKey("completeness_templates.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30), index=True)
    order: Mapped[int] = mapped_column(Integer)
    requires_seal: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_signature: Mapped[bool] = mapped_column(Boolean, default=False)
    condition: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    template: Mapped[CompletenessTemplate] = relationship(back_populates="items")


class MaterialClassificationCandidate(Base):
    """Content-based material-category candidate produced by the worker."""

    __tablename__ = "material_classification_candidates"
    __table_args__ = (UniqueConstraint("document_id", "category", "method"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(30), index=True)
    confidence: Mapped[float] = mapped_column()
    method: Mapped[str] = mapped_column(String(30))
    method_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    document: Mapped[Document] = relationship()


class ClassificationConfirmation(Base):
    """Human confirmation of one material category per document."""

    __tablename__ = "classification_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DocumentChecklistMapping(Base):
    """Confirmed many-to-many evidence mapping between a document and an item."""

    __tablename__ = "document_checklist_mappings"
    __table_args__ = (UniqueConstraint("application_id", "document_id", "item_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    document: Mapped[Document] = relationship()
    item: Mapped[ChecklistItem] = relationship()


class WaiverRecord(Base):
    """Manual waiver of a checklist item with mandatory audit trail."""

    __tablename__ = "waiver_records"
    __table_args__ = (UniqueConstraint("application_id", "item_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="CASCADE"), index=True
    )
    reason: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    item: Mapped[ChecklistItem] = relationship()


class CompletenessRun(Base):
    """Immutable formal completeness snapshot with content hash.

    ``status`` is ``current`` when created and flips to ``stale`` when any
    completeness input changes (mapping, waiver, classification, evidence
    review) or when the applicable published template version changes.
    """

    __tablename__ = "completeness_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    template_id: Mapped[str] = mapped_column(
        ForeignKey("completeness_templates.id", ondelete="CASCADE")
    )
    template_snapshot: Mapped[dict] = mapped_column(JSON)
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    result_snapshot: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.CURRENT, index=True)
    stale_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RulePackage(Base):
    """Versioned hard rule or judicial-risk reference line.

    Approved versions are immutable; change happens through copy-to-change
    which creates a new draft version with the same ``code``. Calculation
    types are code-defined and published with tests; a package only configures
    scope, parameters, thresholds, and legal basis — never executable formulas.
    """

    __tablename__ = "rule_packages"
    __table_args__ = (UniqueConstraint("code", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), index=True)
    lender_qualification: Mapped[str] = mapped_column(String(60), index=True)
    rule_context: Mapped[str] = mapped_column(String(100), index=True)
    product: Mapped[str] = mapped_column(String(100), index=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    calc_type: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict] = mapped_column(JSON)
    legal_basis: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str] = mapped_column(String(100))
    reviewed_at: Mapped[date] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default=RuleStatus.DRAFT, index=True)
    demo_only: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RuleContextConfirmation(Base):
    """Owner-confirmed rule context (region/regulatory context) per application.

    The system never infers rule context from the borrower address; the
    context must be explicitly confirmed before rule selection.
    """

    __tablename__ = "rule_context_confirmations"
    __table_args__ = (UniqueConstraint("application_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    context: Mapped[str] = mapped_column(String(100))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class LprImport(Base):
    """One validated LPR CSV batch; draft until explicitly published.

    Only entries from published imports are eligible for selection. No runtime
    scraping: data comes from official announcements imported by an admin.
    """

    __tablename__ = "lpr_imports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255))
    source_authority: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default=LprImportStatus.DRAFT, index=True)
    demo_only: Mapped[bool] = mapped_column(Boolean, default=False)
    row_count: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    entries: Mapped[list["LprEntry"]] = relationship(
        back_populates="import_batch",
        cascade="all, delete-orphan",
        order_by="LprEntry.effective_date",
    )


class LprEntry(Base):
    """One official LPR value for a tenor on an effective date."""

    __tablename__ = "lpr_entries"
    __table_args__ = (UniqueConstraint("import_id", "effective_date", "tenor"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    import_id: Mapped[str] = mapped_column(
        ForeignKey("lpr_imports.id", ondelete="CASCADE"), index=True
    )
    effective_date: Mapped[date] = mapped_column(Date)
    tenor: Mapped[str] = mapped_column(String(10))  # "1Y" or "5Y"
    value: Mapped[str] = mapped_column(String(40))  # decimal string, percent
    publication_date: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    import_batch: Mapped[LprImport] = relationship(back_populates="entries")


class RedlineRun(Base):
    """Immutable formal redline snapshot with content hash.

    ``status`` is ``current`` when created and flips to ``stale`` when any
    redline input changes (resolution, rule context, application product or
    proposed signing date) or when a new run is created. A newer applicable
    rule or a different LPR selection for the run's as-of date also makes the
    run stale via live checks. ``rule_id`` is null when selection is
    indeterminate (no unique primary rule package).
    """

    __tablename__ = "redline_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    rule_id: Mapped[str | None] = mapped_column(
        ForeignKey("rule_packages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    rule_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    result_snapshot: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.CURRENT, index=True)
    stale_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("actor_id", "operation", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    operation: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(36))
