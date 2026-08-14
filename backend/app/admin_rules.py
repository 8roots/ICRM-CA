"""Administrator rule-package lifecycle and LPR CSV import.

Rule packages are versioned (code x version) with draft/approved/retired
states. Approved versions are immutable: there is no edit endpoint for them;
change happens only through copy-to-change which creates a new draft version.
Calculation types are code-defined (see ``app.redline``) and validated against
their parameter schema — administrators can never enter executable formulas.
Approving a hard rule is rejected when another approved hard rule would make
the primary-rule selection ambiguous (overlapping scope and interval).

LPR is imported from an official-announcement CSV, validated, stored as a
draft batch, and only becomes selectable when explicitly published. There is
no runtime scraping.
"""

import csv
import io
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.audit import (
    LPR_PUBLISHED,
    RULE_APPROVED,
    RULE_RETIRED,
    record_audit,
    request_correlation_id,
)
from app.dependencies import Administrator, Csrf, Db
from app.models import (
    LprEntry,
    LprImport,
    LprImportStatus,
    RulePackage,
    RuleStatus,
)
from app.redline import (
    CALC_LABELS,
    rule_content_hash,
    rule_content_payload,
    validate_calc_params,
)

router = APIRouter(prefix="/admin", tags=["admin-rules"])

REQUIRED_LPR_COLUMNS = {"effective_date", "tenor", "value", "publication_date", "source_url"}
LPR_TENORS = {"1Y", "5Y"}
MAX_LPR_ROWS = 2000


class RulePackageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]*$", min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern=r"^(hard|reference)$")
    lender_qualification: str = Field(min_length=1, max_length=60)
    rule_context: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    effective_from: date
    effective_until: date | None = None
    calc_type: str = Field(min_length=1, max_length=40)
    params: dict
    legal_basis: str = Field(min_length=1, max_length=5000)
    reviewer: str = Field(min_length=1, max_length=100)
    reviewed_at: date
    demo_only: bool = False


