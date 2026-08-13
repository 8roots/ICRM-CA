"""Completeness template lifecycle, confirmations, formal runs, and gates."""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import (
    Base,
    ChecklistItem,
    CompletenessTemplate,
    Document,
    DocumentOutput,
    TemplateStatus,
    User,
)
from app.security import hash_password

DEMO_ITEMS = {
    "license": {"requires_seal": True},
    "credit_authorization": {"requires_signature": True},
}


class MemoryObjects:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key: str, stream, length: int) -> None:
        chunks = []
        remaining = length
        while remaining != 0:
            chunk = stream.read(65536 if remaining < 0 else min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            if remaining > 0:
                remaining -= len(chunk)
        self.objects[key] = b"".join(chunks)

    def open(self, key: str):
        return io.BytesIO(self.objects[key])

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def setup(*, production: bool = False) -> TestClient:
    app = create_app(
        "sqlite+pysqlite:///:memory:",
        cookie_secure=True,
        production=production,
        object_store=MemoryObjects(),
    )
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session() as db:
        admin = User(
            username="admin",
            password_hash=hash_password("administrator password"),
            role="administrator",
            enabled=True,
        )
        owner = User(
            username="owner",
            password_hash=hash_password("approval officer password"),
            role="approval_officer",
            enabled=True,
        )
        other = User(
            username="other",
            password_hash=hash_password("approval officer password"),
            role="approval_officer",
            enabled=True,
        )
        db.add_all([admin, owner, other])
        db.flush()
        for username, borrower_type in (("owner", "corporate"), ("other", "corporate")):
            from app.models import Application

            db.add(
                Application(
                    borrower_type=borrower_type,
                    borrower_name=f"{username}企业",
                    product="经营贷",
                    application_date=date(2026, 8, 7),
                    owner_id=owner.id if username == "owner" else other.id,
                )
            )
        db.commit()
    return TestClient(app, base_url="https://testserver")


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": username, "password": password},
    )
    assert response.status_code == 204
    return {"X-CSRF-Token": client.cookies["icrm_csrf"]}


@pytest.fixture
def client() -> TestClient:
    client = setup()
    with client:
        yield client


def published_template(db, product: str = "经营贷", borrower_type: str = "corporate"):
    return (
        db.query(CompletenessTemplate)
        .filter_by(
            product=product,
            borrower_type=borrower_type,
            status=TemplateStatus.PUBLISHED,
        )
        .one()
    )


def item_by_code(template: CompletenessTemplate, code: str) -> ChecklistItem:
    return next(item for item in template.items if item.code == code)


def add_document(db, application_id: str, filename: str, suffix: str) -> Document:
    document = Document(
        application_id=application_id,
        filename=filename,
        extension=".pdf",
        declared_mime="application/pdf",
        size_bytes=1,
        sha256=suffix * 64,
        object_key=suffix,
    )
    db.add(document)
    db.flush()
    return document


# ---------------------------------------------------------------------------
# Seeding and template lookup
# ---------------------------------------------------------------------------


def test_demo_templates_are_seeded_and_published(client: TestClient) -> None:
    with client.app.state.database.session() as db:
        corp = published_template(db)
        individual = published_template(db, borrower_type="individual")
        assert corp.code == "DEMO-CORP-OPERATING"
        assert individual.code == "DEMO-INDIVIDUAL-OPERATING"
        assert corp.demo_only is True
        assert {item.code for item in corp.items} >= set(DEMO_ITEMS)


