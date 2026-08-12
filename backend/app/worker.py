import logging
import socket
import tempfile
import threading
import time
from datetime import timedelta
from pathlib import Path

from urllib3.exceptions import HTTPError as Urllib3Error

from app.config import settings
from app.database import Database
from app.document_jobs import claim_next_job, finish_job, recover_stale_jobs, renew_claim
from app.models import JobStatus
from app.object_store import MinioObjects, minio_objects
from app.validation import ValidationError, validate

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
heartbeat = Path("/tmp/icrm-worker-heartbeat")
LEASE_RENEWAL_SECONDS = 30


def process_one(database: Database, objects: MinioObjects, worker_id: str) -> bool:
    with database.session() as db:
        recover_stale_jobs(db, timedelta(minutes=5))
        job = claim_next_job(db, worker_id)
        if not job:
            return False
        claim_token = job.claim_token
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
                    material.seek(0)
                    validate(job.document, material)
            finally:
                stop_renewal.set()
                renewal.join()
                source.close()
            finish_job(db, job, JobStatus.SUCCESS, claim_token=claim_token)
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
    database = Database(settings.database_url)
    objects = minio_objects(settings)
    worker_id = socket.gethostname()
    while True:
        worked = process_one(database, objects, worker_id)
        heartbeat.touch()
        if not worked:
            time.sleep(2)


if __name__ == "__main__":
    run()
