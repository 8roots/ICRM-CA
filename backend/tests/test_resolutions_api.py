"""Candidate immutability, resolution creation, and owner-only auth."""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.extraction_service import run_candidate_extraction
from app.main import create_app
from app.models import (
    Application,
    Base,
    CandidateFact,
    Document,
    DocumentOutput,
    User,
)
from app.parsed_outputs import store_parsed_output
from app.security import hash_password
from app.structured import parse_structured

MATERIAL = """# 贷款申请材料

企业名称：示例企业有限公司
统一社会信用代码：91330100MA27XW1234
贷款金额：800万元
贷款期限：24个月
年利率：3.85%
"""


@pytest.fixture
def client() -> TestClient:
    app = create_app("sqlite+pysqlite:///:memory:", cookie_secure=True)
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session() as db:
        owner = User(
            username="owner",
            password_hash=hash_password("approval officer password"),
            role="approval_officer",
        )
        other = User(
            username="other",
            password_hash=hash_password("approval officer password"),
            role="approval_officer",
        )
        admin = User(
            username="admin",
            password_hash=hash_password("approval officer password"),
            role="administrator",
        )
        db.add_all([owner, other, admin])
        db.flush()
        application = Application(
            borrower_type="corporate",
            borrower_name="示例企业有限公司",
            product="经营贷",
            application_date=date(2026, 8, 7),
            owner_id=owner.id,
        )
        db.add(application)
        db.flush()
        parsed = parse_structured("material.md", io.BytesIO(MATERIAL.encode()))
        document = Document(
            application_id=application.id,
            filename="material.md",
            extension=".md",
            declared_mime="text/markdown",
            size_bytes=len(MATERIAL),
            sha256="d" * 64,
            object_key="material",
        )
        db.add(document)
        db.flush()
        output = store_parsed_output(db, document.id, parsed)
        run_candidate_extraction(db, document, output)
        db.commit()
        application_id = application.id
        candidate_id = db.query(CandidateFact).filter_by(field_key="loan_amount").one().id
    created = TestClient(app, base_url="https://testserver")
    created.app.state.test_application_id = application_id
    created.app.state.test_candidate_id = candidate_id
    return created


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": username, "password": "approval officer password"},
    )
    assert response.status_code == 204
    return {"X-CSRF-Token": client.cookies["icrm_csrf"]}


def test_candidates_are_sorted_by_confidence_descending(client) -> None:
    with client:
        csrf = login(client, "owner")
        candidates = client.get(
            f"/api/v1/applications/{client.app.state.test_application_id}/candidates", headers=csrf
        ).json()
        confidences = [item["confidence"] for item in candidates]
        assert confidences == sorted(confidences, reverse=True)
        uscc = next(item for item in candidates if item["field_key"] == "uscc")
        assert uscc["field_label"] == "统一社会信用代码"
        assert uscc["subject_label"] == "主借款人"
        assert uscc["extractor"] == "local_rule"
        assert uscc["source_refs"][0]["output_version"] == 1
        assert uscc["filename"] == "material.md"
        assert uscc["typed_value"]["value"] == "91330100MA27XW1234"


def test_other_user_and_admin_without_assignment_cannot_view(client) -> None:
    with client:
        login(client, "owner")
        app_id = client.app.state.test_application_id
        client.cookies.clear()
        other_csrf = login(client, "other")
        for path in ("candidates", "resolutions", "cloud-calls"):
            response = client.get(f"/api/v1/applications/{app_id}/{path}", headers=other_csrf)
            assert response.status_code == 404
        client.cookies.clear()
        admin_csrf = login(client, "admin")
        response = client.get(f"/api/v1/applications/{app_id}/candidates", headers=admin_csrf)
        assert response.status_code == 404


def test_candidates_and_resolutions_have_no_update_or_delete_endpoints(client) -> None:
    with client:
        csrf = login(client, "owner")
        app_id = client.app.state.test_application_id
        candidate_id = client.app.state.test_candidate_id
        assert client.put(
            f"/api/v1/applications/{app_id}/candidates/{candidate_id}",
            headers=csrf,
            json={"value": "篡改"},
        ).status_code in (404, 405)
        assert client.delete(
            f"/api/v1/applications/{app_id}/candidates/{candidate_id}", headers=csrf
        ).status_code in (404, 405)
        resolution = client.post(
            f"/api/v1/applications/{app_id}/resolutions",
            headers={**csrf, "Idempotency-Key": "sel-1"},
            json={
                "resolution_type": "selected",
                "field_key": "loan_amount",
                "candidate_id": candidate_id,
            },
        )
        assert resolution.status_code == 201
        resolution_id = resolution.json()["id"]
        assert client.delete(
            f"/api/v1/applications/{app_id}/resolutions/{resolution_id}", headers=csrf
        ).status_code in (404, 405)


