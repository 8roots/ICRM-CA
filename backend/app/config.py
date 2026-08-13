from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ICRM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://icrm:icrm@postgres:5432/icrm"
    cookie_secure: bool = True
    session_hours: int = 8
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "icrm-local"
    minio_secret_key: str = "local-minio-password"
    minio_bucket: str = "materials"
    models_dir: str = "/app/models"
    max_material_bytes: int = 200 * 1024 * 1024
    max_application_bytes: int = 2 * 1024 * 1024 * 1024
    max_application_materials: int = 100


settings = Settings()
