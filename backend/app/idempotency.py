from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import IdempotencyRecord


def add_idempotency_record(
    db: Session,
    actor_id: str,
    operation: str,
    key: str,
    request_hash: str,
    resource_id: str,
) -> None:
    db.add(
        IdempotencyRecord(
            actor_id=actor_id,
            operation=operation,
            key=key,
            request_hash=request_hash,
            resource_id=resource_id,
        )
    )


def replay_resource_id(
    db: Session,
    actor_id: str,
    operation: str,
    key: str,
    request_hash: str,
) -> str | None:
    record = (
        db.query(IdempotencyRecord)
        .filter_by(actor_id=actor_id, operation=operation, key=key)
        .first()
    )
    if not record:
        return None
    if record.request_hash != request_hash:
        raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key payload mismatch")
    return record.resource_id
