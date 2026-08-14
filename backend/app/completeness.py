"""Completeness templates, pure item evaluation, and immutable formal runs.

Design section 8: templates are versioned by product x primary-borrower type,
support conditional items, and have draft/published/retired states. Published
versions are immutable; copying creates a new draft version. A formal run
freezes the template and the confirmed inputs into an immutable snapshot with a
content hash. Item states are satisfied / missing / pending confirmation /
not applicable / manually waived. Unconfirmed classification, seal candidates,
and signature presence never satisfy an item.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.classification import MaterialCategory
from app.models import (
    ChecklistItem,
    ClassificationConfirmation,
    CompletenessRun,
    CompletenessTemplate,
    Document,
    DocumentChecklistMapping,
    DocumentOutput,
    EvidenceReview,
    ItemState,
    MaterialClassificationCandidate,
    Resolution,
    RunStatus,
    TemplateStatus,
    WaiverRecord,
)

DEMO_CORP_OPERATING = "DEMO-CORP-OPERATING"
DEMO_INDIVIDUAL_OPERATING = "DEMO-INDIVIDUAL-OPERATING"
DEMO_PRODUCT = "经营贷"

COLLATERAL_FIELD_KEYS = {"collateral_type", "collateral_certificate", "collateral_value"}
GUARANTOR_FIELD_KEYS = {"guarantor", "guarantee_method"}

CONDITION_KEYS = ("collateral", "guarantor")
CONDITION_LABELS = {
    "collateral": "存在抵押物时适用",
    "guarantor": "存在保证人时适用",
}


class ItemLike(Protocol):
    code: str
    label: str
    category: str
    requires_seal: bool
    requires_signature: bool
    condition: dict | None


def item_condition_met(item: ItemLike, context: dict[str, bool]) -> bool:
    if not item.condition:
        return True
    requires = item.condition.get("requires")
    if requires not in CONDITION_KEYS:
        return False
    return bool(context.get(requires, False))


def evaluate_item(
    item: ItemLike,
    *,
    waived: bool,
    condition_context: dict[str, bool],
    mapped_documents: set[str],
    confirmed_category: dict[str, str],
    classification_candidates: dict[str, set[str]],
    seal_present: set[str],
    signature_present: set[str],
) -> ItemState:
    """Pure per-item evaluation. Returns the single documented state."""
    if waived:
        return ItemState.MANUALLY_WAIVED
    if not item_condition_met(item, condition_context):
        return ItemState.NOT_APPLICABLE
    if mapped_documents:
        for document_id in mapped_documents:
            if item.requires_seal and document_id not in seal_present:
                return ItemState.PENDING_CONFIRMATION
            if item.requires_signature and document_id not in signature_present:
                return ItemState.PENDING_CONFIRMATION
        return ItemState.SATISFIED
    has_unconfirmed_evidence = any(
        item.category in candidates for candidates in classification_candidates.values()
    )
    has_confirmed_but_unmapped = any(
        category == item.category for category in confirmed_category.values()
    )
    if has_unconfirmed_evidence or has_confirmed_but_unmapped:
        return ItemState.PENDING_CONFIRMATION
    return ItemState.MISSING


def evaluate_items(
    items: list[ItemLike],
    *,
    waivers: set[str],
    condition_context: dict[str, bool],
    mappings: dict[str, set[str]],
    confirmed_category: dict[str, str],
    classification_candidates: dict[str, set[str]],
    seal_present: set[str],
    signature_present: set[str],
) -> dict[str, ItemState]:
    return {
        item.code: evaluate_item(
            item,
            waived=item.code in waivers,
            condition_context=condition_context,
            mapped_documents=mappings.get(item.code, set()),
            confirmed_category=confirmed_category,
            classification_candidates=classification_candidates,
            seal_present=seal_present,
            signature_present=signature_present,
        )
        for item in items
    }


# ---------------------------------------------------------------------------
# Demo templates
# ---------------------------------------------------------------------------

DEMO_ITEMS: dict[str, list[dict]] = {
    DEMO_CORP_OPERATING: [
        {
            "code": "license",
            "label": "营业执照",
            "category": MaterialCategory.BASIC_INFO.value,
            "requires_seal": True,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "legal_rep_id",
            "label": "法定代表人身份证明",
            "category": MaterialCategory.BASIC_INFO.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "financial_statements",
            "label": "上年度财务报表",
            "category": MaterialCategory.OPERATION.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "bank_statement",
            "label": "近6个月银行流水",
            "category": MaterialCategory.OPERATION.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "loan_application",
            "label": "借款申请书",
            "category": MaterialCategory.LOAN_APPLICATION.value,
            "requires_seal": True,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "shareholder_resolution",
            "label": "股东（大）会决议",
            "category": MaterialCategory.LOAN_APPLICATION.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": {"requires": "guarantor"},
        },
        {
            "code": "purpose_contract",
            "label": "购销合同或用途证明材料",
            "category": MaterialCategory.PURPOSE.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "credit_authorization",
            "label": "征信查询授权书",
            "category": MaterialCategory.CREDIT.value,
            "requires_seal": False,
            "requires_signature": True,
            "condition": None,
        },
        {
            "code": "credit_report",
            "label": "企业信用报告",
            "category": MaterialCategory.CREDIT.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "collateral_certificate",
            "label": "抵押物权证",
            "category": MaterialCategory.COLLATERAL.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": {"requires": "collateral"},
        },
        {
            "code": "collateral_appraisal",
            "label": "抵押物评估报告",
            "category": MaterialCategory.COLLATERAL.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": {"requires": "collateral"},
        },
        {
            "code": "guarantor_material",
            "label": "保证人材料",
            "category": MaterialCategory.COLLATERAL.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": {"requires": "guarantor"},
        },
    ],
    DEMO_INDIVIDUAL_OPERATING: [
        {
            "code": "id_card",
            "label": "身份证",
            "category": MaterialCategory.BASIC_INFO.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "household_register",
            "label": "户口本或婚姻状况证明",
            "category": MaterialCategory.BASIC_INFO.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "business_license",
            "label": "营业执照（个体工商户）",
            "category": MaterialCategory.BASIC_INFO.value,
            "requires_seal": True,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "bank_statement",
            "label": "近6个月银行流水",
            "category": MaterialCategory.OPERATION.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "loan_application",
            "label": "借款申请书",
            "category": MaterialCategory.LOAN_APPLICATION.value,
            "requires_seal": True,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "purpose_contract",
            "label": "购销合同或用途证明材料",
            "category": MaterialCategory.PURPOSE.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "credit_authorization",
            "label": "个人征信查询授权书",
            "category": MaterialCategory.CREDIT.value,
            "requires_seal": False,
            "requires_signature": True,
            "condition": None,
        },
        {
            "code": "credit_report",
            "label": "个人信用报告",
            "category": MaterialCategory.CREDIT.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": None,
        },
        {
            "code": "collateral_certificate",
            "label": "抵押物权证",
            "category": MaterialCategory.COLLATERAL.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": {"requires": "collateral"},
        },
        {
            "code": "collateral_appraisal",
            "label": "抵押物评估报告",
            "category": MaterialCategory.COLLATERAL.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": {"requires": "collateral"},
        },
        {
            "code": "guarantor_material",
            "label": "保证人材料",
            "category": MaterialCategory.COLLATERAL.value,
            "requires_seal": False,
            "requires_signature": False,
            "condition": {"requires": "guarantor"},
        },
    ],
}

DEMO_TEMPLATE_SPECS: list[dict] = [
    {
        "code": DEMO_CORP_OPERATING,
        "name": "演示模板：企业经营贷",
        "product": DEMO_PRODUCT,
        "borrower_type": "corporate",
        "demo_only": True,
    },
    {
        "code": DEMO_INDIVIDUAL_OPERATING,
        "name": "演示模板：个人经营贷",
        "product": DEMO_PRODUCT,
        "borrower_type": "individual",
        "demo_only": True,
    },
]


def template_content_hash(template: CompletenessTemplate) -> str:
    payload = template_payload(template)
    return sha256_json(payload)


def template_payload(template: CompletenessTemplate) -> dict:
    return {
        "code": template.code,
        "name": template.name,
        "product": template.product,
        "borrower_type": template.borrower_type,
        "version": template.version,
        "demo_only": template.demo_only,
        "items": [
            {
                "code": item.code,
                "label": item.label,
                "category": item.category,
                "order": item.order,
                "requires_seal": item.requires_seal,
                "requires_signature": item.requires_signature,
                "condition": item.condition,
            }
            for item in sorted(template.items, key=lambda item: item.order)
        ],
    }


def seed_demo_templates(db: Session) -> None:
    """Create and publish the demo-only templates once (idempotent)."""
    for spec in DEMO_TEMPLATE_SPECS:
        existing = (
            db.query(CompletenessTemplate)
            .filter_by(code=spec["code"], status=TemplateStatus.PUBLISHED)
            .first()
        )
        if existing:
            continue
        version = 1
        template = CompletenessTemplate(
            code=spec["code"],
            name=spec["name"],
            product=spec["product"],
            borrower_type=spec["borrower_type"],
            version=version,
            status=TemplateStatus.PUBLISHED,
            demo_only=True,
            content_hash="",
            published_at=datetime.now(UTC),
        )
        for order, item in enumerate(DEMO_ITEMS[spec["code"]], start=1):
            template.items.append(ChecklistItem(order=order, **item))
        template.content_hash = template_content_hash(template)
        db.add(template)
    db.commit()


def validate_template_items(items: list[dict]) -> None:
    codes: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not item.get("code"):
            raise ValueError(f"item {index}: code is required")
        if item["code"] in codes:
            raise ValueError(f"duplicate item code: {item['code']}")
        codes.add(item["code"])
        try:
            MaterialCategory(item["category"])
        except ValueError:
            raise ValueError(f"item {item['code']}: unknown category") from None
        condition = item.get("condition")
        if condition is not None:
            if not isinstance(condition, dict) or condition.get("requires") not in CONDITION_KEYS:
                raise ValueError(
                    f"item {item['code']}: condition must require one of {CONDITION_KEYS}"
                )


def sha256_json(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Formal run snapshots
# ---------------------------------------------------------------------------


def condition_context(db: Session, application_id: str) -> dict[str, bool]:
    field_keys = {
        row[0]
        for row in db.query(Resolution.field_key)
        .filter(Resolution.application_id == application_id)
        .distinct()
    }
    return {
        "collateral": bool(field_keys & COLLATERAL_FIELD_KEYS),
        "guarantor": bool(field_keys & GUARANTOR_FIELD_KEYS),
    }


def confirmed_category_by_document(db: Session, application_id: str) -> dict[str, str]:
    rows = db.query(ClassificationConfirmation).filter_by(application_id=application_id).all()
    return {row.document_id: row.category for row in rows}


def classification_candidates_by_document(db: Session, application_id: str) -> dict[str, set[str]]:
    rows = (
        db.query(MaterialClassificationCandidate, Document.id)
        .join(Document, MaterialClassificationCandidate.document_id == Document.id)
        .filter(Document.application_id == application_id)
        .all()
    )
    grouped: dict[str, set[str]] = {}
    for candidate, document_id in rows:
        grouped.setdefault(document_id, set()).add(candidate.category)
    return grouped


def mappings_by_item_code(db: Session, application_id: str) -> dict[str, set[str]]:
    rows = (
        db.query(DocumentChecklistMapping, ChecklistItem.code)
        .join(ChecklistItem, DocumentChecklistMapping.item_id == ChecklistItem.id)
        .filter(DocumentChecklistMapping.application_id == application_id)
        .all()
    )
    grouped: dict[str, set[str]] = {}
    for mapping, item_code in rows:
        grouped.setdefault(item_code, set()).add(mapping.document_id)
    return grouped


def seal_present_documents(db: Session, application_id: str) -> set[str]:
    return _latest_presence_documents(db, application_id, "seal_presence")


def signature_present_documents(db: Session, application_id: str) -> set[str]:
    return _latest_presence_documents(db, application_id, "signature_presence")


def _latest_presence_documents(db: Session, application_id: str, kind: str) -> set[str]:
    """Documents whose most recent evidence review of ``kind`` says present.

    A newer "absent" or "uncertain" review overrides an older "present" one,
    so stale seal/signature evidence never keeps satisfying an item.
    """
    rows = (
        db.query(Document.id, EvidenceReview.status)
        .join(DocumentOutput, DocumentOutput.document_id == Document.id)
        .join(EvidenceReview, EvidenceReview.output_id == DocumentOutput.id)
        .filter(
            Document.application_id == application_id,
            EvidenceReview.kind == kind,
        )
        .order_by(EvidenceReview.created_at.asc(), EvidenceReview.id.asc())
        .all()
    )
    latest: dict[str, str] = {}
    for document_id, status in rows:
        latest[document_id] = status
    return {document_id for document_id, status in latest.items() if status == "present"}


def waiver_item_codes(db: Session, application_id: str) -> set[str]:
    rows = (
        db.query(WaiverRecord, ChecklistItem.code)
        .join(ChecklistItem, WaiverRecord.item_id == ChecklistItem.id)
        .filter(WaiverRecord.application_id == application_id)
        .all()
    )
    return {item_code for _, item_code in rows}


def mark_runs_stale(db: Session, application_id: str, reason: str) -> None:
    runs = (
        db.query(CompletenessRun)
        .filter_by(application_id=application_id, status=RunStatus.CURRENT)
        .all()
    )
    for run in runs:
        # Staleness lives on a dedicated column so the JSON snapshots and the
        # content hash stay immutable (ADR-0005).
        run.status = RunStatus.STALE
        run.stale_reason = reason
    db.flush()


def build_run_snapshots(
    db: Session,
    application: Any,
    template: CompletenessTemplate,
    actor_id: str,
) -> tuple[dict, dict, dict]:
    """Freeze template, confirmed inputs, and evaluation results."""
    confirmed_category = confirmed_category_by_document(db, application.id)
    candidates = classification_candidates_by_document(db, application.id)
    mappings = mappings_by_item_code(db, application.id)
    seal_present = seal_present_documents(db, application.id)
    signature_present = signature_present_documents(db, application.id)
    waivers = waiver_item_codes(db, application.id)
    context = condition_context(db, application.id)

    documents = (
        db.query(Document)
        .filter_by(application_id=application.id)
        .order_by(Document.created_at)
        .all()
    )
    input_snapshot: dict = {
        "application": {
            "id": application.id,
            "borrower_type": application.borrower_type,
            "borrower_name": application.borrower_name,
            "product": application.product,
            "application_date": application.application_date.isoformat(),
            "proposed_signing_date": (
                application.proposed_signing_date.isoformat()
                if application.proposed_signing_date
                else None
            ),
        },
        "documents": [
            {
                "document_id": document.id,
                "filename": document.filename,
                "confirmed_category": confirmed_category.get(document.id),
                "seal_confirmed": document.id in seal_present,
                "signature_confirmed": document.id in signature_present,
            }
            for document in documents
        ],
        "mappings": sorted(
            {"document_id": document_id, "item_code": item_code}
            for item_code, document_ids in mappings.items()
            for document_id in document_ids
        ),
        "waivers": sorted(
            {"item_code": item_code, "reason": reason}
            for item_code, reason in waiver_reasons(db, application.id).items()
        ),
        "condition_context": context,
        "actor_id": actor_id,
        "created_at": datetime.now(UTC).isoformat(),
    }

    states = evaluate_items(
        template.items,
        waivers=waivers,
        condition_context=context,
        mappings=mappings,
        confirmed_category=confirmed_category,
        classification_candidates=candidates,
        seal_present=seal_present,
        signature_present=signature_present,
    )
    result_snapshot: dict = {
        "items": [
            {
                "item_code": item.code,
                "label": item.label,
                "category": item.category,
                "state": states[item.code].value,
                "evidence_document_ids": sorted(mappings.get(item.code, set())),
                "reason": reason_for_state(item, states[item.code], NOT_APPLICABLE_REASONS),
            }
            for item in template.items
        ]
    }

    template_snapshot = template_payload(template)
    return template_snapshot, input_snapshot, result_snapshot


def waiver_reasons(db: Session, application_id: str) -> dict[str, str]:
    rows = (
        db.query(WaiverRecord, ChecklistItem.code)
        .join(ChecklistItem, WaiverRecord.item_id == ChecklistItem.id)
        .filter(WaiverRecord.application_id == application_id)
        .all()
    )
    return {item_code: waiver.reason for waiver, item_code in rows}


def reason_for_state(item: ChecklistItem, state: ItemState, reasons: dict[str, str]) -> str:
    if state == ItemState.MANUALLY_WAIVED:
        return "人工豁免"
    if state == ItemState.NOT_APPLICABLE and item.condition:
        return reasons.get(item.condition.get("requires", ""), "不适用")
    if state == ItemState.SATISFIED:
        return "证据已确认"
    if state == ItemState.PENDING_CONFIRMATION:
        return "待人工确认"
    return "缺失"


def run_content_hash(template_snapshot: dict, input_snapshot: dict, result_snapshot: dict) -> str:
    return sha256_json(
        {
            "template": template_snapshot,
            "input": input_snapshot,
            "result": result_snapshot,
        }
    )


# ---------------------------------------------------------------------------
# Printable HTML report
# ---------------------------------------------------------------------------

STATE_LABELS = {
    ItemState.SATISFIED: "已满足",
    ItemState.MISSING: "缺失",
    ItemState.PENDING_CONFIRMATION: "待确认",
    ItemState.NOT_APPLICABLE: "不适用",
    ItemState.MANUALLY_WAIVED: "人工豁免",
}

NOT_APPLICABLE_REASONS = {
    "collateral": "不适用：申请无抵押物",
    "guarantor": "不适用：申请无保证人",
}


def _esc(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_printable_html(run: CompletenessRun, actor_username: str) -> str:
    template = run.template_snapshot
    result = run.result_snapshot
    input_snapshot = run.input_snapshot
    rows = "".join(
        f"""
        <tr>
          <td>{_esc(item["item_code"])}</td>
          <td>{_esc(item["label"])}</td>
          <td>{_esc(item["category"])}</td>
          <td class="state">{_esc(STATE_LABELS.get(ItemState(item["state"]), item["state"]))}</td>
          <td>{_esc(item["reason"])}</td>
        </tr>"""
        for item in result["items"]
    )
    document_rows = "".join(
        f"""
        <li>
          <a href="/documents/{_esc(document["document_id"])}/evidence">
            {_esc(document["filename"])}
          </a>
          （分类：{_esc(document.get("confirmed_category") or "未确认")}
          · 印章：{"已确认" if document.get("seal_confirmed") else "未确认"}
          · 签字：{"已确认" if document.get("signature_confirmed") else "未确认"}）
        </li>"""
        for document in input_snapshot.get("documents", [])
    )
    waiver_rows = "".join(
        f"<li>（{_esc(waiver['item_code'])}）{_esc(waiver['reason'])}</li>"
        for waiver in input_snapshot.get("waivers", [])
    )
    gaps = [
        item
        for item in result["items"]
        if item["state"] in {ItemState.MISSING.value, ItemState.PENDING_CONFIRMATION.value}
    ]
    gap_rows = "".join(
        f"<li>{_esc(item['label'])}（{_esc(item['item_code'])}）</li>" for item in gaps
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>材料完备性正式报告 - {_esc(input_snapshot["application"]["borrower_name"])}</title>
<style>
  body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif; margin: 24px; color: #222; }}
  h1 {{ font-size: 20px; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; font-size: 13px; }}
  th {{ background: #f2f2f2; }}
  .state {{ font-weight: bold; }}
  .disclaimer {{ border: 1px solid #d9b38c; background: #fdf3e7; padding: 10px; }}
  .muted {{ color: #777; font-size: 12px; }}
</style>
</head>
<body>
  <h1>材料完备性正式报告</h1>
  <div class="disclaimer">本报告仅供审批辅助，需人工复核；不构成任何审批决定或法律意见。</div>
  <p class="muted">
    生成时间：{_esc(run.created_at.isoformat())} · 操作者：{_esc(actor_username)} ·
    报告编号：{_esc(run.id)} · 内容哈希：{_esc(run.content_hash)}
  </p>
  <h2>模板</h2>
  <p>
    模板：{_esc(template["name"])}（{_esc(template["code"])} · 版本 v{_esc(template["version"])}）
    · 产品：{_esc(template["product"])} · 主借款人类型：{_esc(template["borrower_type"])}
    · 演示模板：{"是" if template.get("demo_only") else "否"}
  </p>
  <h2>清单结果</h2>
  <table>
    <thead><tr><th>编号</th><th>清单项</th><th>类别</th><th>状态</th><th>说明</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>缺件与待确认</h2>
  <ul>{gap_rows or "<li>无</li>"}</ul>
  <h2>人工豁免</h2>
  <ul>{waiver_rows or "<li>无</li>"}</ul>
  <h2>输入材料与证据</h2>
  <ul>{document_rows or "<li>无</li>"}</ul>
  <p class="muted">
    输入快照与结果快照的完整 JSON 可通过 API 获取；映射或模板版本变化会使本报告失效。
  </p>
</body>
</html>"""
