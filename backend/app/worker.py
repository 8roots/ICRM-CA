import logging
import socket
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session
from urllib3.exceptions import HTTPError as Urllib3Error

from app.classification import run_classification
from app.cloud_extraction import DeepSeekClient
from app.config import settings
from app.database import Database
from app.document_jobs import claim_next_job, finish_job, recover_stale_jobs, renew_claim, utcnow
from app.extraction_service import run_candidate_extraction
from app.logging_config import setup_json_logging
from app.models import DocumentOutput, JobStatus, ProcessingStepName, WorkerHeartbeat
from app.object_store import MinioObjects, minio_objects
from app.paddle_engine import PaddleEngine
from app.parsed_outputs import store_parsed_output
from app.parsing import IMAGE_EXTENSIONS, PDF_EXTENSIONS, ImageAnalysisEngine, parse_material
from app.structured import parse_structured
from app.validation import ValidationError, validate

setup_json_logging()
logger = logging.getLogger("icrm.worker")
heartbeat = Path("/tmp/icrm-worker-heartbeat")
LEASE_RENEWAL_SECONDS = 30
PAGE_FORMAT_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS


def record_heartbeat(db: Session) -> None:
    heartbeat.touch()
    worker_id = socket.gethostname()
    existing = db.get(WorkerHeartbeat, worker_id)
    if existing:
        existing.last_seen_at = datetime.now(UTC)
    else:
        db.add(WorkerHeartbeat(worker_id=worker_id, hostname=worker_id))
    db.commit()


def process_one(
    database: Database,
    objects: MinioObjects,
    worker_id: str,
    image_engine: ImageAnalysisEngine | None = None,
    cloud_client: DeepSeekClient | None = None,
) -> bool:
    with database.session() as db:
        recover_stale_jobs(db, timedelta(minutes=5))
        job = claim_next_job(db, worker_id)
        if not job:
            return False
        claim_token = job.claim_token
        running_steps = {step.name: step for step in job.steps if step.status == JobStatus.RUNNING}
        try:
            source = objects.open(job.document.object_key)
            stop_renewal = threading.Event()

            def renew_lease() -> None:
                while not stop_renewal.wait(LEASE_RENEWAL_SECONDS):
                    renew_claim(database, job.id, claim_token)

            renewal = threading.Thread(target=renew_lease, daemon=True)
            renewal.start()
            try:
                with tempfile.SpooledTemporaryFile(max_size=1024 * 1024) as material:
                    while chunk := source.read(1024 * 1024):
                        renew_claim(database, job.id, claim_token)
                        material.write(chunk)
                    if ProcessingStepName.VALIDATION in running_steps:
                        material.seek(0)
                        validate(job.document, material)
                        validation_step = running_steps[ProcessingStepName.VALIDATION]
                        validation_step.status = JobStatus.SUCCESS
                        validation_step.finished_at = utcnow()
                    output_status = JobStatus.SUCCESS
                    parsing_steps = {
                        ProcessingStepName.PARSING_OCR,
                        ProcessingStepName.SEAL_DETECTION,
                    } & running_steps.keys()
                    parse_error_code = None
                    if parsing_steps:
                        material.seek(0)
                        extension = Path(job.document.filename).suffix.lower()
                        if extension in PAGE_FORMAT_EXTENSIONS:
                            if image_engine is None:
                                raise RuntimeError("image analysis engine is not configured")
                            parsed = parse_material(job.document.filename, material, image_engine)
                        else:
                            parsed = parse_structured(job.document.filename, material)
                        store_parsed_output(db, job.document_id, parsed)
                        output_status = JobStatus(parsed.status)
                        if output_status == JobStatus.PARTIAL_SUCCESS:
                            parse_error_code = "partial_page_failure"
                        elif output_status == JobStatus.FAILED:
                            parse_error_code = "all_pages_failed"
                        for name in parsing_steps:
                            running_steps[name].status = output_status
                            running_steps[name].finished_at = utcnow()
                            running_steps[name].error_code = parse_error_code
                    extraction_step = running_steps.get(ProcessingStepName.CANDIDATE_EXTRACTION)
                    classification_step = running_steps.get(ProcessingStepName.CLASSIFICATION)
                    if extraction_step is not None or classification_step is not None:
                        output = (
                            db.query(DocumentOutput)
                            .filter_by(document_id=job.document_id)
                            .order_by(DocumentOutput.version.desc())
                            .first()
                        )
                        if output and output.status in {
                            JobStatus.SUCCESS,
                            JobStatus.PARTIAL_SUCCESS,
                        }:
                            if classification_step is not None:
                                run_classification(db, job.document, output)
                                classification_step.status = JobStatus.SUCCESS
                                classification_step.finished_at = utcnow()
                            if extraction_step is not None:
                                result = run_candidate_extraction(
                                    db, job.document, output, cloud_client
                                )
                                extraction_step.status = result.step_status
                                extraction_step.error_code = result.error_code
                                extraction_step.finished_at = utcnow()
                                if result.step_status == JobStatus.PARTIAL_SUCCESS:
                                    output_status = JobStatus.PARTIAL_SUCCESS
                                    parse_error_code = result.error_code
                        else:
                            if classification_step is not None:
                                classification_step.status = JobStatus.NOT_APPLICABLE
                                classification_step.finished_at = utcnow()
                            if extraction_step is not None:
                                extraction_step.status = JobStatus.NOT_APPLICABLE
                                extraction_step.finished_at = utcnow()
            finally:
                stop_renewal.set()
                renewal.join()
                source.close()
            finish_job(db, job, output_status, claim_token=claim_token, error_code=parse_error_code)
        except ValidationError as exc:
            finish_job(
                db,
                job,
                JobStatus.MANUAL_HANDLING if exc.manual_handling else JobStatus.FAILED,
                claim_token=claim_token,
                error_code=exc.code,
            )
        except (ConnectionError, OSError, TimeoutError, Urllib3Error):
            logging.exception("object store unavailable job_id=%s", job.id)
            finish_job(
                db,
                job,
                JobStatus.FAILED,
                claim_token=claim_token,
                error_code="object_store_unavailable",
                transient=True,
            )
        except Exception:
            logging.exception("unexpected validation error job_id=%s", job.id)
            finish_job(
                db,
                job,
                JobStatus.FAILED,
                claim_token=claim_token,
                error_code="unexpected_validation_error",
                transient=False,
            )
        return True


def run() -> None:
    database = Database(settings.effective_database_url)
    objects = minio_objects(settings)
    worker_id = socket.gethostname()
    image_engine = PaddleEngine(settings.models_dir)
    cloud_client = DeepSeekClient(
        settings.deepseek_base_url,
        settings.effective_deepseek_api_key,
        settings.deepseek_model,
        cloud_confirmed=settings.cloud_confirmed,
    )
    if not settings.cloud_ready:
        logger.info("cloud extraction disabled blockers=%s", ",".join(settings.cloud_gate_blockers))
    while True:
        worked = process_one(database, objects, worker_id, image_engine, cloud_client)
        with database.session() as db:
            record_heartbeat(db)
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    run()
