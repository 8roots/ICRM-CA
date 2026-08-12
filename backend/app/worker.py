import logging
import time
from pathlib import Path

from sqlalchemy import text

from app.config import settings
from app.database import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
heartbeat = Path("/tmp/icrm-worker-heartbeat")


def run() -> None:
    database = Database(settings.database_url)
    while True:
        with database.session() as db:
            db.execute(text("SELECT 1"))
        heartbeat.touch()
        time.sleep(10)


if __name__ == "__main__":
    run()
