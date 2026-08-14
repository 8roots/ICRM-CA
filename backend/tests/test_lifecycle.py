"""Lifecycle state machine: transitions, completion gates, reopen, archive.

The full path (draft → pending_review → review_complete → archived) needs a
formal redline run and a formal completeness run; the completion endpoint
rejects running jobs and stale reports.
"""

import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import (
    Application,
    Base,
    CompletenessRun,
    RedlineRun,
    RunStatus,
    User,
)
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
        for username, borrower_type in (("owner", "corporate"), ("other", "corporate")):
            db.add(
                Application(
                    borrower_type=borrower_type,
                    borrower_name=f"{username}企业",
                    product="经营贷",
                    application_date=date(2026, 8, 7),
                    proposed_signing_date=date(2026, 8, 20),
                    owner_id=owner.id if username == "owner" else other.id,
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


def owner_application_id(client: TestClient, name: str = "owner企业") -> str:
    with client.app.state.database.session() as db:
        return db.query(Application).filter_by(borrower_name=name).one().id


def confirm_context(client: TestClient, csrf: dict[str, str], application_id: str) -> None:
    response = client.post(
        f"/api/v1/applications/{application_id}/rule-context",
        headers={**csrf, "Idempotency-Key": "ctx-1"},
        json={"context": "全国"},
    )
    assert response.status_code == 201


def confirm_all_critical_inputs(
    client: TestClient, csrf: dict[str, str], application_id: str
) -> None:
    inputs = {
        "loan_amount": ("100000", "贷款金额 100000 元"),
        "loan_term": ("12", "期限 12 个月"),
        "interest_rate": ("12%", "年利率 12%"),
        "repayment_method": ("等额本息", "还款方式等额本息"),
        "loan_fees": ("0", "无必要费用"),
        "overdue_interest_rate": ("18%", "罚息利率 18%"),
    }
    for index, (field_key, (value, reason)) in enumerate(inputs.items()):
        response = client.post(
            f"/api/v1/applications/{application_id}/resolutions",
            headers={**csrf, "Idempotency-Key": f"res-{index}"},
            json={
                "field_key": field_key,
                "resolution_type": "manual",
                "value": value,
                "reason": reason,
            },
        )
        assert response.status_code == 201


def formal_reports(client: TestClient, csrf: dict[str, str], application_id: str) -> None:
    confirm_context(client, csrf, application_id)
    confirm_all_critical_inputs(client, csrf, application_id)
    redline = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "redline-1"},
    )
    assert redline.status_code == 201
    completeness = client.post(
        f"/api/v1/applications/{application_id}/completeness-runs",
        headers={**csrf, "Idempotency-Key": "completeness-1"},
    )
    assert completeness.status_code == 201


def lifecycle(client: TestClient, application_id: str) -> dict:
    response = client.get(f"/api/v1/applications/{application_id}/lifecycle")
    assert response.status_code == 200
    return response.json()


def application_version(client: TestClient, application_id: str) -> int:
    with client.app.state.database.session() as db:
        return db.get(Application, application_id).version


def test_draft_lifecycle_and_completion_blocked_without_reports(
    client: TestClient,
) -> None:
    csrf = login(client, "owner")
    application_id = owner_application_id(client)
    body = lifecycle(client, application_id)
    assert body["state"] == "draft"
    assert body["editable"] is True
    assert body["can_complete"] is False
    assert body["can_reopen"] is False

    with client.app.state.database.session() as db:
        application = db.get(Application, application_id)
        application.lifecycle_state = "pending_review"
        db.commit()
    complete = client.post(
        f"/api/v1/applications/{application_id}/complete",
        headers={**csrf, "Idempotency-Key": "complete-1"},
        json={"version": 1},
    )
    assert complete.status_code == 409
    assert "missing_redline_report" in complete.json()["detail"]
    assert "missing_completeness_report" in complete.json()["detail"]


