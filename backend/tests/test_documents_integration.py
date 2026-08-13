import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer

from app.document_jobs import claim_next_job, finish_job, recover_stale_jobs, renew_claim
from app.main import create_app
from app.models import (
    Application,
    Base,
    Document,
    DocumentJob,
    DocumentOutput,
    ProcessingStep,
    User,
)
from app.object_store import MinioObjects
from app.parsing import Analysis
from app.security import hash_password
from app.worker import process_one


@pytest.fixture(scope="module")
def services():
    with (
        PostgresContainer("postgres:16.8-alpine") as postgres,
        MinioContainer("minio/minio:RELEASE.2025-02-07T23-21-09Z") as minio,
    ):
        client = minio.get_client()
        client.make_bucket("materials")
        database_url = postgres.get_connection_url().replace("psycopg2", "psycopg")
        objects = MinioObjects(
            minio.get_config()["endpoint"].removeprefix("http://"),
            minio.access_key,
            minio.secret_key,
            "materials",
        )
        app = create_app(database_url, cookie_secure=True, object_store=objects)
        Base.metadata.create_all(app.state.database.engine)
        with app.state.database.session() as db:
            db.add(
                User(
                    username="integration-owner",
                    password_hash=hash_password("approval officer password"),
                    role="approval_officer",
                )
            )
            db.commit()
        yield database_url, objects


def login(client: TestClient, username: str) -> dict[str, str]:
    client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://testserver"},
        json={"username": username, "password": "approval officer password"},
    )
    return {"X-CSRF-Token": client.cookies["icrm_csrf"]}


def upload(
    client: TestClient,
    application_id: str,
    csrf: dict[str, str],
    key: str,
    content: bytes,
    filename: str = "sample.pdf",
    mime: str = "application/pdf",
):
    return client.post(
        f"/api/v1/applications/{application_id}/documents",
        headers={**csrf, "Idempotency-Key": key},
        files={"file": (filename, io.BytesIO(content), mime)},
    )


def parseable_pdf(text: str = "public synthetic statement") -> bytes:
    import pymupdf

    pdf = pymupdf.open()
    page = pdf.new_page(width=300, height=200)
    page.insert_text((30, 50), text)
    return pdf.tobytes()


class NoopEngine:
    version = "integration-model-1"

    def analyze(self, image: bytes, *, run_ocr: bool) -> Analysis:
        return Analysis()


def create_application(app, borrower_name: str) -> str:
    with app.state.database.session() as db:
        owner = db.query(User).filter_by(username="integration-owner").one()
        application = Application(
            borrower_type="corporate",
            borrower_name=borrower_name,
            product="经营贷",
            application_date=date(2026, 8, 7),
            owner_id=owner.id,
        )
        db.add(application)
        db.commit()
        return application.id


