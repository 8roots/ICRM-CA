"""Production Compose: only the TLS proxy is exposed; secrets from files.

Parses docker-compose.prod.yml and asserts the network/secret posture from
the ticket: no public port on Postgres/MinIO/API/worker, production and
cookie-secure flags on the API, and no dev default credentials anywhere.
"""

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"


def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_only_proxy_exposes_ports() -> None:
    data = compose()
    services = data["services"]
    exposed = [
        name for name, service in services.items() if "ports" in service
    ]
    assert exposed == ["proxy"], f"unexpected exposed services: {exposed}"


def test_proxy_is_the_only_entrypoint() -> None:
    data = compose()
    proxy = data["services"]["proxy"]
    assert any("443" in port for port in proxy["ports"])
    assert "nginx.prod.conf" in proxy["build"]["args"]["NGINX_CONF"]
    assert "./certs" in proxy["volumes"][0]


def test_internal_services_have_no_ports_and_use_secrets() -> None:
    data = compose()
    for name in ("postgres", "minio", "api", "worker", "migrate"):
        assert "ports" not in data["services"][name], f"{name} must not publish ports"
        assert "secrets" in data["services"][name], f"{name} must mount secrets"


def test_no_dev_default_credentials_in_environment() -> None:
    data = compose()
    env = data["services"]["api"]["environment"]
    assert env.get("ICRM_DATABASE_URL_FILE") == "/run/secrets/database_url"
    assert env.get("ICRM_MINIO_ACCESS_KEY_FILE") == "/run/secrets/minio_root_user"
    assert env.get("ICRM_MINIO_SECRET_KEY_FILE") == "/run/secrets/minio_root_password"
    assert env.get("ICRM_DEEPSEEK_API_KEY_FILE") == "/run/secrets/deepseek_api_key"
    # production posture is fixed by the compose file itself, not by env files
    assert "ICRM_PRODUCTION" in env
    assert "ICRM_COOKIE_SECURE" in env
    joined = str(env).lower()
    for dev_value in ("local-development-only", "local-minio-password", "icrm-local"):
        assert dev_value not in joined


def test_postgres_and_minio_use_secret_files() -> None:
    data = compose()
    assert data["services"]["postgres"]["environment"]["POSTGRES_PASSWORD_FILE"]
    assert data["services"]["minio"]["environment"]["MINIO_ROOT_USER_FILE"]
    assert data["services"]["minio"]["environment"]["MINIO_ROOT_PASSWORD_FILE"]


def test_secrets_are_file_backed() -> None:
    data = compose()
    for name in (
        "postgres_password",
        "minio_root_user",
        "minio_root_password",
        "database_url",
        "deepseek_api_key",
    ):
        assert name in data["secrets"]
        assert data["secrets"][name]["file"].startswith("./secrets/")
