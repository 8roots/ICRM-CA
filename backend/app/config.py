from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ICRM_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://icrm:icrm@postgres:5432/icrm"
    cookie_secure: bool = True
    session_hours: int = 8


settings = Settings()
