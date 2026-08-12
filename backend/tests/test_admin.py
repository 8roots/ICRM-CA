import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.cli import create_first_admin
from app.main import create_app
from app.models import Base, User
from app.security import hash_password


def test_first_admin_requires_strong_supplied_password_and_runs_once() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="at least 12"):
            create_first_admin(db, "admin", "password")

        admin = create_first_admin(db, "admin", "a unique supplied password")
        assert admin.role == "administrator"
        assert admin.password_hash != "a unique supplied password"

        with pytest.raises(ValueError, match="already exists"):
            create_first_admin(db, "another-admin", "another supplied password")


def test_cli_entry_point_prompts_for_password(tmp_path) -> None:
    database_path = tmp_path / "cli.sqlite"
    database_url = f"sqlite+pysqlite:///{database_path}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    password = "interactive supplied password"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.cli import create_admin_command; create_admin_command()",
            "administrator",
        ],
        input=f"{password}\n{password}\n",
        capture_output=True,
        text=True,
        env={**os.environ, "ICRM_DATABASE_URL": database_url},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert password not in result.stdout + result.stderr
    with Session(engine) as db:
        admin = db.query(User).one()
        assert admin.username == "administrator"
        assert admin.password_hash != password


def test_admin_can_create_and_enable_approval_officer(caplog) -> None:
    app = create_app("sqlite+pysqlite:///:memory:", cookie_secure=True)
    Base.metadata.create_all(app.state.database.engine)
    with app.state.database.session() as db:
        db.add(
            User(
                username="admin",
                password_hash=hash_password("administrator password"),
                role="administrator",
                enabled=True,
            )
        )
        db.commit()

    with TestClient(app, base_url="https://testserver") as client:
        assert (
            client.post(
                "/api/v1/auth/login",
                headers={"Origin": "https://testserver"},
                json={"username": "admin", "password": "administrator password"},
            ).status_code
            == 204
        )
        headers = {"X-CSRF-Token": client.cookies["icrm_csrf"]}

        created = client.post(
            "/api/v1/admin/users",
            headers=headers,
            json={
                "username": "new-officer",
                "password": "officer supplied password",
                "enabled": False,
            },
        )
        assert created.status_code == 201
        assert created.json()["enabled"] is False
        assert "password" not in created.text
        assert "officer supplied password" not in caplog.text
        with app.state.database.session() as db:
            officer = db.get(User, created.json()["id"])
            assert officer.password_hash != "officer supplied password"

        enabled = client.patch(
            f"/api/v1/admin/users/{created.json()['id']}",
            headers=headers,
            json={"enabled": True, "version": 1},
        )
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True
        assert enabled.json()["version"] == 2

        stale = client.patch(
            f"/api/v1/admin/users/{created.json()['id']}",
            headers=headers,
            json={"enabled": False, "version": 1},
        )
        assert stale.status_code == 409
