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

import inspect
import os
import uuid
from collections.abc import Generator
from typing import Any
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
import auth_sdk_m8.utils.paths as _paths_mod

_real_find_dotenv = _paths_mod.find_dotenv
_paths_mod.find_dotenv = lambda *_a, **_kw: ""

# Now safe to import the service. These imports are deliberately below the
# env setup above and not at the top of the file; ``E402`` is not enabled in
# this repository's ruff configuration, so they carry no suppression.
import pytest
from auth_sdk_m8.schemas.user import UserModel
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

# Pull every domain model so SQLModel.metadata is populated.
import reparto_service.db_models  # noqa: F401
from reparto_service.core.deps import auth, get_current_user, get_db
from reparto_service.main import app

# Restore find_dotenv (good hygiene).
_paths_mod.find_dotenv = _real_find_dotenv


def _fresh_user_dependency() -> Any:
    """Return the SDK's shared no-positive-cache user dependency.

    Every ``require_role`` guard (``get_current_active_reader``/``_writer``/
    ``_admin``/``_superuser``) authenticates through this one closure and then
    applies ``has_minimum_role`` itself. Overriding *it* — instead of
    overriding each role guard with a pass-through — is what lets the tests
    inject a role while the SDK's real hierarchy check still runs, so a test
    asserting 403 for a ``READER`` is exercising the shipped comparison rather
    than a stub of it.

    Raises:
        RuntimeError: if the SDK's guard no longer takes its user from a single
            sub-dependency. Failing here is deliberate: silently not overriding
            would turn every authorized-route test into a 401 mystery.
    """
    reader_guard = auth.get_current_active_reader
    depends = [
        parameter.default
        for parameter in inspect.signature(reader_guard).parameters.values()
        if hasattr(parameter.default, "dependency")
    ]
    if len(depends) != 1 or depends[0].dependency is None:
        raise RuntimeError(
            "fastapi-m8 role guards no longer resolve their user through a "
            "single sub-dependency; update the test auth override."
        )
    return depends[0].dependency


_FRESH_USER_DEPENDENCY = _fresh_user_dependency()


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


def make_user(role: str = "writer", *, user_id: uuid.UUID | None = None) -> UserModel:
    """Build a canonical ``UserModel`` for *role*.

    ``is_superuser`` is derived, never passed: the SDK enforces the truth table
    (``SUPERADMIN`` ⇔ ``is_superuser``) at construction, so an inconsistent pair
    cannot be built here by accident.
    """
    uid = user_id or uuid.uuid4()
    return UserModel(
        id=str(uid),
        email="test@example.com",
        is_active=True,
        is_superuser=role == "superadmin",
        role=role,
    )


def _make_user(
    *, is_superuser: bool = False, user_id: uuid.UUID | None = None
) -> UserModel:
    return make_user("superadmin" if is_superuser else "writer", user_id=user_id)


@pytest.fixture
def current_user() -> UserModel:
    """The acting department head (plan §21.2: ``ADMIN``).

    Most suites drive the API as the person running the reparto, so this is the
    default identity behind ``client``. It became ``ADMIN`` when §21.2 made
    department-head authorization a role rather than a
    ``department_head_user_id`` binding; ``writer_user`` covers the
    below-the-bar cases.
    """
    return make_user("admin")


@pytest.fixture
def writer_user() -> UserModel:
    """``WRITER``-role identity — own records only (§21.3)."""
    return make_user("writer")


@pytest.fixture
def superuser() -> UserModel:
    """Superuser authenticated user."""
    return make_user("superadmin")


@pytest.fixture
def admin_user() -> UserModel:
    """Authenticated user with the existing admin role."""
    return make_user("admin")


@pytest.fixture
def reader() -> UserModel:
    """Reader-role user (read-only access)."""
    return make_user("reader")


@pytest.fixture
def plain_user() -> UserModel:
    """``USER``-role identity — authenticated, but with no capability here."""
    return make_user("user")


# ── TestClient fixtures ──────────────────────────────────────────────────────


#: Identities registered by the client fixtures, keyed by the opaque header
#: each client sends. ``app.dependency_overrides`` is global to the app, so a
#: test asking for two clients would otherwise get whichever was built last for
#: both of them — a silent way to assert the wrong thing.
_TEST_IDENTITIES: dict[str, UserModel] = {}
_IDENTITY_HEADER = "x-test-identity"


