from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ICRM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://icrm:icrm@postgres:5432/icrm"
    database_url_file: str = ""
    production: bool = False
    cookie_secure: bool = True
    session_hours: int = 8
    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "icrm-local"
    minio_access_key_file: str = ""
    minio_secret_key: str = "local-minio-password"
    minio_secret_key_file: str = ""
    minio_bucket: str = "materials"
    models_dir: str = "/app/models"
    lender_qualification: str = "small_loan_company"
    max_material_bytes: int = 200 * 1024 * 1024
    max_application_bytes: int = 2 * 1024 * 1024 * 1024
    max_application_materials: int = 100
    deepseek_base_url: str = ""
    deepseek_api_key: str = ""
    deepseek_api_key_file: str = ""
    deepseek_model: str = ""
    # Cloud readiness gate (design section 10.2 / 13.2): the provider's
    # no-training confirmation and an explicit retention period must be set
    # before the cloud path is enabled in production. Local extraction never
    # depends on this gate.
    cloud_training_confirmation: bool = False
    cloud_retention_days: int | None = None

    @staticmethod
    def _resolve(value: str, secret_file: str) -> str:
        if secret_file:
            return Path(secret_file).read_text(encoding="utf-8").strip()
        return value

    @property
    def effective_database_url(self) -> str:
        return self._resolve(self.database_url, self.database_url_file)

    @property
    def effective_minio_access_key(self) -> str:
        return self._resolve(self.minio_access_key, self.minio_access_key_file)

    @property
    def effective_minio_secret_key(self) -> str:
        return self._resolve(self.minio_secret_key, self.minio_secret_key_file)

    @property
    def effective_deepseek_api_key(self) -> str:
        return self._resolve(self.deepseek_api_key, self.deepseek_api_key_file)

    @property
    def cloud_configured(self) -> bool:
        return bool(
            self.deepseek_base_url and self.effective_deepseek_api_key and self.deepseek_model
        )

    @property
    def cloud_confirmed(self) -> bool:
        return bool(self.cloud_training_confirmation and self.cloud_retention_days is not None)

    @property
    def cloud_ready(self) -> bool:
        return self.cloud_configured and self.cloud_confirmed

    @property
    def cloud_gate_blockers(self) -> list[str]:
        blockers: list[str] = []
        if (
            not self.deepseek_base_url
            or not self.effective_deepseek_api_key
            or not self.deepseek_model
        ):
            blockers.append("missing_credentials")
        if not self.cloud_training_confirmation:
            blockers.append("missing_training_confirmation")
        if self.cloud_retention_days is None:
            blockers.append("missing_retention_period")
        return blockers


settings = Settings()