def test_selected_resolution_copies_the_immutable_candidate(client) -> None:
    with client:
        csrf = login(client, "owner")
        app_id = client.app.state.test_application_id
        candidate_id = client.app.state.test_candidate_id
        created = client.post(
            f"/api/v1/applications/{app_id}/resolutions",
            headers={**csrf, "Idempotency-Key": "sel-2"},
            json={
                "resolution_type": "selected",
                "field_key": "loan_amount",
                "candidate_id": candidate_id,
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["resolution_type"] == "selected"
        assert body["candidate_id"] == candidate_id
        assert body["typed_value"]["value"] == "8000000"
        assert body["no_material_source"] is False
        # Replay with the same idempotency key returns the same resolution.
        replay = client.post(
            f"/api/v1/applications/{app_id}/resolutions",
            headers={**csrf, "Idempotency-Key": "sel-2"},
            json={
                "resolution_type": "selected",
                "field_key": "loan_amount",
                "candidate_id": candidate_id,
            },
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == body["id"]
        history = client.get(f"/api/v1/applications/{app_id}/resolutions", headers=csrf).json()
        assert len(history) == 1


def test_corrected_resolution_keeps_unit_context_and_does_not_touch_candidate(client) -> None:
    with client:
        csrf = login(client, "owner")
        app_id = client.app.state.test_application_id
        candidate_id = client.app.state.test_candidate_id
        corrected = client.post(
            f"/api/v1/applications/{app_id}/resolutions",
            headers={**csrf, "Idempotency-Key": "cor-1"},
            json={
                "resolution_type": "corrected",
                "field_key": "loan_amount",
                "candidate_id": candidate_id,
                "value": "900万元",
            },
        )
        assert corrected.status_code == 201
        body = corrected.json()
        assert body["typed_value"]["value"] == "9000000"
        assert body["typed_value"]["unit"] == "10000"
        assert body["typed_value"]["currency"] == "CNY"
        assert body["no_material_source"] is False
        # The candidate itself is unchanged.
        candidates = client.get(f"/api/v1/applications/{app_id}/candidates", headers=csrf).json()
        original = next(item for item in candidates if item["id"] == candidate_id)
        assert original["typed_value"]["value"] == "8000000"


def test_corrected_loan_term_keeps_month_unit(client) -> None:
    with client:
        csrf = login(client, "owner")
        app_id = client.app.state.test_application_id
        with client.app.state.database.session() as db:
            candidate = db.query(CandidateFact).filter_by(field_key="loan_term").one()
        corrected = client.post(
            f"/api/v1/applications/{app_id}/resolutions",
            headers={**csrf, "Idempotency-Key": "cor-term"},
            json={
                "resolution_type": "corrected",
                "field_key": "loan_term",
                "candidate_id": candidate.id,
                "value": "2年",
            },
        )
        assert corrected.status_code == 201
        typed = corrected.json()["typed_value"]
        assert typed["value"] == "24"
        assert typed["unit"] == "月"


def test_manual_resolution_requires_reason_and_is_labeled_no_material_source(client) -> None:
    with client:
        csrf = login(client, "owner")
        app_id = client.app.state.test_application_id
        missing_reason = client.post(
            f"/api/v1/applications/{app_id}/resolutions",
            headers={**csrf, "Idempotency-Key": "man-1"},
            json={
                "resolution_type": "manual",
                "field_key": "loan_purpose",
                "value": "购货",
            },
        )
        assert missing_reason.status_code == 422
        manual = client.post(
            f"/api/v1/applications/{app_id}/resolutions",
            headers={**csrf, "Idempotency-Key": "man-2"},
            json={
                "resolution_type": "manual",
                "field_key": "loan_purpose",
                "value": "补充流动资金",
                "reason": "电话与客户确认",
            },
        )
        assert manual.status_code == 201
        body = manual.json()
        assert body["resolution_type"] == "manual"
        assert body["candidate_id"] is None
        assert body["no_material_source"] is True
        assert body["reason"] == "电话与客户确认"
        assert body["typed_value"]["value"] == "补充流动资金"


def test_candidate_from_another_application_is_rejected(client) -> None:
    with client:
        csrf = login(client, "owner")
        app_id = client.app.state.test_application_id
        with client.app.state.database.session() as db:
            other_application = Application(
                borrower_type="individual",
                borrower_name="他人",
                product="消费贷",
                application_date=date(2026, 8, 7),
                owner_id=db.query(User).filter_by(username="owner").one().id,
            )
            db.add(other_application)
            db.flush()
            document = Document(
                application_id=other_application.id,
                filename="other.md",
                extension=".md",
                declared_mime="text/markdown",
                size_bytes=1,
                sha256="e" * 64,
                object_key="other",
            )
            db.add(document)
            db.flush()
            output = DocumentOutput(
                document_id=document.id,
                version=1,
                status="success",
                parser_version="p1",
                model_version="m1",
            )
            db.add(output)
            db.flush()
            foreign_candidate = CandidateFact(
                document_id=document.id,
                output_id=output.id,
                field_key="loan_amount",
                raw_text="100万元",
                typed_value={"type": "amount", "value": "1000000"},
                confidence=0.9,
                extractor="local_rule",
                extractor_version="v1",
                model_version="none",
                source_refs=[],
            )
            db.add(foreign_candidate)
            db.commit()
            foreign_id = foreign_candidate.id
        rejected = client.post(
            f"/api/v1/applications/{app_id}/resolutions",
            headers={**csrf, "Idempotency-Key": "foreign-1"},
            json={
                "resolution_type": "selected",
                "field_key": "loan_amount",
                "candidate_id": foreign_id,
            },
        )
        assert rejected.status_code == 422
