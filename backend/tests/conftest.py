import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://agency:agency@localhost:5432/agency_test")
os.environ["LLM_PROVIDER"] = "mock"
os.environ["SECRET_KEY"] = "test-secret-key-for-tests-only-0123456789"

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.db.session import reset_engine, scoped_session
from app.llm.gateway import set_provider

KEEP = {"alembic_version", "services", "audit_log"}


@pytest.fixture(scope="session", autouse=True)
def database():
    get_settings.cache_clear()
    url = get_settings().database_url
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    command.upgrade(cfg, "head")
    reset_engine()
    yield


@pytest.fixture(autouse=True)
def clean_tables(database):
    set_provider(None)
    with scoped_session(owner=True) as s:
        tables = s.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars().all()
        names = ", ".join(t for t in tables if t not in KEEP)
        s.execute(text(f"TRUNCATE {names} CASCADE"))
    yield
    set_provider(None)


@pytest.fixture
def manager():
    from app.agents.registry import ensure_manager

    with scoped_session(owner=True) as s:
        m = ensure_manager(s)
        return m.id