def _make_client(session: Session, user: UserModel | None) -> TestClient:
    def _override_db():
        yield session

    def _override_user(request: Request) -> UserModel:
        identity = _TEST_IDENTITIES.get(request.headers.get(_IDENTITY_HEADER, ""))
        if identity is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return identity

    app.dependency_overrides[get_db] = _override_db
    if user is None:
        return TestClient(app)

    key = str(uuid.uuid4())
    _TEST_IDENTITIES[key] = user
    app.dependency_overrides[get_current_user] = _override_user
    # The §21.1 reader floor and every role guard resolve their user through
    # the SDK's fresh path, not through ``get_current_user``. Both are
    # overridden so a test client is one identity everywhere.
    app.dependency_overrides[_FRESH_USER_DEPENDENCY] = _override_user
    return TestClient(app, headers={_IDENTITY_HEADER: key})


@pytest.fixture
def client(
    session: Session,
    current_user: UserModel,
) -> Generator[TestClient]:
    """TestClient authenticated as the acting department head (``ADMIN``)."""
    tc = _make_client(session, current_user)
    with tc as c:
        yield c
    app.dependency_overrides.clear()
    _TEST_IDENTITIES.clear()


@pytest.fixture
def writer_client(
    session: Session,
    writer_user: UserModel,
) -> Generator[TestClient]:
    """TestClient authenticated as a ``WRITER`` (own records only)."""
    tc = _make_client(session, writer_user)
    with tc as c:
        yield c
    app.dependency_overrides.clear()
    _TEST_IDENTITIES.clear()


@pytest.fixture
def superuser_client(
    session: Session,
    superuser: UserModel,
) -> Generator[TestClient]:
    """TestClient authenticated as a superuser."""
    tc = _make_client(session, superuser)
    with tc as c:
        yield c
    app.dependency_overrides.clear()
    _TEST_IDENTITIES.clear()


@pytest.fixture
def admin_client(
    session: Session,
    admin_user: UserModel,
) -> Generator[TestClient]:
    """TestClient authenticated as an administrator."""
    tc = _make_client(session, admin_user)
    with tc as c:
        yield c
    app.dependency_overrides.clear()
    _TEST_IDENTITIES.clear()


@pytest.fixture
def reader_client(
    session: Session,
    reader: UserModel,
) -> Generator[TestClient]:
    """TestClient authenticated as a reader (read-only) user."""
    tc = _make_client(session, reader)
    with tc as c:
        yield c
    app.dependency_overrides.clear()
    _TEST_IDENTITIES.clear()


@pytest.fixture
def user_client(
    session: Session,
    plain_user: UserModel,
) -> Generator[TestClient]:
    """TestClient authenticated as a ``USER``-role identity (no capability)."""
    tc = _make_client(session, plain_user)
    with tc as c:
        yield c
    app.dependency_overrides.clear()
    _TEST_IDENTITIES.clear()


@pytest.fixture
def unauth_client(session: Session) -> Generator[TestClient]:
    """TestClient with no auth override: the real bearer-token dependency runs
    with no ``Authorization`` header, so every request is answered 401."""
    tc = _make_client(session, None)
    with tc as c:
        yield c
    app.dependency_overrides.clear()
    _TEST_IDENTITIES.clear()


# ── Convenience mock ────────────────────────────────────────────────────────


@pytest.fixture
def mock_object() -> MagicMock:
    """Plain MagicMock for ad-hoc test double injection."""
    return MagicMock()


@pytest.fixture
def cached_path_only_client(
    session: Session,
    current_user: UserModel,
) -> Generator[TestClient]:
    """A client whose identity resolves *only* through ``get_current_user``.

    The SDK's fresh, no-positive-cache dependency is deliberately left
    un-overridden, so anything that authenticates through it falls back to the
    real bearer-token flow and answers 401. That asymmetry is what lets a test
    prove no route takes its principal from the cacheable path (`RBAC-03`).
    """

    def _override_db():
        yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: current_user
    tc = TestClient(app)
    with tc as c:
        yield c
    app.dependency_overrides.clear()
    _TEST_IDENTITIES.clear()


def identity_client(session: Session, user: UserModel) -> TestClient:
    """Build an extra client for an ad-hoc identity inside a test.

    The fixtures cover the five canonical roles; this exists for the cases that
    need a *second* account of a role already in play — proving that "writer"
    and "this writer's own record" are different claims.
    """
    return _make_client(session, user)
