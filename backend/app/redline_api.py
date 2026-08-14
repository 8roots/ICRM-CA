"""Application redline: confirmed rule context, live preview, formal runs.

Everything here is scoped to the application owner. The live preview is
computed on read from confirmed inputs and the deployment lender profile; a
formal run freezes rule + inputs + steps into an immutable snapshot with a
content hash. Any change to a critical resolution, the rule context, or the
application product/proposed signing date marks the current run stale, and a
new applicable rule or a changed LPR selection also makes it stale via live
checks. The report never claims the system found the loan legally compliant.
"""

import hashlib
import json
from datetime import UTC, date, datetime

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import Csrf, CurrentUser, Db
from app.idempotency import add_idempotency_record, replay_resource_id
from app.models import (
    Application,
    RedlineRun,
    RuleContextConfirmation,
    RulePackage,
    RunStatus,
    User,
)
from app.redline import (
    CALC_LABELS,
    RATE_LABELS,
    REFERENCE_STATE_LABELS,
    STATE_LABELS,
    RedlineState,
    build_run_snapshots,
    confirmed_resolutions,
    confirmed_rule_context,
    critical_fields_for,
    current_lpr_entries,
    current_lpr_entry,
    evaluate_redline,
    mark_runs_stale,
    render_printable_html,
    run_content_hash,
    select_primary_rule,
)

router = APIRouter(prefix="/applications", tags=["redline"])


def owned_application(db: Db, application_id: str, owner_id: str) -> Application:
    application = db.query(Application).filter_by(id=application_id, owner_id=owner_id).first()
    if not application:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    return application


class RuleResponse(BaseModel):
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


class EvaluationResponse(BaseModel):
    state: str
    state_label: str
    steps: list[dict]
    metrics: dict
    missing_inputs: list[str]
    reason: str


class LprInfoResponse(BaseModel):
    entry_id: str | None
    effective_date: str | None
    value: str | None
    provisional: bool
    as_of_date: str | None


class ConfirmedInputResponse(BaseModel):
    field_key: str
    label: str
    value: str | None
    raw_text: str | None
    manual: bool


class SelectionResponse(BaseModel):
    reason: str
    rule: RuleResponse | None
    candidates: list[RuleResponse]
    explanation: str


class LiveRedlineResponse(BaseModel):
    rule_context: str | None
    selection: SelectionResponse
    references: list[dict]
    lpr: LprInfoResponse
    evaluation_date: str
    critical: dict
    state: str
    state_label: str
    primary: EvaluationResponse | None
    latest_run: "RunSummaryResponse | None"
    formal_run_blocked_reason: str | None


class RuleContextConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: str = Field(min_length=1, max_length=100)


class RuleContextResponse(BaseModel):
    application_id: str
    context: str
    actor_id: str
    created_at: datetime


class RunSummaryResponse(BaseModel):
    id: str
    created_at: datetime
    status: str
    stale: bool
    stale_reason: str | None
    content_hash: str
    rule_code: str | None
    rule_version: int | None
    state: str
    actor_id: str


class RunDetailResponse(BaseModel):
    id: str
    application_id: str
    rule_id: str | None
    rule_snapshot: dict | None
    input_snapshot: dict
    result_snapshot: dict
    content_hash: str
    status: str
    stale: bool
    stale_reason: str | None
    actor_id: str
    created_at: datetime


def as_rule(rule: RulePackage) -> RuleResponse:
    return RuleResponse(
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
    )


def as_evaluation(evaluation, *, reference: bool = False) -> EvaluationResponse:
    if evaluation is None:
        return EvaluationResponse(
            state=RedlineState.INDETERMINATE,
            state_label=STATE_LABELS[RedlineState.INDETERMINATE],
            steps=[],
            metrics={},
            missing_inputs=[],
            reason="无法确定适用规则",
        )
    labels = REFERENCE_STATE_LABELS if reference else STATE_LABELS
    return EvaluationResponse(
        state=evaluation.state,
        state_label=labels.get(evaluation.state, evaluation.state),
        steps=evaluation.steps,
        metrics=evaluation.metrics,
        missing_inputs=evaluation.missing_inputs,
        reason=evaluation.reason,
    )


