import io
import time
from datetime import date

import pytest
from urllib3.exceptions import MaxRetryError

from app.database import Database
from app.main import create_app
from app.models import Application, Base, Document, DocumentJob, ProcessingStep, User
from app.worker import process_one


class Objects:
    def open(self, key: str):
        return io.BytesIO(b"%PDF-1.7")


@pytest.fixture
def queued_job() -> tuple[Database, str]:
    app = create_app("sqlite+pysqlite:///:memory:", object_store=Objects())
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session() as db:
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
            filename="sample.pdf",
            extension=".pdf",
            declared_mime="application/pdf",
            size_bytes=8,
            sha256="a" * 64,
            object_key="sample",
        )
        db.add(document)
        db.flush()
        job = DocumentJob(document_id=document.id)
        job.steps.append(ProcessingStep(name="validation"))
        db.add(job)
        db.commit()
        return app.state.database, job.id


def test_validation_renews_lease_while_computing(queued_job, monkeypatch) -> None:
    database, job_id = queued_job

    def slow_validate(document, stream) -> None:
        with database.session() as db:
            job = db.get(DocumentJob, job_id)
            before = job.claimed_at
        time.sleep(0.01)
        with database.session() as db:
            job = db.get(DocumentJob, job_id)
            assert job.claimed_at >= before

    monkeypatch.setattr("app.worker.LEASE_RENEWAL_SECONDS", 0.001)
    monkeypatch.setattr("app.worker.validate", slow_validate)
    assert process_one(database, Objects(), "worker")


def test_minio_network_error_is_transient(queued_job, monkeypatch) -> None:
    database, job_id = queued_job

    class UnavailableObjects:
        def open(self, key: str):
            raise MaxRetryError(None, key, "unavailable")

    assert process_one(database, UnavailableObjects(), "worker")
    with database.session() as db:
        job = db.get(DocumentJob, job_id)
        assert job.status == "waiting"
        assert job.error_code == "object_store_unavailable"


def test_unexpected_validator_error_is_permanent_not_transient(queued_job, monkeypatch) -> None:
    database, job_id = queued_job

    def broken(document, stream) -> None:
        raise RuntimeError("programming error")

    monkeypatch.setattr("app.worker.validate", broken)
    assert process_one(database, Objects(), "worker")
    with database.session() as db:
        job = db.get(DocumentJob, job_id)
        assert job.status == "failed"
        assert job.error_code == "unexpected_validation_error"
        assert job.attempts == 1
