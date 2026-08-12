from datetime import UTC, datetime, timedelta

from app.database import Database
from app.document_jobs import claim_next_job, finish_job, recover_stale_jobs
from app.models import Application, Base, Document, DocumentJob, ProcessingStep, User


def database() -> Database:
    db = Database("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(db.engine)
    with db.session() as session:
        user = User(username="owner", password_hash="hash", role="approval_officer")
        session.add(user)
        session.flush()
        application = Application(
            borrower_type="corporate",
            borrower_name="示例企业",
            product="经营贷",
            application_date=datetime.now(UTC).date(),
            owner_id=user.id,
        )
        session.add(application)
        session.flush()
        for number in range(2):
            document = Document(
                application_id=application.id,
                filename=f"{number}.pdf",
                extension=".pdf",
                declared_mime="application/pdf",
                size_bytes=10,
                sha256=str(number) * 64,
                object_key=f"objects/{number}",
            )
            session.add(document)
            session.flush()
            job = DocumentJob(document_id=document.id, status="waiting")
            session.add(job)
            session.flush()
            session.add(ProcessingStep(job_id=job.id, name="validation", status="waiting"))
        session.commit()
    return db


def test_claims_each_job_once_and_completed_step_is_not_duplicated() -> None:
    db = database()
    with db.session() as first, db.session() as second:
        first_job = claim_next_job(first, "worker-a")
        second_job = claim_next_job(second, "worker-b")
        assert first_job is not None and second_job is not None
        assert first_job.document.processing_status == "running"
        assert first_job.id != second_job.id
        finish_job(first, first_job, "success")

    with db.session() as session:
        completed = session.get(DocumentJob, first_job.id)
        assert completed.status == "success"
        assert completed.steps[0].status == "success"
        assert claim_next_job(session, "worker-c") is None


def test_claim_runs_validation_without_queueing_not_applicable_future_steps() -> None:
    db = database()
    with db.session() as session:
        job = session.query(DocumentJob).first()
        job.steps.append(ProcessingStep(name="parsing_ocr", status="not_applicable"))
        session.commit()
        claimed = claim_next_job(session, "worker")
        assert [(step.name, step.status) for step in claimed.steps] == [
            ("validation", "running"),
            ("parsing_ocr", "not_applicable"),
        ]


def test_worker_restart_recovers_stale_running_job_without_losing_attempt() -> None:
    db = database()
    with db.session() as session:
        job = claim_next_job(session, "dead-worker")
        job.claimed_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()
        job_id = job.id

    with db.session() as session:
        assert recover_stale_jobs(session, timedelta(minutes=5)) == 1
        recovered_job = session.get(DocumentJob, job_id)
        assert recovered_job.available_at.replace(tzinfo=UTC) > datetime.now(UTC)
        recovered_job.available_at = datetime.now(UTC)
        session.commit()
        recovered = claim_next_job(session, "new-worker")
        assert recovered.id == job_id
        assert recovered.attempts == 2


def test_stale_job_stops_after_three_crashed_attempts() -> None:
    db = database()
    with db.session() as session:
        job = session.query(DocumentJob).first()
        job.status = "running"
        job.attempts = 3
        job.claimed_at = datetime.now(UTC) - timedelta(minutes=10)
        session.commit()
        assert recover_stale_jobs(session, timedelta(minutes=5)) == 1
        assert job.status == "failed"
        assert job.error_code == "worker_crash_attempts_exhausted"
        assert claim_next_job(session, "worker") is not job


def test_transient_failures_back_off_three_times_but_permanent_failures_do_not_retry() -> None:
    db = database()
    with db.session() as session:
        job = claim_next_job(session, "worker")
        finish_job(session, job, "failed", error_code="object_store_unavailable", transient=True)
        assert job.status == "waiting"
        assert job.available_at > datetime.now(UTC)

        job.available_at = datetime.now(UTC)
        job = claim_next_job(session, "worker")
        job.available_at = datetime.now(UTC)
        finish_job(session, job, "failed", error_code="object_store_unavailable", transient=True)
        job.available_at = datetime.now(UTC)
        job = claim_next_job(session, "worker")
        finish_job(session, job, "failed", error_code="object_store_unavailable", transient=True)
        assert job.status == "failed"

        other = session.query(DocumentJob).filter(DocumentJob.id != job.id).first()
        other.available_at = datetime.now(UTC)
        permanent = claim_next_job(session, "worker")
        finish_job(session, permanent, "failed", error_code="signature_mismatch", transient=False)
        assert permanent.status == "failed"
        assert permanent.attempts == 1