def test_completion_requires_pending_review(client: TestClient) -> None:
    csrf = login(client, "owner")
    application_id = owner_application_id(client)
    complete = client.post(
        f"/api/v1/applications/{application_id}/complete",
        headers={**csrf, "Idempotency-Key": "complete-1"},
        json={"version": 1},
    )
    assert complete.status_code == 409
    assert "Only pending_review" in complete.json()["detail"]


def test_completion_blocked_by_stale_reports(client: TestClient) -> None:
    csrf = login(client, "owner")
    application_id = owner_application_id(client)
    formal_reports(client, csrf, application_id)
    with client.app.state.database.session() as db:
        db.query(RedlineRun).update({RedlineRun.status: RunStatus.STALE})
        db.query(CompletenessRun).update({CompletenessRun.status: RunStatus.STALE})
        application = db.get(Application, application_id)
        application.lifecycle_state = "pending_review"
        db.commit()
    complete = client.post(
        f"/api/v1/applications/{application_id}/complete",
        headers={**csrf, "Idempotency-Key": "complete-1"},
        json={"version": 1},
    )
    assert complete.status_code == 409
    assert "stale_redline_report" in complete.json()["detail"]
    assert "stale_completeness_report" in complete.json()["detail"]


def test_completion_blocked_by_running_jobs(client: TestClient) -> None:
    csrf = login(client, "owner")
    application_id = owner_application_id(client)
    formal_reports(client, csrf, application_id)
    with client.app.state.database.session() as db:
        from app.models import Document, DocumentJob, JobStatus, ProcessingStep

        document = Document(
            application_id=application_id,
            filename="材料.pdf",
            extension=".pdf",
            declared_mime="application/pdf",
            size_bytes=1,
            sha256="a" * 64,
            object_key="a",
        )
        db.add(document)
        db.flush()
        job = DocumentJob(document=document, status=JobStatus.RUNNING, claimed_by="w1")
        job.steps.append(ProcessingStep(name="validation", status=JobStatus.RUNNING))
        db.add(job)
        application = db.get(Application, application_id)
        application.lifecycle_state = "pending_review"
        db.commit()
    complete = client.post(
        f"/api/v1/applications/{application_id}/complete",
        headers={**csrf, "Idempotency-Key": "complete-1"},
        json={"version": 1},
    )
    assert complete.status_code == 409
    assert "running_jobs" in complete.json()["detail"]


