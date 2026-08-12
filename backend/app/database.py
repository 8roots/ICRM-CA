from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Database:
    def __init__(self, url: str) -> None:
        options: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            options.update(
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
        self.engine = create_engine(url, **options)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        db = self._sessions()
        try:
            yield db
        finally:
            db.close()
