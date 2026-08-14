import argparse
import getpass
import sys

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Database
from app.models import User
from app.security import hash_password


def create_first_admin(db: Session, username: str, password: str) -> User:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(42674801)"))
    if db.scalar(select(func.count()).select_from(User)):
        raise ValueError("A user already exists; use the administration API")
    admin = User(
        username=username,
        password_hash=hash_password(password),
        role="administrator",
        enabled=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def create_admin_command() -> None:
    parser = argparse.ArgumentParser(description="Create the first ICRM-CA administrator")
    parser.add_argument("username", help="administrator username")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password (and confirmation) from stdin instead of a TTY; "
        "used by CI and unattended provisioning",
    )
    args = parser.parse_args()
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
        confirmation = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Administrator password: ")
        confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        parser.error("passwords do not match")
    try:
        with Database(settings.effective_database_url).session() as db:
            create_first_admin(db, args.username, password)
    except ValueError as error:
        parser.error(str(error))
    print(f"Administrator {args.username!r} created")
