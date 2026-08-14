"""Administrator completeness template lifecycle.

Templates are versioned (code x version) with draft/published/retired states.
Published versions are immutable: there is no update endpoint at all; change
happens only through copy-to-change which creates a new draft version. A
product x borrower-type key may have at most one published version.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError

from app.audit import TEMPLATE_PUBLISHED, TEMPLATE_RETIRED, record_audit, request_correlation_id
from app.classification import CATEGORY_LABELS, MaterialCategory
from app.completeness import template_content_hash, validate_template_items
from app.dependencies import Administrator, Csrf, Db
from app.models import ChecklistItem, CompletenessTemplate, TemplateStatus

router = APIRouter(prefix="/admin/completeness-templates", tags=["admin-templates"])


class TemplateItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=60)
    label: str = Field(min_length=1, max_length=200)
    category: MaterialCategory
    requires_seal: bool = False
    requires_signature: bool = False
    condition: dict | None = Field(default=None, max_length=60)


class CreateTemplateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]*$", min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    product: str = Field(min_length=1, max_length=100)
    borrower_type: Literal["corporate", "individual"]
    demo_only: bool = False
    items: list[TemplateItemInput] = Field(min_length=1, max_length=100)


class UpdateTemplateRequest(BaseModel):
    """Draft-only edit; published/retired versions stay immutable."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    items: list[TemplateItemInput] = Field(min_length=1, max_length=100)


class TemplateItemResponse(BaseModel):
    code: str
    label: str
    category: str
    category_label: str
    order: int
    requires_seal: bool
    requires_signature: bool
    condition: dict | None


class TemplateResponse(BaseModel):
    id: str
    code: str
    name: str
    product: str
    borrower_type: str
    version: int
    status: str
    demo_only: bool
    content_hash: str
    published_at: datetime | None
    retired_at: datetime | None
    created_at: datetime
    items: list[TemplateItemResponse]


def as_item(item: ChecklistItem) -> TemplateItemResponse:
    return TemplateItemResponse(
        code=item.code,
        label=item.label,
        category=item.category,
        category_label=CATEGORY_LABELS.get(MaterialCategory(item.category), item.category),
        order=item.order,
        requires_seal=item.requires_seal,
        requires_signature=item.requires_signature,
        condition=item.condition,
    )


def as_template(template: CompletenessTemplate) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        code=template.code,
        name=template.name,
        product=template.product,
        borrower_type=template.borrower_type,
        version=template.version,
        status=template.status,
        demo_only=template.demo_only,
        content_hash=template.content_hash,
        published_at=template.published_at,
        retired_at=template.retired_at,
        created_at=template.created_at,
        items=[as_item(item) for item in sorted(template.items, key=lambda item: item.order)],
    )


def get_template(db: Db, template_id: str) -> CompletenessTemplate:
    template = db.get(CompletenessTemplate, template_id)
    if not template:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Template not found")
    return template


@router.get("", response_model=list[TemplateResponse])
def list_templates(db: Db, admin: Administrator) -> list[TemplateResponse]:
    templates = (
        db.query(CompletenessTemplate)
        .order_by(CompletenessTemplate.code, CompletenessTemplate.version.desc())
        .all()
    )
    return [as_template(template) for template in templates]


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: CreateTemplateRequest,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> TemplateResponse:
    try:
        validate_template_items([item.model_dump() for item in payload.items])
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    existing = (
        db.query(CompletenessTemplate)
        .filter_by(code=payload.code)
        .order_by(CompletenessTemplate.version.desc())
        .first()
    )
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Template code already exists")
    template = CompletenessTemplate(
        code=payload.code,
        name=payload.name,
        product=payload.product,
        borrower_type=payload.borrower_type,
        version=1,
        status=TemplateStatus.DRAFT,
        demo_only=payload.demo_only,
        content_hash="",
    )
    for order, item in enumerate(payload.items, start=1):
        template.items.append(
            ChecklistItem(
                code=item.code,
                label=item.label,
                category=item.category.value,
                order=order,
                requires_seal=item.requires_seal,
                requires_signature=item.requires_signature,
                condition=item.condition,
            )
        )
    template.content_hash = template_content_hash(template)
    db.add(template)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Template code already exists") from None
    db.refresh(template)
    return as_template(template)