def concurrent_upload_statuses(app, application_id: str, uploads: list[tuple[str, bytes]]):
    def send(key: str, content: bytes) -> int:
        with TestClient(app, base_url="https://testserver") as client:
            return upload(
                client, application_id, login(client, "integration-owner"), key, content
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sorted(pool.map(lambda args: send(*args), uploads))


def test_real_postgres_minio_upload_worker_and_restart(services) -> None:
    database_url, objects = services
    app = create_app(database_url, cookie_secure=True, object_store=objects)
    application_id = create_application(app, "示例企业")

    with TestClient(app, base_url="https://testserver") as client:
        csrf = login(client, "integration-owner")
        uploaded = upload(
            client, application_id, csrf, "integration-pdf", parseable_pdf()
        )
        assert uploaded.status_code == 202
        assert (
            client.get(f"/api/v1/applications/{application_id}").json()["lifecycle_state"]
            == "processing"
        )
        assert process_one(app.state.database, objects, "worker-before-restart", NoopEngine())
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as docx:
            docx.writestr("[Content_Types].xml", "content")
            docx.writestr("word/document.xml", "content")
        uploaded_docx = client.post(
            f"/api/v1/applications/{application_id}/documents",
            headers={**csrf, "Idempotency-Key": "integration-docx"},
            files={
                "file": (
                    "sample.docx",
                    package.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert uploaded_docx.status_code == 202
        assert process_one(app.state.database, objects, "worker-before-restart", NoopEngine())
        status = client.get(f"/api/v1/applications/{application_id}/documents").json()
        assert [document["processing_status"] for document in status] == ["success", "success"]
        assert (
            client.get(f"/api/v1/applications/{application_id}").json()["lifecycle_state"]
            == "pending_review"
        )
        assert not process_one(app.state.database, objects, "worker-after-restart", NoopEngine())


def test_rerun_appends_versioned_parsing_output_against_real_postgres(services) -> None:
    database_url, objects = services
    app = create_app(database_url, cookie_secure=True, object_store=objects)
    application_id = create_application(app, "版本追加企业")

    with TestClient(app, base_url="https://testserver") as client:
        csrf = login(client, "integration-owner")
        uploaded = upload(
            client, application_id, csrf, "versioned-pdf", parseable_pdf("version one text")
        )
        assert uploaded.status_code == 202
        assert process_one(app.state.database, objects, "version-worker", NoopEngine())
        document_id = uploaded.json()["document"]["id"]
        job_id = uploaded.json()["job"]["id"]
        first = client.get(f"/api/v1/documents/{document_id}/outputs").json()
        assert [output["version"] for output in first] == [1]
        assert first[0]["pages"][0]["blocks"][0]["text"] == "version one text"
        assert first[0]["model_version"] == "integration-model-1"

        rerun = client.post(
            f"/api/v1/jobs/{job_id}/retry",
            headers={**csrf, "Idempotency-Key": "integration-rerun"},
            json={
                "reason": "更换解析模型后重新解析",
                "selected_steps": ["parsing_ocr", "seal_detection"],
            },
        )
        assert rerun.status_code == 200
        assert process_one(app.state.database, objects, "version-worker", NoopEngine())
        versions = client.get(f"/api/v1/documents/{document_id}/outputs").json()
        assert [output["version"] for output in versions] == [1, 2]
        assert versions[0]["pages"][0]["blocks"][0]["text"] == "version one text"
        assert versions[1]["version"] == 2
        with app.state.database.session() as db:
            assert db.query(DocumentOutput).filter_by(document_id=document_id).count() == 2


def test_concurrent_uploads_respect_application_count_limit(services) -> None:
    database_url, objects = services
    app = create_app(database_url, cookie_secure=True, object_store=objects)
    app.state.document_limits.max_application_materials = 1
    application_id = create_application(app, "并发容量企业")
    statuses = concurrent_upload_statuses(
        app,
        application_id,
        [("one", b"%PDF-1.7 one"), ("two", b"%PDF-1.7 two")],
    )
    assert statuses == [202, 413]


def seed_waiting_jobs(app, count: int) -> None:
    with app.state.database.session() as db:
        owner = db.query(User).filter_by(username="integration-owner").one()
        application = Application(
            borrower_type="corporate",
            borrower_name="并发任务企业",
            product="经营贷",
            application_date=date(2026, 8, 7),
            owner_id=owner.id,
        )
        db.add(application)
        db.flush()
        for number in range(count):
            document = Document(
                application_id=application.id,
                filename=f"claim-{number}.pdf",
                extension=".pdf",
                declared_mime="application/pdf",
                size_bytes=10,
                sha256=f"{number + 5}" * 64,
                object_key=f"claim/{application.id}/{number}",
            )
            db.add(document)
            db.flush()
            job = DocumentJob(document_id=document.id)
            job.steps.append(ProcessingStep(name="validation"))
            db.add(job)
        db.commit()


def test_concurrent_workers_claim_distinct_postgres_jobs_and_old_claim_cannot_finish(
    services,
) -> None:
    database_url, _ = services
    app = create_app(database_url, cookie_secure=True)
    seed_waiting_jobs(app, 2)

    def claim(worker: str):
        with app.state.database.session() as db:
            job = claim_next_job(db, worker)
            return job.id, job.claim_token

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ["worker-a", "worker-b"]))
    assert len({job_id for job_id, _ in claims}) == 2

    stale_job_id, stale_token = claims[0]
    with app.state.database.session() as db:
        stale = db.get(DocumentJob, stale_job_id)
        stale.claimed_at = stale.claimed_at - timedelta(minutes=10)
        db.commit()
        assert recover_stale_jobs(db, timedelta(minutes=5)) == 1
        stale.available_at = datetime.now(UTC)
        db.commit()
        replacement = claim_next_job(db, "replacement")
        assert replacement.id == stale_job_id
        assert replacement.claim_token != stale_token

    with app.state.database.session() as db:
        stale_copy = db.get(DocumentJob, stale_job_id)
        assert not finish_job(db, stale_copy, "success", claim_token=stale_token)
        assert db.get(DocumentJob, stale_job_id).status == "running"


def test_active_worker_renews_lease(services) -> None:
    database_url, _ = services
    app = create_app(database_url, cookie_secure=True)
    seed_waiting_jobs(app, 1)
    with app.state.database.session() as db:
        claimed = claim_next_job(db, "live-worker")
        claimed.claimed_at = claimed.claimed_at - timedelta(minutes=10)
        db.commit()
        token = claimed.claim_token
    assert renew_claim(app.state.database, claimed.id, token)
    with app.state.database.session() as db:
        assert recover_stale_jobs(db, timedelta(minutes=5)) == 0


def test_concurrent_uploads_respect_application_size_limit(services) -> None:
    database_url, objects = services
    app = create_app(database_url, cookie_secure=True, object_store=objects)
    app.state.document_limits.max_application_bytes = 20
    application_id = create_application(app, "并发总大小企业")
    statuses = concurrent_upload_statuses(
        app,
        application_id,
        [("bytes-one", b"%PDF-1.7 one"), ("bytes-two", b"%PDF-1.7 two")],
    )
    assert statuses == [202, 413]


def test_concurrent_idempotency_payload_mismatch_returns_conflict(services) -> None:
    database_url, objects = services
    app = create_app(database_url, cookie_secure=True, object_store=objects)
    application_id = create_application(app, "并发幂等企业")

    def send(content: bytes) -> int:
        with TestClient(
            app, base_url="https://testserver", raise_server_exceptions=False
        ) as client:
            return upload(
                client,
                application_id,
                login(client, "integration-owner"),
                "same-key",
                content,
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(send, [b"%PDF-1.7 one", b"%PDF-1.7 two"]))
    assert sorted(statuses) == [202, 409]