def _lpr_info(result) -> LprInfoResponse:
    entry = result.lpr_entry
    return LprInfoResponse(
        entry_id=entry.id if hasattr(entry, "id") else None,
        effective_date=entry.effective_date.isoformat() if entry else None,
        value=entry.value if entry else None,
        provisional=result.lpr_provisional,
        as_of_date=result.lpr_as_of_date.isoformat(),
    )


SELECTION_EXPLANATIONS = {
    "unique": "唯一主规则包匹配",
    "no_rule_context": "尚未确认规则上下文，无法确定适用规则",
    "no_match": "无匹配的已批准主规则包，无法确定适用规则",
    "multiple_match": "多个主规则包匹配，无法确定适用规则",
}


def _selection_response(selection, packages_by_id: dict[str, RulePackage]) -> SelectionResponse:
    rule = None
    if selection.package is not None:
        rule = as_rule(packages_by_id.get(selection.package.id) or selection.package)
    candidates = [
        as_rule(packages_by_id.get(package.id) or package) for package in selection.candidates
    ]
    return SelectionResponse(
        reason=selection.reason,
        rule=rule,
        candidates=candidates,
        explanation=SELECTION_EXPLANATIONS.get(selection.reason, selection.reason),
    )


def _critical_payload(
    db: Db, rule: RulePackage | None, confirmed: dict[str, dict], evaluation=None
) -> dict:
    if rule is None:
        return {"missing": [], "confirmed": []}
    critical = critical_fields_for(rule)
    if evaluation is not None:
        # Align the alert with the actual evaluation: presence alone is not
        # enough, a confirmed-but-unparseable value is still missing input.
        missing = sorted(key for key in evaluation.missing_inputs if key in critical)
    else:
        missing = sorted(critical - set(confirmed))
    confirmed_items = [
        {
            "field_key": key,
            "label": RATE_LABELS.get(key, key),
            "value": confirmed[key].get("value"),
            "raw_text": confirmed[key].get("raw_text"),
            "manual": confirmed[key].get("manual", False),
        }
        for key in sorted(critical & set(confirmed))
    ]
    return {"missing": missing, "confirmed": confirmed_items}


def _live_result(db: Db, application: Application):
    packages = db.query(RulePackage).all()
    packages_by_id = {package.id: package for package in packages}
    evaluation_date = datetime.now(UTC).date()
    rule_context = confirmed_rule_context(db, application.id)
    confirmed = confirmed_resolutions(db, application.id)
    result = evaluate_redline(
        packages,
        current_lpr_entries(db),
        lender_qualification=application_lender_qualification(db),
        rule_context=rule_context,
        product=application.product,
        evaluation_date=evaluation_date,
        proposed_signing_date=application.proposed_signing_date,
        confirmed=confirmed,
    )
    return result, packages_by_id, confirmed, evaluation_date


def application_lender_qualification(db: Db) -> str:
    from app.config import settings

    return settings.lender_qualification


@router.get("/{application_id}/redline", response_model=LiveRedlineResponse)
def get_redline(
    application_id: str, request: Request, db: Db, user: CurrentUser
) -> LiveRedlineResponse:
    application = owned_application(db, application_id, user.id)
    result, packages_by_id, confirmed, evaluation_date = _live_result(db, application)
    selection = result.selection
    rule = selection.package
    primary = result.primary
    references = [
        {
            "rule": as_rule(packages_by_id.get(reference.id) or reference),
            "evaluation": as_evaluation(evaluation, reference=True),
        }
        for reference, evaluation in result.references
    ]
    blocked = None
    if request.app.state.production and any(
        item.demo_only for item in ([rule] if rule else []) + [r for r, _ in result.references]
    ):
        blocked = "生产模式拒绝使用演示规则生成正式报告"
    latest_run = (
        db.query(RedlineRun)
        .filter_by(application_id=application_id)
        .order_by(RedlineRun.created_at.desc())
        .first()
    )
    return LiveRedlineResponse(
        rule_context=confirmed_rule_context(db, application_id),
        selection=_selection_response(selection, packages_by_id),
        references=references,
        lpr=_lpr_info(result),
        evaluation_date=evaluation_date.isoformat(),
        critical=_critical_payload(db, rule, confirmed, result.primary),
        state=result.state,
        state_label=STATE_LABELS.get(result.state, result.state),
        primary=as_evaluation(primary),
        latest_run=as_run_summary(latest_run, db, application) if latest_run else None,
        formal_run_blocked_reason=blocked,
    )


