from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def build_connect_args(database_url: str, settings: Settings | None = None) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}

    if not database_url.startswith("mysql"):
        return {}

    active_settings = settings or get_settings()
    if not active_settings.aiven_ca_path:
        return {}

    ca_path = Path(active_settings.aiven_ca_path).expanduser()
    if not ca_path.is_absolute():
        ca_path = Path.cwd() / ca_path
    ca_path = ca_path.resolve()
    if not ca_path.is_file():
        raise FileNotFoundError(f"AIVEN_CA_PATH does not point to a file: {ca_path}")
    return {"ssl": {"ca": str(ca_path)}}


def normalize_database_url(database_url: str) -> str:
    if database_url.startswith("mysql://"):
        database_url = database_url.replace("mysql://", "mysql+pymysql://", 1)
    if database_url.startswith("mysql+pymysql://"):
        url = make_url(database_url)
        url = url.difference_update_query(["ssl-mode", "ssl-ca", "ssl-cert", "ssl-key"])
        return url.render_as_string(hide_password=False)
    return database_url


def make_engine(database_url: str | None = None):
    settings = get_settings()
    url = normalize_database_url(database_url or settings.database_url)
    connect_args = build_connect_args(url, settings)
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_all() -> None:
    Base.metadata.create_all(bind=engine)


def get_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
