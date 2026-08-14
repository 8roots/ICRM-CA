"""Append-only audit trail: coverage, read-only API, access control."""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Application, AuditEvent, Base, User
from app.security import hash_password


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


def setup() -> TestClient:
    app = create_app(
        "sqlite+pysqlite:///:memory:",
        cookie_secure=True,
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
    return TestClient(app, base_url="https://testserver")


@pytest.fixture
def client() -> TestClient:
    client = setup()
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


def audit_rows(client: TestClient) -> list[AuditEvent]:
    with client.app.state.database.session() as db:
        return db.query(AuditEvent).order_by(AuditEvent.created_at).all()


def test_auth_events_are_recorded(client: TestClient) -> None:
    # failed login records an event with the attempted username and no actor
    client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": "owner", "password": "wrong password"},
    )
    login(client, "owner", "approval officer password")
    rows = audit_rows(client)
    event_types = [row.event_type for row in rows]
    assert "auth.login_failed" in event_types
    assert "auth.login" in event_types
    failed = next(row for row in rows if row.event_type == "auth.login_failed")
    assert failed.actor_id is None
    assert failed.actor_username == "owner"
    logged = next(row for row in rows if row.event_type == "auth.login")
    assert logged.actor_username == "owner"
    assert logged.resource_type == "user"


def test_upload_download_resolution_events(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    upload = client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={**csrf, "Idempotency-Key": "up-1"},
        files={"file": ("材料.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert upload.status_code == 202
    document_id = upload.json()["document"]["id"]
    assert client.get(f"/api/v1/documents/{document_id}/download").status_code == 200
    resolution = client.post(
        f"/api/v1/applications/{application_id}/resolutions",
        headers={**csrf, "Idempotency-Key": "res-1"},
        json={
            "field_key": "loan_amount",
            "resolution_type": "manual",
            "value": "100000",
            "reason": "补录金额",
        },
    )
    assert resolution.status_code == 201
    rows = audit_rows(client)
    types = [row.event_type for row in rows]
    assert "document.uploaded" in types
    assert "document.downloaded" in types
    assert "resolution.created" in types
    uploaded = next(row for row in rows if row.event_type == "document.uploaded")
    assert uploaded.resource_type == "document"
    assert uploaded.resource_id == document_id
    assert uploaded.correlation_id  # correlation id is attached
    resolution_event = next(row for row in rows if row.event_type == "resolution.created")
    assert resolution_event.details["field_key"] == "loan_amount"


def test_publication_events(client: TestClient) -> None:
    admin = login(client, "admin", "administrator password")
    created = client.post(
        "/api/v1/admin/completeness-templates",
        headers=admin,
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
                }
            ],
        },
    )
    assert created.status_code == 201
    template_id = created.json()["id"]
    publish = client.post(
        f"/api/v1/admin/completeness-templates/{template_id}/publish", headers=admin
    )
    assert publish.status_code == 200
    rows = audit_rows(client)
    assert "template.published" in [row.event_type for row in rows]
    published = next(row for row in rows if row.event_type == "template.published")
    assert published.actor_username == "admin"
    assert published.details["code"] == "CORP-OPERATING-2026"


def test_audit_api_is_read_only(client: TestClient) -> None:
    """The audit surface has no mutation endpoints at all."""
    login(client, "admin", "administrator password")
    paths = client.app.openapi()["paths"]
    audit_paths = {
        path: set(methods) for path, methods in paths.items() if "audit" in path
    }
    assert audit_paths == {
        "/api/v1/audit/events": {"get"},
        "/api/v1/applications/{application_id}/audit-events": {"get"},
    }


def test_audit_access_control(client: TestClient) -> None:
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    resolution = client.post(
        f"/api/v1/applications/{application_id}/resolutions",
        headers={**csrf, "Idempotency-Key": "res-1"},
        json={
            "field_key": "loan_amount",
            "resolution_type": "manual",
            "value": "100000",
            "reason": "补录金额",
        },
    )
    assert resolution.status_code == 201
    # officer cannot list the global audit
    assert client.get("/api/v1/audit/events").status_code == 403
    # officer can see their own application's events
    events = client.get(f"/api/v1/applications/{application_id}/audit-events")
    assert events.status_code == 200
    assert any(row["event_type"] == "resolution.created" for row in events.json())

    login(client, "admin", "administrator password")
    assert client.get("/api/v1/audit/events").status_code == 200
    filtered = client.get("/api/v1/audit/events", params={"event_type": "resolution.created"})
    assert filtered.status_code == 200
    assert {row["event_type"] for row in filtered.json()} == {"resolution.created"}


def test_view_events_are_deduped(client: TestClient) -> None:
    login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    with client.app.state.database.session() as db:
        from app.models import Document, DocumentOutput

        document = Document(
            application_id=application_id,
            filename="材料.pdf",
            extension=".pdf",
            declared_mime="application/pdf",
            size_bytes=1,
            sha256="c" * 64,
            object_key="c",
        )
        db.add(document)
        db.flush()
        db.add(
            DocumentOutput(
                document_id=document.id,
                version=1,
                status="success",
                parser_version="p1",
                model_version="m1",
            )
        )
        db.commit()
        document_id = document.id
    for _ in range(3):
        response = client.get(f"/api/v1/documents/{document_id}/outputs")
        assert response.status_code == 200
    rows = audit_rows(client)
    views = [row for row in rows if row.event_type == "document.viewed"]
    assert len(views) == 1


def test_audit_rows_never_contain_material_text(client: TestClient) -> None:
    """Audit content is metadata-only: no material text or identity info."""
    csrf = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    secret = "客户身份证号 110101199001011234，贷款金额 99999999 元"
    upload = client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={**csrf, "Idempotency-Key": "up-secret"},
        files={"file": ("秘密材料.md", secret.encode(), "text/markdown")},
    )
    assert upload.status_code == 202
    document_id = upload.json()["document"]["id"]
    assert client.get(f"/api/v1/documents/{document_id}/download").status_code == 200
    client.post(
        f"/api/v1/applications/{application_id}/resolutions",
        headers={**csrf, "Idempotency-Key": "res-secret"},
        json={
            "field_key": "loan_amount",
            "resolution_type": "manual",
            "value": "99999999",
            "reason": secret,
        },
    )
    rows = audit_rows(client)
    assert rows
    serialized = "\n".join(
        f"{row.event_type} {row.actor_username} {row.resource_type} "
        f"{row.resource_id} {row.correlation_id} {row.details}"
        for row in rows
    )
    assert "110101199001011234" not in serialized
    assert "99999999 元" not in serialized
    assert secret not in serialized
    assert "材料正文" not in serialized
