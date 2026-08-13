"""Candidate extraction orchestration: local rules first, then cloud for the rest.

Local deterministic extraction always runs and its candidates are always
stored. Only *unresolved* target fields (no candidate and no resolution yet in
the application) are sliced, redacted with stable per-application aliases, and
sent to DeepSeek. A redaction failure or cloud outage never removes local
candidates; it records a restricted audit row and marks the extraction step
retryable.
"""

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.cloud_extraction import (
    PROMPT_VERSION,
    CloudCandidate,
    CloudExtractionError,
    DeepSeekClient,
    RedactedSlice,
)
from app.extraction import LOCAL_EXTRACTOR_VERSION, CandidateSeed, extract_from_output
from app.fields import FIELDS, cloud_targets
from app.models import (
    Application,
    CandidateFact,
    CloudExtractionCall,
    Document,
    DocumentOutput,
    JobStatus,
    Resolution,
)
from app.redaction import (
    REDACTION_VERSION,
    RedactionError,
    alias_map,
    redact,
    verify_redaction,
)
from app.values import normalize_field

logger = logging.getLogger("icrm.extraction")

MAX_SLICES_PER_FIELD = 2
MAX_SLICE_CHARS = 4000


@dataclass
class ExtractionStepResult:
    step_status: JobStatus
    error_code: str | None


def _source_block_id(seed: CandidateSeed) -> str | None:
    return seed.source_refs[0].get("block_id") if seed.source_refs else None


def _source_cell_id(seed: CandidateSeed) -> str | None:
    return seed.source_refs[0].get("cell_id") if seed.source_refs else None


def _store_seeds(
    db: Session,
    document_id: str,
    output_id: str,
    seeds: list[CandidateSeed],
    *,
    extractor: str,
    extractor_version: str,
    model_version: str,
    prompt_version: str | None = None,
) -> None:
    for seed in seeds:
        existing = (
            db.query(CandidateFact)
            .filter_by(
                output_id=output_id,
                field_key=seed.field_key,
                subject_role=seed.subject_role,
                raw_text=seed.raw_text,
                extractor=extractor,
            )
            .first()
        )
        if existing:
            continue
        db.add(
            CandidateFact(
                document_id=document_id,
                output_id=output_id,
                block_id=_source_block_id(seed),
                cell_id=_source_cell_id(seed),
                subject_role=seed.subject_role,
                field_key=seed.field_key,
                raw_text=seed.raw_text,
                typed_value=seed.typed_value.model_dump_stored(),
                confidence=seed.confidence,
                extractor=extractor,
                extractor_version=extractor_version,
                model_version=model_version,
                prompt_version=prompt_version,
                source_refs=seed.source_refs,
            )
        )
    db.flush()


MIN_AMBIGUOUS_CONFIDENCE = 0.5


def _confident_field_keys(db: Session, application_id: str) -> set[str]:
    """Fields with a confident candidate or an explicit resolution.

    A field counts as covered only when it has a candidate at or above
    ``MIN_AMBIGUOUS_CONFIDENCE``; low-confidence (ambiguous) candidates leave
    the field open for the cloud path, matching design section 4.2
    ("仍缺失或含糊的目标字段").
    """
    rows = (
        db.query(CandidateFact.field_key, CandidateFact.confidence)
        .join(Document)
        .filter(Document.application_id == application_id)
        .all()
    )
    confident = {
        key
        for key, confidence in rows
        if confidence >= MIN_AMBIGUOUS_CONFIDENCE
    }
    resolution_keys = (
        db.query(Resolution.field_key)
        .filter(Resolution.application_id == application_id)
        .distinct()
        .all()
    )
    return confident | {key for (key,) in resolution_keys}


def _select_slices(output: DocumentOutput, unresolved: list[str]) -> list[RedactedSlice]:
    keyword_fields = {
        keyword: field_key for field_key in unresolved for keyword in FIELDS[field_key].keywords
    }
    found: dict[str, list[RedactedSlice]] = {}
    for page in output.pages:
        for block in page.blocks:
            for keyword, field_key in keyword_fields.items():
                if keyword not in block.text or (
                    len(found.get(field_key, [])) >= MAX_SLICES_PER_FIELD
                ):
                    continue
                if any(slice.text == block.text for slice in found.get(field_key, [])):
                    continue
                found.setdefault(field_key, []).append(
                    RedactedSlice(
                        field_key=field_key,
                        label=FIELDS[field_key].label,
                        text=block.text,
                        source_refs=[
                            {
                                "document_id": output.document_id,
                                "output_id": output.id,
                                "output_version": output.version,
                                "page_number": page.number,
                                "block_id": block.id,
                                "block_order": block.order,
                                "locator": block.locator,
                            }
                        ],
                    )
                )
    # Round-robin across fields so no field is starved by page order, then cap
    # the total slice size sent to the cloud.
    ordered = [
        slice
        for index in range(MAX_SLICES_PER_FIELD)
        for field_key in sorted(found)
        if index < len(found[field_key])
        for slice in [found[field_key][index]]
    ]
    slices: list[RedactedSlice] = []
    budget = MAX_SLICE_CHARS
    for slice in ordered:
        if budget - len(slice.text) < 0:
            break
        slices.append(slice)
        budget -= len(slice.text)
    return slices


