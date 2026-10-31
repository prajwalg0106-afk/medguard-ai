
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


BASE_DIR = Path(__file__).resolve().parent


def get_database_url() -> str:
    """Return the configured database URL.

    PostgreSQL is selected through DATABASE_URL.
    SQLite remains the local-development fallback.
    """
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        # Support providers that supply the older postgres:// scheme.
        if database_url.startswith("postgres://"):
            database_url = database_url.replace(
                "postgres://", "postgresql+psycopg://", 1
            )
        elif database_url.startswith("postgresql://"):
            database_url = database_url.replace(
                "postgresql://", "postgresql+psycopg://", 1
            )
        return database_url

    db_path = Path(
        os.getenv("MEDGUARD_DB_PATH", str(BASE_DIR / "medguard.sqlite3"))
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return f"sqlite:///{db_path.as_posix()}"


def create_database_engine() -> Engine:
    """Create the SQLAlchemy engine for the configured database."""
    database_url = get_database_url()

    if database_url.startswith("sqlite:"):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False, "timeout": 8},
        )

    return create_engine(
        database_url,
        pool_pre_ping=True,
    )
