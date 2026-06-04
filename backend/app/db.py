from collections.abc import Generator
import ssl

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()


def normalize_database_url(url: str, driver: str | None = None) -> str:
    driver = (driver or settings.database_driver or "psycopg").strip().lower()
    if driver not in {"psycopg", "pg8000"}:
        driver = "psycopg"
    driver_prefix = f"postgresql+{driver}://"
    if url.startswith("postgresql+psycopg2://"):
        return url.replace("postgresql+psycopg2://", driver_prefix, 1)
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", driver_prefix, 1)
    if url.startswith("postgresql+pg8000://"):
        return url.replace("postgresql+pg8000://", driver_prefix, 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", driver_prefix, 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", driver_prefix, 1)
    return url


def database_url_and_connect_args() -> tuple[str, dict]:
    url = normalize_database_url(settings.database_url)
    connect_args: dict = {}
    if url.startswith("postgresql+pg8000://"):
        url_obj = make_url(url)
        query = {key: value for key, value in url_obj.query.items()}
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)
        if sslmode in {"require", "verify-ca", "verify-full"}:
            connect_args["ssl_context"] = ssl.create_default_context()
        url = str(url_obj.set(query=query))
    return url, connect_args


database_url, connect_args = database_url_and_connect_args()
engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from backend.app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
