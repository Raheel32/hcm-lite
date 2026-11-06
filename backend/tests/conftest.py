import os

# Force tests to use a throwaway database, never Neon. This must come BEFORE importing app code.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "test-secret-key"

os.environ["AI_API_KEY"] = ""   # tests must never call a real AI provider
os.environ["AI_MODEL"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.database import Base, get_db
import app.models  # noqa: F401
from app.main import app
from app.models.user import User, Role


@pytest.fixture()
def db_session():
    """A fresh empty in-memory database for every single test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """The real app, but wired to the temporary database."""

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture()
def make_user(db_session):
    """Call make_user(role=Role.hr, ...) inside a test to insert a user."""

    def _make(
        email="user@test.com",
        password="Password123",
        role=Role.employee,
        is_active=True,
        must_change_password=False,
    ):
        user = User(
            email=email,
            password_hash=hash_password(password),
            role=role,
            is_active=is_active,
            must_change_password=must_change_password,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _make