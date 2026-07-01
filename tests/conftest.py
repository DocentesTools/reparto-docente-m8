"""Shared pytest fixtures for reparto_service tests.

The order of setup matters:

1.  Required env vars are set BEFORE any ``reparto_service`` import —
    Pydantic settings reads them at import time and the auth-sdk-m8
    strict-mode defaults reject missing/weak secret keys.
2.  ``auth_sdk_m8.utils.paths.find_dotenv`` is monkey-patched to return
    an empty string so the local ``.example_env`` (and any local
    ``.env``) is not loaded — tests must be reproducible regardless of
    the developer's environment.
3.  Every domain model is imported so ``SQLModel.metadata`` is
    populated before the test engine calls ``create_all``.
"""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

_TEST_ENV: dict[str, str] = {
    "DOMAIN": "localhost",
    "ENVIRONMENT": "local",
    "PROJECT_NAME": "M8RepartoTest",
    "STACK_NAME": "m8-reparto-test",
    "API_PREFIX": "/reparto",
    "AUTH_PREFIX": "/user",
    "BACKEND_HOST": "http://localhost:9000",
    "FRONTEND_HOST": "http://localhost:5173",
    "BACKEND_CORS_ORIGINS": "http://localhost",
    "AUTH_SERVICE_ROLE": "consumer",
    "TOKEN_MODE": "stateless",
    # auth-sdk-m8 >= 1.0.0 is secure-by-default; the documented local
    # opt-outs keep unit tests bootable without cross-service binding.
    "TOKEN_STRICT_VALIDATION": "false",
    "EVENT_SIGNING_ENABLED": "false",
    "ACCESS_SECRET_KEY": "TestSecret!Key4UnitTests_onlyXYZ0987",
    "REFRESH_SECRET_KEY": "TestRefresh!Key4UnitTests_onlyABC1234",
    "ACCESS_TOKEN_ALGORITHM": "HS256",
    "REFRESH_TOKEN_ALGORITHM": "HS256",
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "5432",
    "DB_DATABASE": "test_db",
    "DB_USER": "test",
    "DB_PASSWORD": "TestDb!Pass1secure",
    "SELECTED_DB": "Postgres",
    "TABLES_PREFIX": "reparto",
    "METRICS_ENABLED": "false",
}
for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)

# Disable the local .env lookup BEFORE the first service import.
import auth_sdk_m8.utils.paths as _paths_mod  # noqa: E402

_real_find_dotenv = _paths_mod.find_dotenv
_paths_mod.find_dotenv = lambda *_a, **_kw: ""

# Now safe to import the service.
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from sqlmodel.pool import StaticPool  # noqa: E402

from auth_sdk_m8.schemas.user import UserModel  # noqa: E402

# Pull every domain model so SQLModel.metadata is populated.
import reparto_service.db_models  # noqa: F401, E402

from reparto_service.core.deps import get_current_user, get_db  # noqa: E402
from reparto_service.main import app  # noqa: E402

# Restore find_dotenv (good hygiene).
_paths_mod.find_dotenv = _real_find_dotenv


# ── anyio backend — restrict to asyncio (trio not installed) ──────────────────


@pytest.fixture(params=["asyncio"])
def anyio_backend() -> str:
    """Run anyio-marked tests only on asyncio (trio is not installed)."""
    return "asyncio"


# ── Database fixtures ────────────────────────────────────────────────────────


@pytest.fixture(name="engine")
def engine_fixture():
    """Fresh in-memory SQLite engine per test (prevents cross-test pollution)."""
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(_engine)
    yield _engine
    SQLModel.metadata.drop_all(_engine)


@pytest.fixture(name="session")
def session_fixture(engine):
    """Database session backed by the per-test in-memory SQLite engine."""
    with Session(engine) as _session:
        yield _session


# ── User fixtures ────────────────────────────────────────────────────────────


def _make_user(
    *, is_superuser: bool = False, user_id: uuid.UUID | None = None
) -> UserModel:
    uid = user_id or uuid.uuid4()
    return UserModel(
        id=str(uid),
        email="test@example.com",
        is_active=True,
        is_superuser=is_superuser,
        role="superadmin" if is_superuser else "writer",
    )


@pytest.fixture
def current_user() -> UserModel:
    """Regular (writer role) authenticated user."""
    return _make_user()


@pytest.fixture
def superuser() -> UserModel:
    """Superuser authenticated user."""
    return _make_user(is_superuser=True)


@pytest.fixture
def reader() -> UserModel:
    """Reader-role user (read-only access)."""

    user = _make_user()
    user.role = "reader"  # type: ignore[assignment]
    return user


# ── TestClient fixtures ──────────────────────────────────────────────────────


def _make_client(session: Session, user: UserModel | None) -> TestClient:
    def _override_db():
        yield session

    def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    if user is not None:
        app.dependency_overrides[get_current_user] = _override_user
    return TestClient(app)


@pytest.fixture
def client(
    session: Session,
    current_user: UserModel,
) -> TestClient:
    """TestClient authenticated as a regular writer user."""
    tc = _make_client(session, current_user)
    with tc as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def superuser_client(
    session: Session,
    superuser: UserModel,
) -> TestClient:
    """TestClient authenticated as a superuser."""
    tc = _make_client(session, superuser)
    with tc as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def reader_client(
    session: Session,
    reader: UserModel,
) -> TestClient:
    """TestClient authenticated as a reader (read-only) user."""
    tc = _make_client(session, reader)
    with tc as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def unauth_client(session: Session) -> TestClient:
    """TestClient with no auth override (the default test auth dep returns
    whatever the test client is configured with — but here we explicitly
    raise 401 to simulate unauthenticated requests)."""
    tc = _make_client(session, None)
    with tc as c:
        yield c
    app.dependency_overrides.clear()


# ── Convenience mock ────────────────────────────────────────────────────────


@pytest.fixture
def mock_object() -> MagicMock:
    """Plain MagicMock for ad-hoc test double injection."""
    return MagicMock()
