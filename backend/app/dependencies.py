import hmac
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.models import LoginSession, User
from app.security import token_hash

SESSION_COOKIE = "icrm_session"
CSRF_COOKIE = "icrm_csrf"


def get_db(request: Request):
    with request.app.state.database.session() as db:
        yield db


Db = Annotated[Session, Depends(get_db)]


def current_user(
    db: Db,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> User:
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    login_session = db.query(LoginSession).filter_by(token_hash=token_hash(session_token)).first()
    if not login_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    expires_at = login_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= datetime.now(UTC) or not login_session.user.enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return login_session.user


CurrentUser = Annotated[User, Depends(current_user)]


def require_csrf(
    db: Db,
    user: CurrentUser,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    if not session_token or not csrf_cookie or not csrf_header:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    login_session = db.query(LoginSession).filter_by(token_hash=token_hash(session_token)).first()
    supplied = token_hash(csrf_header)
    if (
        not login_session
        or not hmac.compare_digest(csrf_cookie, csrf_header)
        or not hmac.compare_digest(login_session.csrf_hash, supplied)
        or login_session.user_id != user.id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")


Csrf = Annotated[None, Depends(require_csrf)]


def administrator(user: CurrentUser) -> User:
    if user.role != "administrator":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    return user


Administrator = Annotated[User, Depends(administrator)]
