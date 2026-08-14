"""Cloud readiness gate: missing credentials or confirmation disables DeepSeek.

Local candidate extraction continues regardless; only the cloud path is
blocked, and the gate state is visible through the meta endpoint and the
health readiness check.
"""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.cloud_extraction import DeepSeekClient
from app.config import Settings
from app.main import create_app
from app.models import Application, Base, User
from app.security import hash_password


class MemoryObjects:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.bucket = True

    def put(self, key: str, stream, length: int) -> None:
        self.objects[key] = b""

    def open(self, key: str):
        return io.BytesIO(self.objects[key])

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def bucket_exists(self) -> bool:
        return True


def setup() -> TestClient:
    app = create_app(
        "sqlite+pysqlite:///:memory:",
        cookie_secure=True,
        object_store=MemoryObjects(),
    )
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session() as db:
        owner = User(
            username="owner",
            password_hash=hash_password("approval officer password"),
            role="approval_officer",
            enabled=True,
        )
        db.add(owner)
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


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": username, "password": "approval officer password"},
    )
    assert response.status_code == 204
    return {"X-CSRF-Token": client.cookies["icrm_csrf"]}


def test_deepseek_client_requires_confirmation() -> None:
    client = DeepSeekClient(
        "https://api.example.com",
        "key",
        "model",
        cloud_confirmed=False,
    )
    assert client.enabled is False
    confirmed = DeepSeekClient(
        "https://api.example.com",
        "key",
        "model",
        cloud_confirmed=True,
    )
    assert confirmed.enabled is True


def test_settings_cloud_gate() -> None:
    base = Settings(
        deepseek_base_url="https://api.example.com",
        deepseek_api_key="key",
        deepseek_model="model",
    )
    assert base.cloud_configured is True
    assert base.cloud_ready is False  # no training/retention confirmation
    assert "missing_training_confirmation" in base.cloud_gate_blockers
    assert "missing_retention_period" in base.cloud_gate_blockers

    confirmed = Settings(
        deepseek_base_url="https://api.example.com",
        deepseek_api_key="key",
        deepseek_model="model",
        cloud_training_confirmation=True,
        cloud_retention_days=30,
    )
    assert confirmed.cloud_ready is True


def test_meta_cloud_gate_endpoint_reports_blockers(client: TestClient) -> None:
    login(client, "owner")
    response = client.get("/api/v1/meta/cloud-gate")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["blockers"]


def test_health_reports_cloud_gate(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["cloud_gate"] == "blocked"


def test_gate_blocked_cloud_keeps_local_candidates() -> None:
    """A gate-blocked client disables the cloud path without losing local
    candidates or writing any restricted cloud audit row."""
    import io

    from app.cloud_extraction import DeepSeekClient
    from app.extraction_service import run_candidate_extraction
    from app.main import create_app
    from app.models import (
        CandidateFact,
        CloudExtractionCall,
        Document,
        JobStatus,
        User,
    )
    from app.parsed_outputs import store_parsed_output
    from app.structured import parse_structured

    created = create_app("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(created.state.database.engine)
    text = "# 贷款申请材料\n贷款金额：500万元。\n"
    parsed = parse_structured("material.md", io.BytesIO(text.encode()))
    with created.state.database.session() as db:
        user = User(username="owner", password_hash="hash", role="approval_officer")
        db.add(user)
        db.flush()
        application = Application(
            borrower_type="corporate",
            borrower_name="示例企业",
            product="经营贷",
            application_date=date(2026, 8, 7),
            owner_id=user.id,
        )
        db.add(application)
        db.flush()
        document = Document(
            application_id=application.id,
            filename="material.md",
            extension=".md",
            declared_mime="text/markdown",
            size_bytes=1,
            sha256="c" * 64,
            object_key="c",
        )
        db.add(document)
        db.flush()
        output = store_parsed_output(db, document.id, parsed)
        db.commit()
        blocked_client = DeepSeekClient(
            "https://api.example.com", "key", "model", cloud_confirmed=False
        )
        result = run_candidate_extraction(db, document, output, blocked_client)
        assert result.step_status == JobStatus.SUCCESS
        assert db.query(CandidateFact).count() > 0  # local candidates stored
        assert db.query(CloudExtractionCall).count() == 0  # no cloud audit row
        db.rollback()