@router.post(
    "/{application_id}/rule-context",
    response_model=RuleContextResponse,
    status_code=status.HTTP_201_CREATED,
)
def confirm_rule_context(
    application_id: str,
    payload: RuleContextConfirmRequest,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> RuleContextResponse:
    owned_application(db, application_id, user.id)
    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    operation = f"confirm_rule_context:{application_id}"
    replay_id = replay_resource_id(db, user.id, operation, idempotency_key, request_hash)
    if replay_id:
        response.status_code = status.HTTP_200_OK
        return _as_context(db.get(RuleContextConfirmation, replay_id))
    confirmation = (
        db.query(RuleContextConfirmation).filter_by(application_id=application_id).first()
    )
    if confirmation:
        confirmation.context = payload.context.strip()
        confirmation.actor_id = user.id
    else:
        confirmation = RuleContextConfirmation(
            application_id=application_id,
            context=payload.context.strip(),
            actor_id=user.id,
        )
        db.add(confirmation)
    db.flush()
    add_idempotency_record(db, user.id, operation, idempotency_key, request_hash, confirmation.id)
    mark_runs_stale(db, application_id, "rule_context_change")
    db.commit()
    return _as_context(confirmation)


def _as_context(confirmation: RuleContextConfirmation) -> RuleContextResponse:
    return RuleContextResponse(
        application_id=confirmation.application_id,
        context=confirmation.context,
        actor_id=confirmation.actor_id,
        created_at=confirmation.created_at,
    )


def run_staleness(run: RedlineRun, db: Db, application: Application) -> tuple[bool, str | None]:
    """Stored staleness plus live rule/LPR change checks (ADR-0005).

    The rule check compares today's unique selection against the frozen rule:
    a newly approved rule that now applies makes the run update-available.
    The LPR check re-selects the LPR for the run's frozen as-of date when the
    run's primary rule or any reference consumes LPR.
    """
    stale_reason = run.stale_reason
    if run.status == RunStatus.CURRENT:
        packages = db.query(RulePackage).all()
        rule_context = confirmed_rule_context(db, application.id)
        evaluation_date = datetime.now(UTC).date()
        current = select_primary_rule(
            packages,
            lender_qualification=application_lender_qualification(db),
            rule_context=rule_context,
            product=application.product,
            as_of_date=evaluation_date,
        )
        current_id = current.package.id if current.package else None
        if current_id != run.rule_id:
            stale_reason = "rule_changed"
        else:
            uses_lpr = (run.rule_snapshot or {}).get("calc_type") == "lpr_multiple_limit"
            uses_lpr = uses_lpr or any(
                reference["rule"].get("calc_type") == "lpr_multiple_limit"
                for reference in run.result_snapshot.get("references", [])
            )
            if uses_lpr:
                lpr_info = run.input_snapshot.get("lpr") or {}
                as_of_text = lpr_info.get("as_of_date") or run.input_snapshot.get(
                    "evaluation_date"
                )
                as_of = date.fromisoformat(as_of_text)
                current_lpr = current_lpr_entry(db, as_of)
                run_lpr_id = lpr_info.get("entry_id")
                if current_lpr is not None and current_lpr.id != run_lpr_id:
                    stale_reason = "lpr_changed"
                elif current_lpr is None and run_lpr_id:
                    stale_reason = "lpr_changed"
    return run.status == RunStatus.STALE or stale_reason is not None, stale_reason


def as_run_summary(run: RedlineRun, db: Db, application: Application) -> RunSummaryResponse:
    stale, stale_reason = run_staleness(run, db, application)
    rule = run.rule_snapshot or {}
    return RunSummaryResponse(
        id=run.id,
        created_at=run.created_at,
        status=run.status,
        stale=stale,
        stale_reason=stale_reason,
        content_hash=run.content_hash,
        rule_code=rule.get("code"),
        rule_version=rule.get("version"),
        state=run.result_snapshot.get("state", ""),
        actor_id=run.actor_id,
    )


def as_run_detail(run: RedlineRun, db: Db, application: Application) -> RunDetailResponse:
    stale, stale_reason = run_staleness(run, db, application)
    return RunDetailResponse(
        id=run.id,
        application_id=run.application_id,
        rule_id=run.rule_id,
        rule_snapshot=run.rule_snapshot,
        input_snapshot=run.input_snapshot,
        result_snapshot=run.result_snapshot,
        content_hash=run.content_hash,
        status=run.status,
        stale=stale,
        stale_reason=stale_reason,
        actor_id=run.actor_id,
        created_at=run.created_at,
    )


@router.post(
    "/{application_id}/redline-runs",
    response_model=RunDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_redline_run(
    application_id: str,
    request: Request,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
) -> RunDetailResponse:
    application = owned_application(db, application_id, user.id)
    result, packages_by_id, confirmed, evaluation_date = _live_result(db, application)
    rule = result.selection.package
    demo_rules = [rule] if rule and rule.demo_only else []
    demo_rules += [reference for reference, _ in result.references if reference.demo_only]
    if demo_rules and request.app.state.production:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Production mode rejects demo rules for formal reports",
        )
    rule_snapshot, input_snapshot, result_snapshot = build_run_snapshots(db, application, user.id)
    request_hash = hashlib.sha256(
        json.dumps(
            {
                "application_id": application_id,
                "rule": rule_snapshot,
                "input": {
                    key: input_snapshot[key]
                    for key in (
                        "lender_qualification",
                        "rule_context",
                        "evaluation_date",
                        "lpr",
                        "confirmed_inputs",
                    )
                },
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    operation = f"create_redline_run:{application_id}"
    replay_id = replay_resource_id(db, user.id, operation, idempotency_key, request_hash)
    if replay_id:
        response.status_code = status.HTTP_200_OK
        return as_run_detail(db.get(RedlineRun, replay_id), db, application)
    run = RedlineRun(
        application_id=application_id,
        rule_id=rule.id if rule else None,
        rule_snapshot=rule_snapshot,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        content_hash=run_content_hash(rule_snapshot, input_snapshot, result_snapshot),
        status=RunStatus.CURRENT,
        actor_id=user.id,
    )
    mark_runs_stale(db, application_id, "new_run")
    db.add(run)
    db.flush()
    add_idempotency_record(db, user.id, operation, idempotency_key, request_hash, run.id)
    db.commit()
    return as_run_detail(run, db, application)


@router.get("/{application_id}/redline-runs", response_model=list[RunSummaryResponse])
def list_redline_runs(application_id: str, db: Db, user: CurrentUser) -> list[RunSummaryResponse]:
    application = owned_application(db, application_id, user.id)
    runs = (
        db.query(RedlineRun)
        .filter_by(application_id=application_id)
        .order_by(RedlineRun.created_at.desc())
        .all()
    )
    return [as_run_summary(run, db, application) for run in runs]


def owned_run(db: Db, application_id: str, run_id: str) -> RedlineRun:
    run = db.query(RedlineRun).filter_by(id=run_id, application_id=application_id).first()
    if not run:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Redline run not found")
    return run


@router.get("/{application_id}/redline-runs/{run_id}", response_model=RunDetailResponse)
def get_redline_run(
    application_id: str, run_id: str, db: Db, user: CurrentUser
) -> RunDetailResponse:
    application = owned_application(db, application_id, user.id)
    return as_run_detail(owned_run(db, application_id, run_id), db, application)


@router.get("/{application_id}/redline-runs/{run_id}/printable")
def printable_redline_run(application_id: str, run_id: str, db: Db, user: CurrentUser) -> Response:
    owned_application(db, application_id, user.id)
    run = owned_run(db, application_id, run_id)
    actor = db.get(User, run.actor_id)
    html = render_printable_html(run, actor.username if actor else run.actor_id)
    return Response(content=html, media_type="text/html; charset=utf-8")
