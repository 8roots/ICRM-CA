"""Read-only field whitelist metadata for the approval workbench.

The candidate review UI lets an officer manually confirm any whitelisted
field, not only fields that already produced candidates; the whitelist itself
lives in ``app.fields`` and this endpoint exposes it to the frontend.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.dependencies import CurrentUser, Db
from app.fields import FIELDS, GROUP_LABELS, FieldGroup

router = APIRouter(prefix="/meta", tags=["meta"])


class CloudGateResponse(BaseModel):
    configured: bool
    confirmed: bool
    ready: bool
    blockers: list[str]


@router.get("/cloud-gate", response_model=CloudGateResponse)
def cloud_gate(db: Db, user: CurrentUser) -> CloudGateResponse:
    """Cloud readiness gate: whether DeepSeek extraction is enabled.

    Missing credentials or a missing no-training/retention confirmation
    disable the cloud path; local candidate extraction continues regardless.
    """
    return CloudGateResponse(
        configured=settings.cloud_configured,
        confirmed=settings.cloud_confirmed,
        ready=settings.cloud_ready,
        blockers=settings.cloud_gate_blockers,
    )


class FieldMetaResponse(BaseModel):
    key: str
    label: str
    group: str
    group_label: str
    critical: bool


@router.get("/fields", response_model=list[FieldMetaResponse])
def list_fields(db: Db, user: CurrentUser) -> list[FieldMetaResponse]:
    definitions = sorted(
        FIELDS.values(), key=lambda definition: (definition.group.value, definition.key)
    )
    return [
        FieldMetaResponse(
            key=definition.key,
            label=definition.label,
            group=definition.group.value,
            group_label=GROUP_LABELS.get(FieldGroup(definition.group), definition.group.value),
            critical=definition.critical,
        )
        for definition in definitions
    ]
