from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.admin import router as admin_router
from app.admin_rules import router as admin_rules_router
from app.admin_templates import router as admin_templates_router
from app.applications import router as applications_router
from app.audit import router as audit_router
from app.auth import router as auth_router
from app.candidates import router as candidates_router
from app.completeness import seed_demo_templates
from app.completeness_api import router as completeness_router
from app.config import settings
from app.database import Database
from app.document_outputs import router as document_outputs_router
from app.documents import DocumentLimits
from app.documents import router as documents_router
from app.lifecycle import router as lifecycle_router
from app.logging_config import CorrelationMiddleware, setup_json_logging
from app.meta import router as meta_router
from app.models import DocumentJob, JobStatus, WorkerHeartbeat
from app.object_store import minio_objects
from app.redline import seed_demo_data
from app.redline_api import router as redline_router


def create_app(
    database_url: str | None = None,
    *,
    cookie_secure: bool | None = None,
    production: bool | None = None,
    object_store=None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Demo-only templates, rule packages, and synthetic LPR ship for
        # development and synthetic-material demos; production mode skips
        # seeding and rejects demo rules for formal redline reports.
        if not app.state.production:
            with app.state.database.session() as db:
                seed_demo_templates(db)
                seed_demo_data(db)
        yield

    setup_json_logging()
    app = FastAPI(title="ICRM-CA API", version="0.1.0", lifespan=lifespan)
    app.state.database = Database(database_url or settings.effective_database_url)
    app.state.cookie_secure = settings.cookie_secure if cookie_secure is None else cookie_secure
    app.state.production = settings.production if production is None else production
    app.state.session_hours = settings.session_hours
    app.state.document_limits = DocumentLimits(
        settings.max_material_bytes,
        settings.max_application_bytes,
        settings.max_application_materials,
    )
    app.state.object_store = object_store or minio_objects(settings)

    app.add_middleware(CorrelationMiddleware)

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(meta_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(admin_templates_router, prefix="/api/v1")
    app.include_router(admin_rules_router, prefix="/api/v1")
    app.include_router(applications_router, prefix="/api/v1")
    app.include_router(lifecycle_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(document_outputs_router, prefix="/api/v1")
    app.include_router(candidates_router, prefix="/api/v1")
    app.include_router(completeness_router, prefix="/api/v1")
    app.include_router(redline_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready(request: Request) -> dict:
        """Ready / degraded / failed with per-component detail.

        ``ready`` requires a working database, an object store, and (when jobs
        are waiting or running) a fresh worker heartbeat. ``degraded`` still
        returns 503 but reports exactly which component is unhealthy.
        """
        checks: dict[str, str] = {}
        with app.state.database.session() as db:
            try:
                db.execute(text("SELECT 1"))
                checks["database"] = "ok"
            except Exception:
                checks["database"] = "error"
        try:
            if not app.state.object_store.bucket_exists():
                raise RuntimeError("bucket missing")
            checks["object_store"] = "ok"
        except Exception:
            checks["object_store"] = "error"
        worker_status = "ok"
        pending_jobs = 0
        if checks["database"] == "ok":
            with app.state.database.session() as db:
                pending_jobs = (
                    db.query(DocumentJob)
                    .filter(DocumentJob.status.in_([JobStatus.WAITING, JobStatus.RUNNING]))
                    .count()
                )
            if pending_jobs:
                with app.state.database.session() as db:
                    heartbeat = (
                        db.query(WorkerHeartbeat)
                        .order_by(WorkerHeartbeat.last_seen_at.desc())
                        .first()
                    )
                fresh = (
                    heartbeat is not None
                    and (
                        datetime.now(UTC) - heartbeat.last_seen_at.replace(tzinfo=UTC)
                    ).total_seconds()
                    < 120
                )
                worker_status = "ok" if fresh else "stale"
        else:
            worker_status = "unknown"
        checks["worker"] = worker_status
        checks["cloud_gate"] = "ready" if settings.cloud_ready else "blocked"
        failed = [name for name, result in checks.items() if result in {"error", "stale"}]
        status_code = status.HTTP_200_OK if not failed else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(
            status_code=status_code,
            content={
                "status": (
                    "ready"
                    if not failed
                    else "degraded"
                    if checks.get("database") == "ok"
                    else "failed"
                ),
                "checks": checks,
                "pending_jobs": pending_jobs,
                "correlation_id": request.state.correlation_id,
            },
        )

    return app


app = create_app()