def test_complete_reopen_archive_cycle(client: TestClient) -> None:
    csrf = login(client, "owner")
    application_id = owner_application_id(client)
    formal_reports(client, csrf, application_id)
    with client.app.state.database.session() as db:
        application = db.get(Application, application_id)
        application.lifecycle_state = "pending_review"
        db.commit()
        version = application.version

    completed = client.post(
        f"/api/v1/applications/{application_id}/complete",
        headers={**csrf, "Idempotency-Key": "complete-1"},
        json={"version": version},
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "review_complete"
    assert completed.json()["editable"] is False
    assert completed.json()["can_reopen"] is True
    with client.app.state.database.session() as db:
        assert db.get(Application, application_id).completed_at is not None

    # completion is idempotent on the resulting state check, not on version:
    # a second complete call must be rejected because state is no longer pending_review
    again = client.post(
        f"/api/v1/applications/{application_id}/complete",
        headers={**csrf, "Idempotency-Key": "complete-2"},
        json={"version": version + 1},
    )
    assert again.status_code == 409

    # reopen requires a reason
    bad_reopen = client.post(
        f"/api/v1/applications/{application_id}/reopen",
        headers={**csrf, "Idempotency-Key": "reopen-1"},
        json={"version": version + 1},
    )
    assert bad_reopen.status_code == 422

    reopened = client.post(
        f"/api/v1/applications/{application_id}/reopen",
        headers={**csrf, "Idempotency-Key": "reopen-1"},
        json={"version": version + 1, "reason": "补充抵押物材料"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["state"] == "pending_review"
    with client.app.state.database.session() as db:
        application = db.get(Application, application_id)
        assert application.completed_at is None

    archived = client.post(
        f"/api/v1/applications/{application_id}/archive",
        headers={**csrf, "Idempotency-Key": "archive-1"},
        json={"version": application_version(client, application_id)},
    )
    assert archived.status_code == 200
    assert archived.json()["state"] == "archived"
    assert archived.json()["editable"] is False

    # archived can be reopened with reason
    reopened_again = client.post(
        f"/api/v1/applications/{application_id}/reopen",
        headers={**csrf, "Idempotency-Key": "reopen-2"},
        json={"version": application_version(client, application_id), "reason": "补交材料"},
    )
    assert reopened_again.status_code == 200
    assert reopened_again.json()["state"] == "pending_review"


def test_archive_rejected_while_jobs_running(client: TestClient) -> None:
    csrf = login(client, "owner")
    application_id = owner_application_id(client)
    with client.app.state.database.session() as db:
        from app.models import Document, DocumentJob, JobStatus, ProcessingStep

        document = Document(
            application_id=application_id,
            filename="材料.pdf",
            extension=".pdf",
            declared_mime="application/pdf",
            size_bytes=1,
            sha256="b" * 64,
            object_key="b",
        )
        db.add(document)
        db.flush()
        job = DocumentJob(document=document, status=JobStatus.RUNNING, claimed_by="w1")
        job.steps.append(ProcessingStep(name="validation", status=JobStatus.RUNNING))
        db.add(job)
        db.commit()
    response = client.post(
        f"/api/v1/applications/{application_id}/archive",
        headers={**csrf, "Idempotency-Key": "archive-1"},
        json={"version": 1},
    )
    assert response.status_code == 409
    assert "running" in response.json()["detail"]


def test_readonly_states_block_all_mutations(client: TestClient) -> None:
    csrf = login(client, "owner")
    application_id = owner_application_id(client)
    formal_reports(client, csrf, application_id)
    with client.app.state.database.session() as db:
        application = db.get(Application, application_id)
        application.lifecycle_state = "archived"
        db.commit()
        version = application.version

    update = client.put(
        f"/api/v1/applications/{application_id}",
        headers=csrf,
        json={
            "primary_borrower": {"type": "corporate", "name": "改名企业"},
            "product": "经营贷",
            "application_date": "2026-08-07",
            "proposed_signing_date": "2026-08-20",
            "version": version,
        },
    )
    assert update.status_code == 409

    resolution = client.post(
        f"/api/v1/applications/{application_id}/resolutions",
        headers={**csrf, "Idempotency-Key": "res-x"},
        json={
            "field_key": "loan_amount",
            "resolution_type": "manual",
            "value": "200000",
            "reason": "补录金额",
        },
    )
    assert resolution.status_code == 409

    upload = client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={**csrf, "Idempotency-Key": "up-x"},
        files={"file": ("材料.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert upload.status_code == 409

    run = client.post(
        f"/api/v1/applications/{application_id}/redline-runs",
        headers={**csrf, "Idempotency-Key": "run-x"},
    )
    assert run.status_code == 409

    # reads still work: direct URL attempts cannot bypass, but viewing is allowed
    assert client.get(f"/api/v1/applications/{application_id}").status_code == 200
    assert lifecycle(client, application_id)["state"] == "archived"


def test_other_officer_cannot_touch_application(client: TestClient) -> None:
    login(client, "owner")
    csrf_other = login(client, "other")
    application_id = owner_application_id(client)
    assert (
        client.get(f"/api/v1/applications/{application_id}").status_code == 404
    )
    complete = client.post(
        f"/api/v1/applications/{application_id}/complete",
        headers={**csrf_other, "Idempotency-Key": "complete-1"},
        json={"version": 1},
    )
    assert complete.status_code == 404
