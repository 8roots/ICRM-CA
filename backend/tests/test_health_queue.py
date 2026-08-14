"""Health readiness/degraded states, worker heartbeat, admin queue view."""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import (
    Application,
    Base,
    Document,
    DocumentJob,
    JobStatus,
    User,
    WorkerHeartbeat,
)
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
        return self.bucket


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


def test_liveness_always_ok(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200


def test_readiness_ready_with_no_pending_jobs(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["object_store"] == "ok"
    assert body["correlation_id"]


def test_readiness_degraded_with_pending_jobs_and_stale_worker(client: TestClient) -> None:
    with client.app.state.database.session() as db:
        from app.models import Application as App

        application_id = db.query(App).first().id
        document = Document(
            application_id=application_id,
            filename="材料.pdf",
            extension=".pdf",
            declared_mime="application/pdf",
            size_bytes=1,
            sha256="f" * 64,
            object_key="f",
        )
        db.add(document)
        db.flush()
        db.add(DocumentJob(document=document, status=JobStatus.WAITING))
        db.commit()
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["worker"] == "stale"
    assert body["pending_jobs"] == 1


def test_readiness_degraded_when_object_store_down(client: TestClient) -> None:
    client.app.state.object_store.bucket = False
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["object_store"] == "error"


def test_worker_heartbeat_flow(client: TestClient) -> None:
    with client.app.state.database.session() as db:
        db.add(WorkerHeartbeat(worker_id="worker-a", hostname="worker-a"))
        db.commit()
    login(client, "admin", "administrator password")
    queue = client.get("/api/v1/admin/queue")
    assert queue.status_code == 200
    body = queue.json()
    assert body["workers"][0]["worker_id"] == "worker-a"
    assert body["workers"][0]["healthy"] is True


def test_queue_view_reports_failures_and_waiting(client: TestClient) -> None:
    with client.app.state.database.session() as db:
        application_id = db.query(Application).first().id
        document = Document(
            application_id=application_id,
            filename="坏材料.pdf",
            extension=".pdf",
            declared_mime="application/pdf",
            size_bytes=1,
            sha256="g" * 64,
            object_key="g",
        )
        db.add(document)
        db.flush()
        db.add(
            DocumentJob(
                document=document,
                status=JobStatus.FAILED,
                error_code="unsupported_format",
                retry_reason="重新上传",
            )
        )
        db.add(
            DocumentJob(
                document=document,
                status=JobStatus.WAITING,
            )
        )
        db.commit()
    login(client, "admin", "administrator password")
    queue = client.get("/api/v1/admin/queue")
    body = queue.json()
    assert body["failed"] == 1
    assert body["waiting"] == 1
    assert body["by_status"]["failed"] == 1
    assert body["oldest_waiting"] is not None
    failures = body["recent_failures"]
    assert failures[0]["error_code"] == "unsupported_format"
    assert failures[0]["retry_reason"] == "重新上传"
    assert failures[0]["filename"] == "坏材料.pdf"

    # officers cannot see the queue
    login(client, "owner", "approval officer password")
    assert client.get("/api/v1/admin/queue").status_code == 403