def _record_call(
    db: Session,
    application_id: str,
    document_id: str,
    output_id: str,
    status: str,
    error_code: str | None,
    slices: list[RedactedSlice],
    *,
    model: str,
    response: list[dict] | None = None,
) -> None:
    db.add(
        CloudExtractionCall(
            application_id=application_id,
            document_id=document_id,
            output_id=output_id,
            status=status,
            error_code=error_code,
            model=model,
            prompt_version=PROMPT_VERSION,
            redaction_version=REDACTION_VERSION,
            source_refs=[
                {"field_key": item.field_key, "source_refs": item.source_refs} for item in slices
            ],
            redacted_request={
                "slices": [
                    {"field_key": item.field_key, "label": item.label, "text": item.text}
                    for item in slices
                ]
            },
            redacted_response={"results": response} if response is not None else None,
        )
    )


def _known_names(application: Application, seeds: list[CandidateSeed]) -> list[str]:
    names = [application.borrower_name]
    name_fields = {"personal_name", "corporate_name", "legal_representative", "guarantor"}
    names.extend(seed.typed_value.value for seed in seeds if seed.field_key in name_fields)
    return names


def _cloud_seeds(
    candidates: list[CloudCandidate], slices: list[RedactedSlice]
) -> list[CandidateSeed]:
    slice_by_field: dict[str, list[RedactedSlice]] = {}
    for slice in slices:
        slice_by_field.setdefault(slice.field_key, []).append(slice)
    seeds: list[CandidateSeed] = []
    for candidate in candidates:
        field = FIELDS.get(candidate.field_key)
        if field is None:
            continue
        typed = normalize_field(field, candidate.value)
        if typed is None:
            continue
        source_refs = candidate.source_refs or [
            ref
            for slice in slice_by_field.get(candidate.field_key, [])
            for ref in slice.source_refs
        ]
        seeds.append(
            CandidateSeed(
                field_key=candidate.field_key,
                raw_text=candidate.value,
                typed_value=typed,
                confidence=candidate.confidence,
                subject_role=field.default_subject,
                source_refs=source_refs,
            )
        )
    return seeds


def run_candidate_extraction(
    db: Session,
    document: Document,
    output: DocumentOutput,
    cloud_client: DeepSeekClient | None = None,
) -> ExtractionStepResult:
    """Run local extraction, then (when enabled) cloud extraction for gaps."""
    application = db.get(Application, document.application_id)
    seeds = extract_from_output(output)
    _store_seeds(
        db,
        document.id,
        output.id,
        seeds,
        extractor="local_rule",
        extractor_version=LOCAL_EXTRACTOR_VERSION,
        model_version="none",
    )

    if cloud_client is None or not cloud_client.enabled:
        db.commit()
        return ExtractionStepResult(JobStatus.SUCCESS, None)

    unresolved = [
        key
        for key in cloud_targets()
        if key not in _confident_field_keys(db, application.id)
    ]
    slices = _select_slices(output, unresolved)
    if not slices:
        db.commit()
        return ExtractionStepResult(JobStatus.SUCCESS, None)

    aliases = alias_map(
        application.id,
        _known_names(application, seeds),
        [slice.text for slice in slices],
    )
    redacted: list[RedactedSlice] = []
    try:
        for slice in slices:
            result = redact(application.id, slice.text, aliases)
            verify_redaction(result.text)
            redacted.append(
                RedactedSlice(slice.field_key, slice.label, result.text, slice.source_refs)
            )
    except RedactionError:
        logger.warning("redaction failed application_id=%s code=redaction_failed", application.id)
        # Fail closed: the raw slice must not be persisted anywhere, not even in
        # the restricted audit, so only metadata is recorded.
        _record_call(
            db,
            application.id,
            document.id,
            output.id,
            "redaction_failed",
            "redaction_failed",
            [],
            model=cloud_client.model,
        )
        db.commit()
        return ExtractionStepResult(JobStatus.PARTIAL_SUCCESS, "redaction_failed")

    try:
        cloud_candidates = cloud_client.extract(redacted)
    except CloudExtractionError as exc:
        logger.warning(
            "cloud extraction failed application_id=%s code=%s", application.id, exc.code
        )
        _record_call(
            db,
            application.id,
            document.id,
            output.id,
            "cloud_unavailable",
            exc.code,
            redacted,
            model=cloud_client.model,
        )
        db.commit()
        return ExtractionStepResult(JobStatus.PARTIAL_SUCCESS, exc.code)

    cloud_seeds = _cloud_seeds(cloud_candidates, redacted)
    _store_seeds(
        db,
        document.id,
        output.id,
        cloud_seeds,
        extractor="deepseek",
        extractor_version=cloud_client.extractor_version,
        model_version=cloud_client.model,
        prompt_version=PROMPT_VERSION,
    )
    _record_call(
        db,
        application.id,
        document.id,
        output.id,
        "success",
        None,
        redacted,
        model=cloud_client.model,
        response=[
            {
                "field_key": candidate.field_key,
                "value": candidate.value,
                "confidence": candidate.confidence,
            }
            for candidate in cloud_candidates
        ],
    )
    db.commit()
    return ExtractionStepResult(JobStatus.SUCCESS, None)
