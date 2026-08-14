"""Seed CI/e2e users against a disposable Compose stack.

Used by CI (and local e2e runs) to provision the first administrator plus an
approval officer from generated credentials, so checks pass from a fresh
clone with no hidden local state. Demo templates/rules are seeded by the API
startup in development mode; this script only creates accounts.

Credentials come from environment variables:

    ICRM_E2E_ADMIN_USERNAME   (default: e2e-admin)
    ICRM_E2E_ADMIN_PASSWORD   (required, at least 12 characters)
    ICRM_E2E_OFFICER_USERNAME (default: e2e-officer)
    ICRM_E2E_OFFICER_PASSWORD (required, at least 12 characters)

Idempotent: rerunning updates the same usernames in place. Run inside the
``api`` container (``docker compose exec -T api ...``) or anywhere with
database access via ICRM_DATABASE_URL[_FILE].
"""

import os
import sys

from sqlalchemy import func, select

from app.cli import create_first_admin
from app.config import settings
from app.database import Database
from app.models import User
from app.security import hash_password


def _credential(name: str, default: str | None) -> str:
    value = os.environ.get(name) or default
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def upsert_user(db, username: str, password: str, role: str) -> None:
    if len(password) < 12:
        raise ValueError(f"password for {username!r} must be at least 12 characters")
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                enabled=True,
            )
        )
    else:
        user.password_hash = hash_password(password)
        user.role = role
        user.enabled = True
    db.commit()
    print(f"{role}: {username!r} ready")


def main() -> None:
    admin_username = _credential("ICRM_E2E_ADMIN_USERNAME", "e2e-admin")
    admin_password = _credential("ICRM_E2E_ADMIN_PASSWORD", None)
    officer_username = _credential("ICRM_E2E_OFFICER_USERNAME", "e2e-officer")
    officer_password = _credential("ICRM_E2E_OFFICER_PASSWORD", None)
    with Database(settings.effective_database_url).session() as db:
        if db.scalar(select(func.count()).select_from(User)) == 0:
            create_first_admin(db, admin_username, admin_password)
            print(f"administrator: {admin_username!r} created as first admin")
        else:
            upsert_user(db, admin_username, admin_password, "administrator")
        upsert_user(db, officer_username, officer_password, "approval_officer")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, SystemExit) as error:
        print(f"seed-e2e: {error}", file=sys.stderr)
        sys.exit(1)
