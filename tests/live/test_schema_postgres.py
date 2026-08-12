"""Clean-PostgreSQL verification of the schema Alembic generates (plan §16).

``tests/test_schema_migration_gate.py`` asserts the same invariants against the
declared metadata and the SQLite test engine. This module proves them on the
real deployment target, which is the only place a native ``ENUM`` type, a
dropped ``CHECK`` constraint or a non-idempotent bootstrap actually shows up.

It is excluded from the default run by ``--ignore=tests/live`` in ``pytest.ini``;
the ``REPARTO_SCHEMA_CHECK_DSN`` guard below keeps it inert if it is collected
anyway. It carries no ``live`` marker, which would be redundant: when
``security-tests-m8`` is installed as a pytest plugin, its
``pytest_collection_modifyitems`` hook skips every item with ``live`` among its
keywords — including, via the package name, everything under ``tests/live/``.
That is why the run below disables that plugin.

Point ``REPARTO_SCHEMA_CHECK_DSN`` at a **disposable** database — these tests
drop and recreate the ``public`` schema::

    docker run -d --rm --name reparto-schema-check \\
        -e POSTGRES_USER=schemacheck \\
        -e POSTGRES_PASSWORD=<password> \\
        -e POSTGRES_DB=reparto_schema_check \\
        -p 55432:5432 postgres:18.4-alpine

    REPARTO_SCHEMA_CHECK_DSN=postgresql+psycopg2://schemacheck:<password>@127.0.0.1:55432/reparto_schema_check \\
        pytest tests/live/test_schema_postgres.py --no-cov -p no:security_tests_m8

Never point it at a database holding anything you want to keep.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlmodel import SQLModel

from reparto_service.core.migrations import (
    make_include_object,
    type_bound_check_constraint_names,
)

METADATA = SQLModel.metadata

#: The bootstrap's own version table, so the comparisons below are configured
#: exactly as ``reparto_service/alembic/env.py`` configures them.
VERSION_TABLE = "alembic_version_reparto"


def _bootstrap_diff(connection: sa.Connection, *, filtered: bool = True) -> list:
    """Diff the metadata against *connection* the way the bootstrap does."""
    opts: dict[str, object] = {
        "compare_type": True,
        "target_metadata": METADATA,
    }
    if filtered:
        opts["include_object"] = make_include_object(VERSION_TABLE, METADATA)
    return compare_metadata(MigrationContext.configure(connection, opts=opts), METADATA)


DSN = os.environ.get("REPARTO_SCHEMA_CHECK_DSN", "")

pytestmark = pytest.mark.skipif(
    not DSN, reason="set REPARTO_SCHEMA_CHECK_DSN to a disposable database"
)

NATIVE_ENUM_TYPES = sa.text(
    "SELECT typname FROM pg_type WHERE typtype = 'e' ORDER BY typname"
)
ENUM_CHECK_CONSTRAINTS = sa.text(
    "SELECT count(*) FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
    "WHERE c.contype = 'c' AND t.relname LIKE 'reparto\\_%'"
)
PUBLIC_TABLES = sa.text(
    "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
)


@pytest.fixture(name="engine")
def engine_fixture() -> Generator[sa.Engine]:
    """Engine over an emptied ``public`` schema, torn down after the test."""
    engine = sa.create_engine(DSN)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA public"))
    yield engine
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA public"))
    engine.dispose()


def test_clean_database_creation_matches_the_metadata(engine: sa.Engine) -> None:
    """Every declared table is created and leaves no residual difference.

    An empty diff is what the bootstrap's ``alembic check`` sees on its second
    run, so this is the "compose initialization succeeds twice" gate.
    """
    METADATA.create_all(engine)

    with engine.connect() as connection:
        tables = [row[0] for row in connection.execute(PUBLIC_TABLES)]
        assert _bootstrap_diff(connection) == []

    assert sorted(tables) == sorted(METADATA.tables)


def test_enum_check_constraints_survive_a_second_bootstrap(engine: sa.Engine) -> None:
    """The second Compose initialization must not drop the enum validation.

    Alembic 1.19 reads the metadata side of a check-constraint comparison
    through ``all_table_check_constraints``, which excludes type-bound
    constraints, while PostgreSQL reflects every one of them by name. Each
    enum ``CHECK`` therefore compares as *removed*: the unfiltered diff below
    is the revision the bootstrap generated and applied on the second
    ``docker compose up``, leaving 0 of the 22 constraints in place with the
    schema otherwise intact and the service healthy.

    Only PostgreSQL shows this — SQLite reflects the same constraints without
    names, so the SQLite gate passes either way (which is why it did).
    """
    METADATA.create_all(engine)

    with engine.connect() as connection:
        dropped = {
            operation[1].name
            for diff in _bootstrap_diff(connection, filtered=False)
            for operation in (diff if isinstance(diff, list) else [diff])
            if operation[0] == "remove_constraint"
        }
        assert _bootstrap_diff(connection) == []

    assert dropped == type_bound_check_constraint_names(METADATA)

    with engine.connect() as connection:
        assert connection.execute(ENUM_CHECK_CONSTRAINTS).scalar_one() == 22


def test_no_native_enum_type_is_created(engine: sa.Engine) -> None:
    """Plan §16: enum columns are ``VARCHAR`` + ``CHECK``, never ``CREATE TYPE``."""
    METADATA.create_all(engine)

    with engine.connect() as connection:
        native = [row[0] for row in connection.execute(NATIVE_ENUM_TYPES)]
        checks = connection.execute(ENUM_CHECK_CONSTRAINTS).scalar_one()

    assert native == []
    assert checks == 22


def test_the_destructive_reset_is_repeatable(engine: sa.Engine) -> None:
    """Dropping the tables must leave nothing behind for the next bootstrap.

    This is the concrete reason native enums are banned: a native type is
    schema-level, so it survives ``DROP TABLE`` and the next ``CREATE TYPE``
    fails with "type already exists" — exactly the destructive development
    reset documented in ``docs/ARCHITECTURE.md`` §3.2.
    """
    METADATA.create_all(engine)
    with engine.connect() as connection:
        tables = [row[0] for row in connection.execute(PUBLIC_TABLES)]

    with engine.begin() as connection:
        for table in tables:
            connection.execute(sa.text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))

    with engine.connect() as connection:
        assert [row[0] for row in connection.execute(PUBLIC_TABLES)] == []
        assert [row[0] for row in connection.execute(NATIVE_ENUM_TYPES)] == []

    METADATA.create_all(engine)

    with engine.connect() as connection:
        assert _bootstrap_diff(connection) == []