class UpdateRulePackageRequest(BaseModel):
    """Draft-only edit; approved/retired versions stay immutable."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    effective_from: date
    effective_until: date | None = None
    params: dict
    legal_basis: str = Field(min_length=1, max_length=5000)
    reviewer: str = Field(min_length=1, max_length=100)
    reviewed_at: date


class RulePackageResponse(BaseModel):
    id: str
    code: str
    name: str
    kind: str
    lender_qualification: str
    rule_context: str
    product: str
    effective_from: date
    effective_until: date | None
    calc_type: str
    calc_type_label: str
    params: dict
    legal_basis: str
    reviewer: str
    reviewed_at: date
    version: int
    status: str
    demo_only: bool
    content_hash: str
    approved_at: datetime | None
    retired_at: datetime | None
    created_at: datetime


class LprEntryResponse(BaseModel):
    id: str
    effective_date: date
    tenor: str
    value: str
    publication_date: date
    source_url: str


class LprImportResponse(BaseModel):
    id: str
    filename: str
    source_authority: str
    status: str
    demo_only: bool
    row_count: int
    created_at: datetime
    published_at: datetime | None
    entries: list[LprEntryResponse]


def as_rule(rule: RulePackage) -> RulePackageResponse:
    return RulePackageResponse(
        id=rule.id,
        code=rule.code,
        name=rule.name,
        kind=rule.kind,
        lender_qualification=rule.lender_qualification,
        rule_context=rule.rule_context,
        product=rule.product,
        effective_from=rule.effective_from,
        effective_until=rule.effective_until,
        calc_type=rule.calc_type,
        calc_type_label=CALC_LABELS.get(rule.calc_type, rule.calc_type),
        params=rule.params,
        legal_basis=rule.legal_basis,
        reviewer=rule.reviewer,
        reviewed_at=rule.reviewed_at,
        version=rule.version,
        status=rule.status,
        demo_only=rule.demo_only,
        content_hash=rule.content_hash,
        approved_at=rule.approved_at,
        retired_at=rule.retired_at,
        created_at=rule.created_at,
    )


def as_import(batch: LprImport) -> LprImportResponse:
    return LprImportResponse(
        id=batch.id,
        filename=batch.filename,
        source_authority=batch.source_authority,
        status=batch.status,
        demo_only=batch.demo_only,
        row_count=batch.row_count,
        created_at=batch.created_at,
        published_at=batch.published_at,
        entries=[as_entry(entry) for entry in batch.entries],
    )


def as_entry(entry: LprEntry) -> LprEntryResponse:
    return LprEntryResponse(
        id=entry.id,
        effective_date=entry.effective_date,
        tenor=entry.tenor,
        value=entry.value,
        publication_date=entry.publication_date,
        source_url=entry.source_url,
    )


def get_rule(db: Db, rule_id: str) -> RulePackage:
    rule = db.get(RulePackage, rule_id)
    if not rule:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Rule package not found")
    return rule


def _apply_rule_fields(rule: RulePackage, payload: RulePackageInput) -> None:
    rule.code = payload.code
    rule.name = payload.name
    rule.kind = payload.kind
    rule.lender_qualification = payload.lender_qualification
    rule.rule_context = payload.rule_context
    rule.product = payload.product
    rule.effective_from = payload.effective_from
    rule.effective_until = payload.effective_until
    rule.calc_type = payload.calc_type
    rule.params = payload.params
    rule.legal_basis = payload.legal_basis
    rule.reviewer = payload.reviewer
    rule.reviewed_at = payload.reviewed_at
    rule.demo_only = payload.demo_only


def validate_rule_input(
    calc_type: str, params: dict, effective_from: date, effective_until: date | None
) -> None:
    try:
        validate_calc_params(calc_type, params)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    if effective_until is not None and effective_until < effective_from:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "effective_until must be on or after effective_from",
        )


def intervals_overlap(
    a_from: date, a_until: date | None, b_from: date, b_until: date | None
) -> bool:
    return a_from <= (b_until or date.max) and b_from <= (a_until or date.max)


@router.get("/rule-packages", response_model=list[RulePackageResponse])
def list_rule_packages(db: Db, admin: Administrator) -> list[RulePackageResponse]:
    rules = db.query(RulePackage).order_by(RulePackage.code, RulePackage.version.desc()).all()
    return [as_rule(rule) for rule in rules]


@router.post(
    "/rule-packages", response_model=RulePackageResponse, status_code=status.HTTP_201_CREATED
)
def create_rule_package(
    payload: RulePackageInput,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> RulePackageResponse:
    validate_rule_input(
        payload.calc_type, payload.params, payload.effective_from, payload.effective_until
    )
    existing = (
        db.query(RulePackage)
        .filter_by(code=payload.code)
        .order_by(RulePackage.version.desc())
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Rule package code already exists")
    rule = RulePackage(
        version=1,
        status=RuleStatus.DRAFT,
        content_hash="",
        **payload.model_dump(),
    )
    rule.content_hash = rule_content_hash(rule_content_payload(rule))
    db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Rule package code already exists") from None
    db.refresh(rule)
    return as_rule(rule)


@router.put("/rule-packages/{rule_id}", response_model=RulePackageResponse)
def update_rule_package(
    rule_id: str,
    payload: UpdateRulePackageRequest,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> RulePackageResponse:
    rule = get_rule(db, rule_id)
    if rule.status != RuleStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only draft rule packages can be edited; approved versions are immutable",
        )
    validate_rule_input(
        rule.calc_type, payload.params, payload.effective_from, payload.effective_until
    )
    rule.name = payload.name
    rule.effective_from = payload.effective_from
    rule.effective_until = payload.effective_until
    rule.params = payload.params
    rule.legal_basis = payload.legal_basis
    rule.reviewer = payload.reviewer
    rule.reviewed_at = payload.reviewed_at
    rule.content_hash = rule_content_hash(rule_content_payload(rule))
    db.commit()
    return as_rule(rule)


@router.post("/rule-packages/{rule_id}/approve", response_model=RulePackageResponse)
def approve_rule_package(
    rule_id: str,
    request: Request,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> RulePackageResponse:
    rule = get_rule(db, rule_id)
    if rule.status != RuleStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft rule packages can be approved")
    if rule.kind == "hard":
        overlapping = (
            db.query(RulePackage)
            .filter(
                RulePackage.kind == "hard",
                RulePackage.status == RuleStatus.APPROVED,
                RulePackage.lender_qualification == rule.lender_qualification,
                RulePackage.rule_context == rule.rule_context,
                RulePackage.product == rule.product,
            )
            .all()
        )
        for other in overlapping:
            if intervals_overlap(
                rule.effective_from,
                rule.effective_until,
                other.effective_from,
                other.effective_until,
            ):
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "Approving would make primary-rule selection ambiguous: another approved "
                    f"hard rule ({other.code} v{other.version}) covers an overlapping scope",
                )
    rule.status = RuleStatus.APPROVED
    rule.approved_at = datetime.now(UTC)
    record_audit(
        db,
        event_type=RULE_APPROVED,
        actor=admin,
        resource_type="rule",
        resource_id=rule.id,
        correlation_id=request_correlation_id(request),
        metadata={"code": rule.code, "version": rule.version, "kind": rule.kind},
    )
    db.commit()
    return as_rule(rule)


@router.post(
    "/rule-packages/{rule_id}/copy",
    response_model=RulePackageResponse,
    status_code=status.HTTP_201_CREATED,
)
def copy_rule_package(
    rule_id: str,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> RulePackageResponse:
    source = get_rule(db, rule_id)
    latest_version = (
        db.query(RulePackage.version)
        .filter_by(code=source.code)
        .order_by(RulePackage.version.desc())
        .first()
    )
    rule = RulePackage(
        code=source.code,
        name=source.name,
        kind=source.kind,
        lender_qualification=source.lender_qualification,
        rule_context=source.rule_context,
        product=source.product,
        effective_from=source.effective_from,
        effective_until=source.effective_until,
        calc_type=source.calc_type,
        params=dict(source.params),
        legal_basis=source.legal_basis,
        reviewer=source.reviewer,
        reviewed_at=source.reviewed_at,
        version=(latest_version[0] if latest_version else 0) + 1,
        status=RuleStatus.DRAFT,
        demo_only=source.demo_only,
        content_hash="",
    )
    rule.content_hash = rule_content_hash(rule_content_payload(rule))
    db.add(rule)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Rule package code already exists") from None
    db.refresh(rule)
    return as_rule(rule)


@router.post("/rule-packages/{rule_id}/retire", response_model=RulePackageResponse)
def retire_rule_package(
    rule_id: str,
    request: Request,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> RulePackageResponse:
    rule = get_rule(db, rule_id)
    if rule.status != RuleStatus.APPROVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only approved rule packages can be retired")
    rule.status = RuleStatus.RETIRED
    rule.retired_at = datetime.now(UTC)
    record_audit(
        db,
        event_type=RULE_RETIRED,
        actor=admin,
        resource_type="rule",
        resource_id=rule.id,
        correlation_id=request_correlation_id(request),
        metadata={"code": rule.code, "version": rule.version, "kind": rule.kind},
    )
    db.commit()
    return as_rule(rule)


# ---------------------------------------------------------------------------
# LPR CSV import / publish
# ---------------------------------------------------------------------------


def parse_lpr_csv(content: str) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None or set(reader.fieldnames) != REQUIRED_LPR_COLUMNS:
        raise ValueError(
            "CSV 表头必须且只能包含列：effective_date,tenor,value,publication_date,source_url"
        )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for lineno, row in enumerate(reader, start=2):
        effective_date = _parse_date(row.get("effective_date", ""), lineno, "effective_date")
        tenor = (row.get("tenor") or "").strip()
        if tenor not in LPR_TENORS:
            raise ValueError(f"第 {lineno} 行：tenor 必须是 1Y 或 5Y")
        try:
            value = Decimal((row.get("value") or "").strip())
        except InvalidOperation:
            raise ValueError(f"第 {lineno} 行：value 必须是数字") from None
        if value <= 0 or value >= 100:
            raise ValueError(f"第 {lineno} 行：value 必须大于 0 且小于 100")
        publication_date = _parse_date(row.get("publication_date", ""), lineno, "publication_date")
        if publication_date > effective_date:
            raise ValueError(f"第 {lineno} 行：publication_date 不得晚于 effective_date")
        source_url = (row.get("source_url") or "").strip()
        if not source_url or len(source_url) > 500:
            raise ValueError(f"第 {lineno} 行：source_url 必须为非空字符串（≤500 字符）")
        key = (effective_date.isoformat(), tenor)
        if key in seen:
            raise ValueError(f"第 {lineno} 行：同一生效日期与期限重复（{key[0]} {key[1]}）")
        seen.add(key)
        rows.append(
            {
                "effective_date": effective_date,
                "tenor": tenor,
                "value": format(value, "f"),
                "publication_date": publication_date,
                "source_url": source_url,
            }
        )
        if len(rows) > MAX_LPR_ROWS:
            raise ValueError(f"CSV 行数超过上限 {MAX_LPR_ROWS}")
    if not rows:
        raise ValueError("CSV 不包含任何数据行")
    return rows


def _parse_date(text: str, lineno: int, column: str) -> date:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"第 {lineno} 行：{column} 必须是 ISO 日期（YYYY-MM-DD）") from None


@router.get("/lpr-imports", response_model=list[LprImportResponse])
def list_lpr_imports(db: Db, admin: Administrator) -> list[LprImportResponse]:
    batches = db.query(LprImport).order_by(LprImport.created_at.desc()).all()
    return [as_import(batch) for batch in batches]


@router.post(
    "/lpr-imports",
    response_model=LprImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_lpr_csv(
    file: UploadFile,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
    source_authority: str = Form(...),
) -> LprImportResponse:
    content = file.file.read().decode("utf-8-sig")
    try:
        rows = parse_lpr_csv(content)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    batch = LprImport(
        filename=(file.filename or "lpr.csv")[:255],
        source_authority=source_authority.strip()[:200] or "全国银行间同业拆借中心",
        status=LprImportStatus.DRAFT,
        demo_only=False,
        row_count=len(rows),
        actor_id=admin.id,
    )
    for row in rows:
        batch.entries.append(LprEntry(**row))
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return as_import(batch)


@router.post("/lpr-imports/{import_id}/publish", response_model=LprImportResponse)
def publish_lpr_import(
    import_id: str,
    request: Request,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> LprImportResponse:
    batch = db.get(LprImport, import_id)
    if not batch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "LPR import not found")
    if batch.status != LprImportStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft LPR imports can be published")
    if not batch.entries:
        raise HTTPException(status.HTTP_409_CONFLICT, "LPR import has no entries")
    batch.status = LprImportStatus.PUBLISHED
    batch.published_at = datetime.now(UTC)
    record_audit(
        db,
        event_type=LPR_PUBLISHED,
        actor=admin,
        resource_type="lpr",
        resource_id=batch.id,
        correlation_id=request_correlation_id(request),
        metadata={"filename": batch.filename, "row_count": batch.row_count},
    )
    db.commit()
    return as_import(batch)
