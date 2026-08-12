from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import update

from app.dependencies import Administrator, Csrf, Db
from app.models import User
from app.security import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=1024)
    enabled: bool = True


class UpdateUserRequest(BaseModel):
    enabled: bool
    version: int = Field(ge=1)


class ManagedUserResponse(BaseModel):
    id: str
    username: str
    role: str
    enabled: bool
    version: int


@router.get("/users", response_model=list[ManagedUserResponse])
def list_users(db: Db, admin: Administrator) -> list[User]:
    return db.query(User).order_by(User.username).all()


@router.post("/users", response_model=ManagedUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> User:
    if db.query(User).filter_by(username=payload.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="approval_officer",
        enabled=payload.enabled,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=ManagedUserResponse)
def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    db: Db,
    admin: Administrator,
    csrf: Csrf,
) -> User:
    result = db.execute(
        update(User)
        .where(User.id == user_id, User.version == payload.version)
        .values(enabled=payload.enabled, version=User.version + 1)
    )
    if result.rowcount == 0:
        if db.get(User, user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        raise HTTPException(status.HTTP_409_CONFLICT, "Stale version")
    db.commit()
    return db.get(User, user_id)