@router.put("/{template_id}", response_model=TemplateResponse)
def update_template(
    template_id: str,
    payload: UpdateTemplateRequest,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> TemplateResponse:
    template = get_template(db, template_id)
    if template.status != TemplateStatus.DRAFT:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only draft templates can be edited; published versions are immutable",
        )
    try:
        validate_template_items([item.model_dump() for item in payload.items])
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None
    template.name = payload.name
    db.query(ChecklistItem).filter_by(template_id=template.id).delete(
        synchronize_session=False
    )
    db.expire(template, ["items"])
    for order, item in enumerate(payload.items, start=1):
        db.add(
            ChecklistItem(
                template_id=template.id,
                code=item.code,
                label=item.label,
                category=item.category.value,
                order=order,
                requires_seal=item.requires_seal,
                requires_signature=item.requires_signature,
                condition=item.condition,
            )
        )
    template.content_hash = template_content_hash(template)
    db.commit()
    return as_template(template)


@router.post("/{template_id}/publish", response_model=TemplateResponse)
def publish_template(
    template_id: str,
    request: Request,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> TemplateResponse:
    template = get_template(db, template_id)
    if template.status != TemplateStatus.DRAFT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only draft templates can be published")
    published = (
        db.query(CompletenessTemplate)
        .filter_by(
            product=template.product,
            borrower_type=template.borrower_type,
            status=TemplateStatus.PUBLISHED,
        )
        .first()
    )
    if published and published.id != template.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A published template already exists for this product and borrower type",
        )
    template.status = TemplateStatus.PUBLISHED
    template.published_at = datetime.now(UTC)
    record_audit(
        db,
        event_type=TEMPLATE_PUBLISHED,
        actor=admin,
        resource_type="template",
        resource_id=template.id,
        correlation_id=request_correlation_id(request),
        metadata={
            "code": template.code,
            "version": template.version,
            "product": template.product,
            "borrower_type": template.borrower_type,
        },
    )
    db.commit()
    return as_template(template)


@router.post(
    "/{template_id}/copy",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def copy_template(
    template_id: str,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> TemplateResponse:
    source = get_template(db, template_id)
    latest_version = (
        db.query(CompletenessTemplate.version)
        .filter_by(code=source.code)
        .order_by(CompletenessTemplate.version.desc())
        .first()
    )
    template = CompletenessTemplate(
        code=source.code,
        name=source.name,
        product=source.product,
        borrower_type=source.borrower_type,
        version=(latest_version[0] if latest_version else 0) + 1,
        status=TemplateStatus.DRAFT,
        demo_only=source.demo_only,
        content_hash="",
    )
    for order, item in enumerate(sorted(source.items, key=lambda item: item.order), start=1):
        template.items.append(
            ChecklistItem(
                code=item.code,
                label=item.label,
                category=item.category,
                order=order,
                requires_seal=item.requires_seal,
                requires_signature=item.requires_signature,
                condition=item.condition,
            )
        )
    template.content_hash = template_content_hash(template)
    db.add(template)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Template code already exists") from None
    db.refresh(template)
    return as_template(template)


@router.post("/{template_id}/retire", response_model=TemplateResponse)
def retire_template(
    template_id: str,
    request: Request,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> TemplateResponse:
    template = get_template(db, template_id)
    if template.status != TemplateStatus.PUBLISHED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only published templates can be retired")
    template.status = TemplateStatus.RETIRED
    template.retired_at = datetime.now(UTC)
    record_audit(
        db,
        event_type=TEMPLATE_RETIRED,
        actor=admin,
        resource_type="template",
        resource_id=template.id,
        correlation_id=request_correlation_id(request),
        metadata={"code": template.code, "version": template.version},
    )
    db.commit()
    return as_template(template)
