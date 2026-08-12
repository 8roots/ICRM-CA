from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.dependencies import CSRF_COOKIE, SESSION_COOKIE, Csrf, CurrentUser, Db
from app.models import LoginSession, User
from app.security import random_token, token_hash, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: str
    username: str
    role: str


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(payload: LoginRequest, request: Request, response: Response, db: Db) -> None:
    origin = request.headers.get("origin")
    if not origin or urlparse(origin).netloc != request.headers.get("host"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid request origin")

    user = db.query(User).filter_by(username=payload.username).first()
    if not user or not user.enabled or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    session_token = random_token()
    csrf_token = random_token()
    session_hours = request.app.state.session_hours
    db.add(
        LoginSession(
            token_hash=token_hash(session_token),
            csrf_hash=token_hash(csrf_token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=session_hours),
        )
    )
    db.commit()
    cookie_options = {
        "secure": request.app.state.cookie_secure,
        "samesite": "strict",
        "max_age": session_hours * 3600,
        "path": "/",
    }
    response.set_cookie(SESSION_COOKIE, session_token, httponly=True, **cookie_options)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, **cookie_options)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> User:
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Db,
    user: CurrentUser,
    csrf: Csrf,
) -> None:
    session_token = request.cookies.get(SESSION_COOKIE)
    if session_token:
        db.query(LoginSession).filter_by(token_hash=token_hash(session_token)).delete()
        db.commit()
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=request.app.state.cookie_secure, samesite="strict"
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", secure=request.app.state.cookie_secure, samesite="strict"
    )
