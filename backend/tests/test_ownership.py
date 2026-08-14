"""Ownership: admin metadata-only reassignment; prior owner loses access."""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Application, Base, User
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
        other = User(
            username="other",
            password_hash=hash_password("approval officer password"),
            role="approval_officer",
            enabled=True,
        )
        db.add_all([admin, owner, other])
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


def test_reassignment_revokes_previous_owner_immediately(client: TestClient) -> None:
    application_id = owner_application_id(client)
    with client.app.state.database.session() as db:
        other_id = db.query(User).filter_by(username="other").one().id
        version = db.get(Application, application_id).version

    admin = login(client, "admin", "administrator password")
    reassigned = client.post(
        f"/api/v1/applications/{application_id}/reassign",
        headers={**admin, "Idempotency-Key": "reassign-1"},
        json={"version": version, "owner_id": other_id},
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["state"] == "draft"

    # previous owner loses access immediately: metadata list and direct URL
    login(client, "owner", "approval officer password")
    assert client.get("/api/v1/applications").json() == []
    assert client.get(f"/api/v1/applications/{application_id}").status_code == 404
    assert client.get(f"/api/v1/applications/{application_id}/documents").status_code == 404

    # new owner sees the application and can upload; the document is visible
    other = login(client, "other", "approval officer password")
    assert client.get(f"/api/v1/applications/{application_id}").status_code == 200
    upload = client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={**other, "Idempotency-Key": "up-1"},
        files={"file": ("材料.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert upload.status_code == 202
    document_id = upload.json()["document"]["id"]
    assert client.get(f"/api/v1/documents/{document_id}/download").status_code == 200

    # the former owner cannot download the new document either
    login(client, "owner", "approval officer password")
    assert client.get(f"/api/v1/documents/{document_id}/download").status_code == 404


def test_admin_metadata_only_no_material_access(client: TestClient) -> None:
    application_id = owner_application_id(client)
    admin = login(client, "admin", "administrator password")
    upload = client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={**admin, "Idempotency-Key": "up-1"},
        files={"file": ("材料.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    # admin is not the owner, so the application is not found for material access
    assert upload.status_code == 404
    assert client.get(f"/api/v1/applications/{application_id}/candidates").status_code == 404

    # the admin metadata view lists the application without material content
    response = client.get("/api/v1/admin/applications")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["borrower_name"] == "owner企业"
    assert rows[0]["owner_username"] == "owner"


def test_non_admin_cannot_reassign(client: TestClient) -> None:
    owner = login(client, "owner", "approval officer password")
    application_id = owner_application_id(client)
    with client.app.state.database.session() as db:
        other_id = db.query(User).filter_by(username="other").one().id
    response = client.post(
        f"/api/v1/applications/{application_id}/reassign",
        headers={**owner, "Idempotency-Key": "reassign-1"},
        json={"version": 1, "owner_id": other_id},
    )
    assert response.status_code == 403


def test_reassign_rejects_unknown_or_non_officer_owner(client: TestClient) -> None:
    admin = login(client, "admin", "administrator password")
    application_id = owner_application_id(client)
    with client.app.state.database.session() as db:
        admin_id = db.query(User).filter_by(username="admin").one().id
    bad = client.post(
        f"/api/v1/applications/{application_id}/reassign",
        headers={**admin, "Idempotency-Key": "reassign-1"},
        json={"version": 1, "owner_id": "no-such-user"},
    )
    assert bad.status_code == 422
    as_admin = client.post(
        f"/api/v1/applications/{application_id}/reassign",
        headers={**admin, "Idempotency-Key": "reassign-2"},
        json={"version": 1, "owner_id": admin_id},
    )
    assert as_admin.status_code == 422


def test_rbac_matrix(client: TestClient) -> None:
    """Endpoint x role matrix for the key application/material surfaces."""
    application_id = owner_application_id(client)
    with client.app.state.database.session() as db:
        other_id = db.query(User).filter_by(username="other").one().id
    officer = login(client, "owner", "approval officer password")
    admin = login(client, "admin", "administrator password")

    # admin: metadata endpoints allowed; officer-scoped endpoints forbidden
    assert client.get("/api/v1/admin/applications").status_code == 200
    assert client.get("/api/v1/admin/queue").status_code == 200
    assert client.get("/api/v1/admin/users").status_code == 200
    # officer-role-gated endpoints return 403 to an admin
    assert client.get(f"/api/v1/applications/{application_id}").status_code == 403
    assert client.get("/api/v1/applications").status_code == 403
    # owner-scoped material surfaces are not found for a non-owner admin
    assert client.get(f"/api/v1/applications/{application_id}/documents").status_code == 404
    assert client.get(f"/api/v1/applications/{application_id}/candidates").status_code == 404
    upload = client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={**admin, "Idempotency-Key": "up-a"},
        files={"file": ("m.pdf", b"x", "application/pdf")},
    )
    assert upload.status_code == 404

    # officer: own application yes; admin-only surfaces forbidden
    login(client, "owner", "approval officer password")
    assert client.get(f"/api/v1/applications/{application_id}").status_code == 200
    assert client.get("/api/v1/admin/users").status_code == 403
    assert client.get("/api/v1/admin/applications").status_code == 403
    assert client.get("/api/v1/admin/queue").status_code == 403
    assert client.get("/api/v1/audit/events").status_code == 403
    officer = login(client, "owner", "approval officer password")
    reassign = client.post(
        f"/api/v1/applications/{application_id}/reassign",
        headers={**officer, "Idempotency-Key": "reassign-x"},
        json={"version": 1, "owner_id": other_id},
    )
    assert reassign.status_code == 403

    # anonymous: everything behind auth is 401/403
    client.cookies.clear()
    assert client.get("/api/v1/applications").status_code == 401
    assert client.get("/api/v1/admin/users").status_code == 401
