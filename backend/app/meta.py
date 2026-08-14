"""Read-only field whitelist metadata for the approval workbench.

The candidate review UI lets an officer manually confirm any whitelisted
field, not only fields that already produced candidates; the whitelist itself
lives in ``app.fields`` and this endpoint exposes it to the frontend.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.dependencies import CurrentUser, Db
from app.fields import FIELDS, GROUP_LABELS, FieldGroup

router = APIRouter(prefix="/meta", tags=["meta"])


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