def test_live_draft_reports_no_template_for_unmatched_product(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application

        application = (
            db.query(Application).filter_by(borrower_name="owner企业").one()
        )
        application.product = "汽车金融"
        db.commit()
        application_id = application.id
    draft = client.get(f"/api/v1/applications/{application_id}/completeness")
    assert draft.status_code == 200
    body = draft.json()
    assert body["template"] is None
    assert "没有适用于" in body["no_template_reason"]
    assert body["formal_run_blocked_reason"] == "无已发布适用模板"
    run = client.post(
        f"/api/v1/applications/{application_id}/completeness-runs",
        headers={**csrf, "Idempotency-Key": "run"},
    )
    assert run.status_code == 422


def test_live_draft_initial_states(client: TestClient) -> None:
    login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application

        application_id = (
            db.query(Application).filter_by(borrower_name="owner企业").one().id
        )
    draft = client.get(f"/api/v1/applications/{application_id}/completeness")
    body = draft.json()
    assert body["template"]["code"] == "DEMO-CORP-OPERATING"
    states = {item["code"]: item["state"] for item in body["items"]}
    assert states["license"] == "missing"
    assert states["credit_authorization"] == "missing"
    # conditional items start not applicable without collateral/guarantor context
    assert states["collateral_certificate"] == "not_applicable"
    assert states["guarantor_material"] == "not_applicable"
    assert body["latest_run"] is None
    assert body["formal_run_blocked_reason"] is None


# ---------------------------------------------------------------------------
# Admin template lifecycle
# ---------------------------------------------------------------------------


def test_admin_template_lifecycle_and_immutability(client: TestClient) -> None:
    csrf = login(client, "admin", "administrator password")
    created = client.post(
        "/api/v1/admin/completeness-templates",
        headers=csrf,
        json={
            "code": "CORP-OPERATING-2026",
            "name": "企业流动资金贷清单",
            "product": "流动资金贷",
            "borrower_type": "corporate",
            "demo_only": False,
            "items": [
                {
                    "code": "license",
                    "label": "营业执照",
                    "category": "basic_info",
                    "requires_seal": True,
                    "requires_signature": False,
                    "condition": None,
                },
                {
                    "code": "collateral_certificate",
                    "label": "抵押物权证",
                    "category": "collateral",
                    "condition": {"requires": "collateral"},
                },
            ],
        },
    )
    assert created.status_code == 201
    template_id = created.json()["id"]
    assert created.json()["status"] == "draft"
    assert created.json()["version"] == 1
    assert created.json()["content_hash"]

    # published versions cannot be edited: PUT on a published template is rejected
    published = client.post(
        f"/api/v1/admin/completeness-templates/{template_id}/publish", headers=csrf
    )
    assert published.status_code == 200
    assert published.json()["status"] == "published"
    no_update = client.put(
        f"/api/v1/admin/completeness-templates/{template_id}",
        headers=csrf,
        json={
            "name": "篡改",
            "items": [{"code": "license", "label": "x", "category": "basic_info"}],
        },
    )
    assert no_update.status_code == 409

    # a second publish is rejected
    assert (
        client.post(
            f"/api/v1/admin/completeness-templates/{template_id}/publish", headers=csrf
        ).status_code
        == 409
    )

    # another published version for the same product x borrower type is rejected
    duplicate = client.post(
        "/api/v1/admin/completeness-templates",
        headers=csrf,
        json={
            "code": "CORP-OPERATING-OTHER",
            "name": "重复清单",
            "product": "流动资金贷",
            "borrower_type": "corporate",
            "demo_only": False,
            "items": [
                {
                    "code": "license",
                    "label": "营业执照",
                    "category": "basic_info",
                }
            ],
        },
    )
    assert duplicate.status_code == 201
    assert (
        client.post(
            f"/api/v1/admin/completeness-templates/{duplicate.json()['id']}/publish",
            headers=csrf,
        ).status_code
        == 409
    )

    # copy to change creates a new draft version, keeping the code, and the
    # draft is editable before it is published
    copied = client.post(
        f"/api/v1/admin/completeness-templates/{template_id}/copy", headers=csrf
    )
    assert copied.status_code == 201
    assert copied.json()["code"] == "CORP-OPERATING-2026"
    assert copied.json()["version"] == 2
    assert copied.json()["status"] == "draft"
    edited = client.put(
        f"/api/v1/admin/completeness-templates/{copied.json()['id']}",
        headers=csrf,
        json={
            "name": "企业流动资金贷清单（2026 修订）",
            "items": [
                {
                    "code": "license",
                    "label": "营业执照（修订）",
                    "category": "basic_info",
                    "requires_seal": True,
                    "requires_signature": False,
                    "condition": None,
                }
            ],
        },
    )
    assert edited.status_code == 200
    assert edited.json()["status"] == "draft"
    assert edited.json()["name"] == "企业流动资金贷清单（2026 修订）"
    assert edited.json()["items"][0]["label"] == "营业执照（修订）"

    retired = client.post(
        f"/api/v1/admin/completeness-templates/{template_id}/retire", headers=csrf
    )
    assert retired.status_code == 200
    assert retired.json()["status"] == "retired"
    # retiring a retired/draft version is rejected
    assert (
        client.post(
            f"/api/v1/admin/completeness-templates/{copied.json()['id']}/retire",
            headers=csrf,
        ).status_code
        == 409
    )


def test_admin_create_validates_items_and_code(client: TestClient) -> None:
    csrf = login(client, "admin", "administrator password")
    bad_condition = client.post(
        "/api/v1/admin/completeness-templates",
        headers=csrf,
        json={
            "code": "BAD-CONDITION",
            "name": "坏条件",
            "product": "X",
            "borrower_type": "corporate",
            "items": [
                {
                    "code": "item",
                    "label": "项",
                    "category": "basic_info",
                    "condition": {"requires": "moon_phase"},
                }
            ],
        },
    )
    assert bad_condition.status_code == 422
    bad_code = client.post(
        "/api/v1/admin/completeness-templates",
        headers=csrf,
        json={
            "code": "lower case",
            "name": "坏编码",
            "product": "X",
            "borrower_type": "corporate",
            "items": [{"code": "item", "label": "项", "category": "basic_info"}],
        },
    )
    assert bad_code.status_code == 422
    dup_items = client.post(
        "/api/v1/admin/completeness-templates",
        headers=csrf,
        json={
            "code": "DUP-ITEMS",
            "name": "重复项",
            "product": "X",
            "borrower_type": "corporate",
            "items": [
                {"code": "a", "label": "A", "category": "basic_info"},
                {"code": "a", "label": "B", "category": "basic_info"},
            ],
        },
    )
    assert dup_items.status_code == 422


def test_officer_cannot_manage_templates(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    assert (
        client.get("/api/v1/admin/completeness-templates").status_code == 403
    )
    assert (
        client.post(
            "/api/v1/admin/completeness-templates",
            headers=csrf,
            json={
                "code": "NOPE",
                "name": "nope",
                "product": "X",
                "borrower_type": "corporate",
                "items": [{"code": "a", "label": "A", "category": "basic_info"}],
            },
        ).status_code
        == 403
    )


# ---------------------------------------------------------------------------
# Officer confirmation flow and formal runs
# ---------------------------------------------------------------------------


def test_classification_mapping_waiver_and_formal_run(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application

        application_id = (
            db.query(Application).filter_by(borrower_name="owner企业").one().id
        )
        document = add_document(db, application_id, "执照扫描.pdf", "c")
        document_id = document.id
        template = published_template(db)
        license_item = item_by_code(template, "license")
        purpose_item = item_by_code(template, "purpose_contract")
        db.commit()
        license_item_id = license_item.id
        purpose_item_id = purpose_item.id

    # unconfirmed classification pends the item, never satisfies it
    with client.app.state.database.session() as db:
        from app.models import MaterialClassificationCandidate

        db.add(
            MaterialClassificationCandidate(
                document_id=document_id,
                category="basic_info",
                confidence=0.9,
                method="content_keyword",
                method_version="1",
            )
        )
        db.commit()
    draft = client.get(f"/api/v1/applications/{application_id}/completeness").json()
    doc = next(item for item in draft["documents"] if item["id"] == document_id)
    assert doc["confirmed_category"] is None
    assert [c["category"] for c in doc["classification_candidates"]] == ["basic_info"]
    states = {item["code"]: item["state"] for item in draft["items"]}
    # license requires seal, but there is no confirmed mapping at all
    assert states["license"] == "pending_confirmation"

    confirmed = client.post(
        f"/api/v1/applications/{application_id}/documents/{document_id}/classification",
        headers={**csrf, "Idempotency-Key": "classify-license"},
        json={"category": "basic_info"},
    )
    assert confirmed.status_code == 201
    draft = client.get(f"/api/v1/applications/{application_id}/completeness").json()
    doc = next(item for item in draft["documents"] if item["id"] == document_id)
    assert doc["confirmed_category"] == "basic_info"

    # mapping to an item with a seal requirement still pends until the seal is
    # confirmed; a plain item with the mapping is satisfied
    mapped = client.post(
        f"/api/v1/applications/{application_id}/mappings",
        headers={**csrf, "Idempotency-Key": "map-license"},
        json={"document_id": document_id, "item_id": license_item_id},
    )
    assert mapped.status_code == 201
    states = {
        item["code"]: item["state"]
        for item in client.get(
            f"/api/v1/applications/{application_id}/completeness"
        ).json()["items"]
    }
    assert states["license"] == "pending_confirmation"

    # seal confirmation via the evidence review API satisfies the item
    with client.app.state.database.session() as db:
        from app.models import DocumentPage, SealCandidate

        output = DocumentOutput(
            document_id=document_id,
            version=1,
            status="success",
            parser_version="test",
            model_version="none",
        )
        db.add(output)
        db.flush()
        page = DocumentPage(
            output_id=output.id,
            number=1,
            status="success",
        )
        db.add(page)
        db.flush()
        seal = SealCandidate(
            page_id=page.id,
            text="示例公司",
            x0=0,
            y0=0,
            x1=10,
            y1=10,
            confidence=0.9,
            model_version="test",
        )
        db.add(seal)
        db.flush()
        output_id = output.id
        seal_id = seal.id
        db.commit()
    review = client.post(
        f"/api/v1/document-outputs/{output_id}/reviews",
        headers={**csrf, "Idempotency-Key": "seal-review"},
        json={
            "kind": "seal_presence",
            "status": "present",
            "seal_candidate_id": seal_id,
            "reason": "原页可见公章",
        },
    )
    assert review.status_code == 201
    states = {
        item["code"]: item["state"]
        for item in client.get(
            f"/api/v1/applications/{application_id}/completeness"
        ).json()["items"]
    }
    assert states["license"] == "satisfied"

    # waiver overrides a missing item with an audit trail
    waived = client.post(
        f"/api/v1/applications/{application_id}/waivers",
        headers={**csrf, "Idempotency-Key": "waiver-purpose"},
        json={"item_id": purpose_item_id, "reason": "客户暂未提供，已线下核实"},
    )
    assert waived.status_code == 201
    assert waived.json()["reason"] == "客户暂未提供，已线下核实"
    states = {
        item["code"]: item["state"]
        for item in client.get(
            f"/api/v1/applications/{application_id}/completeness"
        ).json()["items"]
    }
    assert states["purpose_contract"] == "manually_waived"

    # formal run freezes the snapshot and hash
    run = client.post(
        f"/api/v1/applications/{application_id}/completeness-runs",
        headers={**csrf, "Idempotency-Key": "formal-1"},
    )
    assert run.status_code == 201
    body = run.json()
    assert body["content_hash"]
    assert body["template_snapshot"]["code"] == "DEMO-CORP-OPERATING"
    result_states = {
        item["item_code"]: item["state"] for item in body["result_snapshot"]["items"]
    }
    assert result_states["license"] == "satisfied"
    assert result_states["purpose_contract"] == "manually_waived"
    assert result_states["collateral_certificate"] == "not_applicable"
    assert body["input_snapshot"]["documents"][0]["seal_confirmed"] is True
    assert body["input_snapshot"]["waivers"][0]["item_code"] == "purpose_contract"
    assert body["stale"] is False

    # idempotent replay returns the same run
    replay = client.post(
        f"/api/v1/applications/{application_id}/completeness-runs",
        headers={**csrf, "Idempotency-Key": "formal-1"},
    )
    assert replay.status_code == 200
    assert replay.json()["id"] == body["id"]

    # mapping change marks the run stale; rerun keeps history
    mapped_delete = client.delete(
        f"/api/v1/applications/{application_id}/mappings/{mapped.json()['id']}",
        headers=csrf,
    )
    assert mapped_delete.status_code == 204
    runs = client.get(
        f"/api/v1/applications/{application_id}/completeness-runs"
    ).json()
    assert runs[0]["id"] == body["id"]
    assert runs[0]["stale"] is True
    assert runs[0]["stale_reason"] == "mapping_change"

    rerun = client.post(
        f"/api/v1/applications/{application_id}/completeness-runs",
        headers={**csrf, "Idempotency-Key": "formal-2"},
    )
    assert rerun.status_code == 201
    assert rerun.json()["id"] != body["id"]
    runs = client.get(
        f"/api/v1/applications/{application_id}/completeness-runs"
    ).json()
    assert {run["id"] for run in runs} == {body["id"], rerun.json()["id"]}
    assert runs[0]["stale"] is False

    # printable HTML includes disclaimer, template version, gaps, and hash
    printable = client.get(
        f"/api/v1/applications/{application_id}/completeness-runs/{rerun.json()['id']}/printable"
    )
    assert printable.status_code == 200
    html = printable.text
    assert "仅供审批辅助，需人工复核" in html
    assert "DEMO-CORP-OPERATING" in html
    assert "人工豁免" in html
    assert rerun.json()["content_hash"] in html
    assert 'href="/documents/' in html


def test_signature_requirement_needs_signature_confirmation(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application

        application_id = (
            db.query(Application).filter_by(borrower_name="owner企业").one().id
        )
        document = add_document(db, application_id, "授权书.pdf", "d")
        document_id = document.id
        template = published_template(db)
        db.commit()
        authorization_item = item_by_code(template, "credit_authorization")
        authorization_item_id = authorization_item.id

    mapped = client.post(
        f"/api/v1/applications/{application_id}/mappings",
        headers={**csrf, "Idempotency-Key": "map-auth"},
        json={"document_id": document_id, "item_id": authorization_item_id},
    )
    assert mapped.status_code == 201
    states = {
        item["code"]: item["state"]
        for item in client.get(
            f"/api/v1/applications/{application_id}/completeness"
        ).json()["items"]
    }
    assert states["credit_authorization"] == "pending_confirmation"

    with client.app.state.database.session() as db:
        output = DocumentOutput(
            document_id=document_id,
            version=1,
            status="success",
            parser_version="test",
            model_version="none",
        )
        db.add(output)
        db.flush()
        output_id = output.id
        db.commit()
    signature = client.post(
        f"/api/v1/document-outputs/{output_id}/reviews",
        headers={**csrf, "Idempotency-Key": "sig-review"},
        json={
            "kind": "signature_presence",
            "status": "present",
            "reason": "原页可见签字",
        },
    )
    assert signature.status_code == 201
    states = {
        item["code"]: item["state"]
        for item in client.get(
            f"/api/v1/applications/{application_id}/completeness"
        ).json()["items"]
    }
    assert states["credit_authorization"] == "satisfied"


def test_individual_demo_application_exercises_individual_template(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application, Resolution, User

        owner = db.query(User).filter_by(username="owner").one()
        application = Application(
            borrower_type="individual",
            borrower_name="个人经营贷申请",
            product="经营贷",
            application_date=date(2026, 8, 7),
            owner_id=owner.id,
        )
        db.add(application)
        db.flush()
        db.add(
            Resolution(
                application_id=application.id,
                field_key="guarantor",
                subject_role="primary_borrower",
                resolution_type="manual",
                typed_value={"type": "text", "value": "张三"},
                reason="人工录入保证人",
                actor_id=owner.id,
            )
        )
        db.commit()
        application_id = application.id
    draft = client.get(f"/api/v1/applications/{application_id}/completeness")
    assert draft.status_code == 200
    body = draft.json()
    assert body["template"]["code"] == "DEMO-INDIVIDUAL-OPERATING"
    states = {item["code"]: item["state"] for item in body["items"]}
    assert states["id_card"] == "missing"
    assert states["business_license"] == "missing"
    # guarantor condition activated by the resolution; collateral stays off
    assert states["guarantor_material"] == "missing"
    assert states["collateral_certificate"] == "not_applicable"
    run = client.post(
        f"/api/v1/applications/{application_id}/completeness-runs",
        headers={**csrf, "Idempotency-Key": "individual-run"},
    )
    assert run.status_code == 201
    assert run.json()["template_snapshot"]["code"] == "DEMO-INDIVIDUAL-OPERATING"
    run_states = {
        item["item_code"]: item["state"] for item in run.json()["result_snapshot"]["items"]
    }
    assert run_states["guarantor_material"] == "missing"
    assert run_states["collateral_certificate"] == "not_applicable"


def test_conditional_items_activate_with_resolutions(client: TestClient) -> None:
    login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application, Resolution

        application_id = (
            db.query(Application).filter_by(borrower_name="owner企业").one().id
        )
        db.add(
            Resolution(
                application_id=application_id,
                field_key="collateral_type",
                subject_role="primary_borrower",
                resolution_type="manual",
                typed_value={"type": "text", "value": "房产"},
                reason="人工录入抵押物",
                actor_id=db.query(User).filter_by(username="owner").one().id,
            )
        )
        db.commit()
    states = {
        item["code"]: item["state"]
        for item in client.get(
            f"/api/v1/applications/{application_id}/completeness"
        ).json()["items"]
    }
    assert states["collateral_certificate"] == "missing"
    assert states["collateral_appraisal"] == "missing"
    assert states["guarantor_material"] == "not_applicable"


# ---------------------------------------------------------------------------
# Production gate and auth boundaries
# ---------------------------------------------------------------------------


def test_production_rejects_demo_templates_for_formal_reports() -> None:
    client = setup(production=True)
    with client:
        csrf = login(client, "admin", "administrator password")
        demo = client.post(
            "/api/v1/admin/completeness-templates",
            headers=csrf,
            json={
                "code": "DEMO-TEST",
                "name": "演示测试模板",
                "product": "经营贷",
                "borrower_type": "corporate",
                "demo_only": True,
                "items": [
                    {"code": "license", "label": "营业执照", "category": "basic_info"}
                ],
            },
        )
        assert demo.status_code == 201
        assert (
            client.post(
                f"/api/v1/admin/completeness-templates/{demo.json()['id']}/publish",
                headers=csrf,
            ).status_code
            == 200
        )
        owner_csrf = login(client, "owner", "approval officer password")
        with client.app.state.database.session() as db:
            from app.models import Application

            application_id = (
                db.query(Application).filter_by(borrower_name="owner企业").one().id
            )
        draft = client.get(f"/api/v1/applications/{application_id}/completeness")
        assert draft.status_code == 200
        assert draft.json()["formal_run_blocked_reason"] is not None
        run = client.post(
            f"/api/v1/applications/{application_id}/completeness-runs",
            headers={**owner_csrf, "Idempotency-Key": "prod-demo"},
        )
        assert run.status_code == 422
        assert "Production mode rejects demo templates" in run.json()["detail"]


def test_production_allows_non_demo_template_formal_report() -> None:
    client = setup(production=True)
    with client:
        csrf = login(client, "admin", "administrator password")
        created = client.post(
            "/api/v1/admin/completeness-templates",
            headers=csrf,
            json={
                "code": "PROD-CORP-OPERATING",
                "name": "生产企业经营贷清单",
                "product": "经营贷",
                "borrower_type": "corporate",
                "demo_only": False,
                "items": [
                    {
                        "code": "license",
                        "label": "营业执照",
                        "category": "basic_info",
                    }
                ],
            },
        )
        assert created.status_code == 201
        assert (
            client.post(
                f"/api/v1/admin/completeness-templates/{created.json()['id']}/publish",
                headers=csrf,
            ).status_code
            == 200
        )
        owner_csrf = login(client, "owner", "approval officer password")
        with client.app.state.database.session() as db:
            from app.models import Application

            application_id = (
                db.query(Application).filter_by(borrower_name="owner企业").one().id
            )
        run = client.post(
            f"/api/v1/applications/{application_id}/completeness-runs",
            headers={**owner_csrf, "Idempotency-Key": "prod-ok"},
        )
        assert run.status_code == 201
        assert run.json()["template_snapshot"]["code"] == "PROD-CORP-OPERATING"


def test_completeness_is_owner_scoped(client: TestClient) -> None:
    login(client, "other", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application

        owner_application_id = (
            db.query(Application).filter_by(borrower_name="owner企业").one().id
        )
        other_application_id = (
            db.query(Application).filter_by(borrower_name="other企业").one().id
        )
        owner_document = add_document(db, owner_application_id, "他人材料.pdf", "e")
        db.commit()
        owner_document_id = owner_document.id
    assert (
        client.get(f"/api/v1/applications/{owner_application_id}/completeness").status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/applications/{other_application_id}/completeness").status_code
        == 200
    )
    # a non-owner cannot confirm another application's material classification
    assert (
        client.post(
            f"/api/v1/applications/{owner_application_id}/documents/{owner_document_id}/classification",
            headers={
                **login(client, "other", "approval officer password"),
                "Idempotency-Key": "classify-other",
            },
            json={"category": "basic_info"},
        ).status_code
        == 404
    )
    # administrator is not the owner either
    admin_csrf = login(client, "admin", "administrator password")
    assert (
        client.get(f"/api/v1/applications/{owner_application_id}/completeness").status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/applications/{other_application_id}/completeness-runs",
            headers={**admin_csrf, "Idempotency-Key": "admin-run"},
        ).status_code
        == 404
    )


def test_resolution_change_marks_current_run_stale(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application

        application_id = (
            db.query(Application).filter_by(borrower_name="owner企业").one().id
        )
    run = client.post(
        f"/api/v1/applications/{application_id}/completeness-runs",
        headers={**csrf, "Idempotency-Key": "run-before-resolution"},
    )
    assert run.status_code == 201
    assert run.json()["stale"] is False

    resolution = client.post(
        f"/api/v1/applications/{application_id}/resolutions",
        headers={**csrf, "Idempotency-Key": "resolution-1"},
        json={
            "resolution_type": "manual",
            "field_key": "collateral_type",
            "value": "房产",
            "reason": "人工录入抵押物",
        },
    )
    assert resolution.status_code == 201
    runs = client.get(
        f"/api/v1/applications/{application_id}/completeness-runs"
    ).json()
    assert runs[0]["stale"] is True
    assert runs[0]["stale_reason"] == "condition_context_change"


def test_latest_seal_review_overrides_older_present(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application, DocumentPage, SealCandidate

        application_id = (
            db.query(Application).filter_by(borrower_name="owner企业").one().id
        )
        document = add_document(db, application_id, "盖章材料.pdf", "g")
        output = DocumentOutput(
            document_id=document.id,
            version=1,
            status="success",
            parser_version="test",
            model_version="none",
        )
        db.add(output)
        db.flush()
        page = DocumentPage(output_id=output.id, number=1, status="success")
        db.add(page)
        db.flush()
        seal = SealCandidate(
            page_id=page.id,
            text="公司章",
            x0=0,
            y0=0,
            x1=10,
            y1=10,
            confidence=0.9,
            model_version="test",
        )
        db.add(seal)
        db.flush()
        document_id = document.id
        seal_id = seal.id
        output_id = output.id
        template = published_template(db)
        db.commit()
        license_item_id = item_by_code(template, "license").id

    mapped = client.post(
        f"/api/v1/applications/{application_id}/mappings",
        headers={**csrf, "Idempotency-Key": "map-seal"},
        json={"document_id": document_id, "item_id": license_item_id},
    )
    assert mapped.status_code == 201
    present = client.post(
        f"/api/v1/document-outputs/{output_id}/reviews",
        headers={**csrf, "Idempotency-Key": "seal-present"},
        json={
            "kind": "seal_presence",
            "status": "present",
            "seal_candidate_id": seal_id,
            "reason": "原页可见公章",
        },
    )
    assert present.status_code == 201
    states = {
        item["code"]: item["state"]
        for item in client.get(
            f"/api/v1/applications/{application_id}/completeness"
        ).json()["items"]
    }
    assert states["license"] == "satisfied"

    # a newer absent review must override the older present one
    absent = client.post(
        f"/api/v1/document-outputs/{output_id}/reviews",
        headers={**csrf, "Idempotency-Key": "seal-absent"},
        json={
            "kind": "seal_presence",
            "status": "absent",
            "seal_candidate_id": seal_id,
            "reason": "复核后确认未见公章",
        },
    )
    assert absent.status_code == 201
    states = {
        item["code"]: item["state"]
        for item in client.get(
            f"/api/v1/applications/{application_id}/completeness"
        ).json()["items"]
    }
    assert states["license"] == "pending_confirmation"


def test_formal_run_without_template_is_rejected(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    with client.app.state.database.session() as db:
        from app.models import Application

        application_id = (
            db.query(Application).filter_by(borrower_name="owner企业").one().id
        )
        # retire the applicable template so nothing is published
        template = published_template(db)
        template.status = TemplateStatus.RETIRED
        db.commit()
    run = client.post(
        f"/api/v1/applications/{application_id}/completeness-runs",
        headers={**csrf, "Idempotency-Key": "no-template"},
    )
    assert run.status_code == 422
    assert "No published applicable template" in run.json()["detail"]
