"""Admin two-phase hard delete: reason, confirmation token, tombstone."""

import io
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import (
    Application,
    ApplicationTombstone,
    AuditEvent,
    Base,
    CandidateFact,
    CompletenessRun,
    Document,
    DocumentOutput,
    Resolution,
    User,
)
from app.security import hash_password


class FlakyObjects:
    """Memory object store that can fail deletes for selected keys."""

    def __init__(self, fail_keys: set[str] | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_keys = fail_keys or set()
        self.deleted: list[str] = []

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
        self.deleted.append(key)
        if key in self.fail_keys:
            raise RuntimeError("minio unavailable")
        self.objects.pop(key, None)


def setup(fail_keys: set[str] | None = None) -> tuple[TestClient, FlakyObjects]:
    objects = FlakyObjects(fail_keys)
    app = create_app(
        "sqlite+pysqlite:///:memory:",
        cookie_secure=True,
        object_store=objects,
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
        db.add_all([admin, owner])
        db.flush()
        db.add(
            Application(
                borrower_type="corporate",
                borrower_name="owner企业",
                product="经营贷",
                application_date=date(2026, 8, 7),
                owner_id=owner.id,
            )
        )
        db.commit()
    return TestClient(app, base_url="https://testserver"), objects


@pytest.fixture
def client():
    client, _ = setup()
    with client:
        yield client


def login(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": username, "password": password},
    )
    assert response.status_code == 204
    return {"X-CSRF-Token": client.cookies["icrm_csrf"]}


def owner_application_id(client: TestClient) -> str:
    with client.app.state.database.session() as db:
        return db.query(Application).filter_by(borrower_name="owner企业").one().id


def seed_sensitive_rows(client: TestClient) -> tuple[str, str, str]:
    """Create a document + output + candidate + resolution + completeness run."""
    application_id = owner_application_id(client)
    with client.app.state.database.session() as db:
        owner = db.query(User).filter_by(username="owner").one()
        document = Document(
            application_id=application_id,
            filename="材料.pdf",
            extension=".pdf",
            declared_mime="application/pdf",
            size_bytes=1,
            sha256="d" * 64,
            object_key="app-key/material.pdf",
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
        db.add(
            CandidateFact(
                document_id=document.id,
                output_id=output.id,
                field_key="loan_amount",
                raw_text="100000",
                typed_value={"type": "money", "value": "100000"},
                confidence=0.9,
                extractor="local_rule",
                extractor_version="v1",
                model_version="none",
                source_refs=[],
            )
        )
        resolution = Resolution(
            application_id=application_id,
            field_key="loan_amount",
            resolution_type="manual",
            typed_value={"type": "money", "value": "100000"},
            reason="补录",
            actor_id=owner.id,
        )
        db.add(resolution)
        db.flush()
        from app.models import CompletenessTemplate

        template = db.query(CompletenessTemplate).first()
        db.add(
            CompletenessRun(
                application_id=application_id,
                template_id=template.id,
                template_snapshot={},
                input_snapshot={},
                result_snapshot={},
                content_hash="h",
                actor_id=owner.id,
            )
        )
        db.commit()
        return application_id, document.id, document.object_key


def test_hard_delete_requires_reason_then_token(client: TestClient) -> None:
    application_id = owner_application_id(client)
    admin = login(client, "admin", "administrator password")
    # phase two without a request is rejected
    direct = client.post(
        f"/api/v1/applications/{application_id}/hard-delete",
        headers={**admin, "Idempotency-Key": "del-1"},
        json={"confirmation_token": "whatever"},
    )
    assert direct.status_code == 409

    # phase one requires a reason
    no_reason = client.post(
        f"/api/v1/applications/{application_id}/hard-delete-requests",
        headers=admin,
        json={"reason": ""},
    )
    assert no_reason.status_code == 422

    requested = client.post(
        f"/api/v1/applications/{application_id}/hard-delete-requests",
        headers=admin,
        json={"reason": "客户资料已归档至机构档案系统，系统内数据整体删除"},
    )
    assert requested.status_code == 201
    token = requested.json()["confirmation_token"]

    # wrong token rejected
    wrong = client.post(
        f"/api/v1/applications/{application_id}/hard-delete",
        headers={**admin, "Idempotency-Key": "del-2"},
        json={"confirmation_token": "wrong-token"},
    )
    assert wrong.status_code == 403

    confirmed = client.post(
        f"/api/v1/applications/{application_id}/hard-delete",
        headers={**admin, "Idempotency-Key": "del-2"},
        json={"confirmation_token": token},
    )
    assert confirmed.status_code == 204


def test_hard_delete_removes_sensitive_rows_and_leaves_tombstone(
    client: TestClient,
) -> None:
    application_id, document_id, object_key = seed_sensitive_rows(client)
    admin = login(client, "admin", "administrator password")
    requested = client.post(
        f"/api/v1/applications/{application_id}/hard-delete-requests",
        headers=admin,
        json={"reason": "机构要求整体删除"},
    )
    token = requested.json()["confirmation_token"]
    confirmed = client.post(
        f"/api/v1/applications/{application_id}/hard-delete",
        headers={**admin, "Idempotency-Key": "del-1"},
        json={"confirmation_token": token},
    )
    assert confirmed.status_code == 204

    with client.app.state.database.session() as db:
        assert db.get(Application, application_id) is None
        assert db.get(Document, document_id) is None
        assert db.query(DocumentOutput).count() == 0
        assert db.query(CandidateFact).count() == 0
        assert db.query(Resolution).count() == 0
        assert db.query(CompletenessRun).count() == 0
        tombstone = db.get(ApplicationTombstone, application_id)
        assert tombstone is not None
        assert tombstone.reason == "机构要求整体删除"
        assert tombstone.remaining_object_keys == []
        # the append-only audit trail survives the delete
        audit = [
            row.event_type for row in db.query(AuditEvent).order_by(AuditEvent.created_at).all()
        ]
        assert "application.hard_delete_requested" in audit
        assert "application.hard_deleted" in audit


def test_hard_delete_partial_failure_is_visible_and_recoverable() -> None:
    client, objects = setup(fail_keys={"app-key/material.pdf"})
    application_id = owner_application_id(client)
    objects.put("app-key/material.pdf", io.BytesIO(b"%PDF-1.4 fake"), -1)
    with client.app.state.database.session() as db:
        document = Document(
            application_id=application_id,
            filename="材料.pdf",
            extension=".pdf",
            declared_mime="application/pdf",
            size_bytes=1,
            sha256="e" * 64,
            object_key="app-key/material.pdf",
        )
        db.add(document)
        db.commit()
    admin = login(client, "admin", "administrator password")
    requested = client.post(
        f"/api/v1/applications/{application_id}/hard-delete-requests",
        headers=admin,
        json={"reason": "删除失败演练"},
    )
    token = requested.json()["confirmation_token"]
    with client:
        confirmed = client.post(
            f"/api/v1/applications/{application_id}/hard-delete",
            headers={**admin, "Idempotency-Key": "del-1"},
            json={"confirmation_token": token},
        )
        assert confirmed.status_code == 204
    with client.app.state.database.session() as db:
        tombstone = db.get(ApplicationTombstone, application_id)
        assert tombstone is not None
        assert tombstone.remaining_object_keys == ["app-key/material.pdf"]
        # the originals still exist in object storage and can be purged later
        assert "app-key/material.pdf" in objects.objects


def test_hard_delete_token_expires(client: TestClient) -> None:
    application_id = owner_application_id(client)
    admin = login(client, "admin", "administrator password")
    requested = client.post(
        f"/api/v1/applications/{application_id}/hard-delete-requests",
        headers=admin,
        json={"reason": "删除测试"},
    )
    token = requested.json()["confirmation_token"]
    with client.app.state.database.session() as db:
        from app.models import HardDeleteRequest

        request_row = (
            db.query(HardDeleteRequest)
            .filter_by(application_id=application_id)
            .one()
        )
        request_row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    expired = client.post(
        f"/api/v1/applications/{application_id}/hard-delete",
        headers={**admin, "Idempotency-Key": "del-1"},
        json={"confirmation_token": token},
    )
    assert expired.status_code == 409
    assert "expired" in expired.json()["detail"]


def test_hard_delete_requires_admin(client: TestClient) -> None:
    application_id = owner_application_id(client)
    owner = login(client, "owner", "approval officer password")
    response = client.post(
        f"/api/v1/applications/{application_id}/hard-delete-requests",
        headers=owner,
        json={"reason": "普通用户尝试"},
    )
    assert response.status_code == 403
