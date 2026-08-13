from fastapi import FastAPI
from sqlalchemy import text

from app.admin import router as admin_router
from app.applications import router as applications_router
from app.auth import router as auth_router
from app.candidates import router as candidates_router
from app.config import settings
from app.database import Database
from app.document_outputs import router as document_outputs_router
from app.documents import DocumentLimits
from app.documents import router as documents_router
from app.object_store import minio_objects


def create_app(
    database_url: str | None = None,
    *,
    cookie_secure: bool | None = None,
    object_store=None,
) -> FastAPI:
    app = FastAPI(title="ICRM-CA API", version="0.1.0")
    app.state.database = Database(database_url or settings.database_url)
    app.state.cookie_secure = settings.cookie_secure if cookie_secure is None else cookie_secure
    app.state.session_hours = settings.session_hours
    app.state.document_limits = DocumentLimits(
        settings.max_material_bytes,
        settings.max_application_bytes,
        settings.max_application_materials,
    )
    app.state.object_store = object_store or minio_objects(settings)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(admin_router, prefix="/api/v1")
    app.include_router(applications_router, prefix="/api/v1")
    app.include_router(documents_router, prefix="/api/v1")
    app.include_router(document_outputs_router, prefix="/api/v1")
    app.include_router(candidates_router, prefix="/api/v1")

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> dict[str, str]:
        with app.state.database.session() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready"}

    return app


app = create_app()
