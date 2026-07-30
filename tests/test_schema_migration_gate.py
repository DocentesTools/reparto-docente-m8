"""Migration-gate guards for the schema Alembic generates from the models.

Migration files are never hand-authored here: the Compose bootstrap runs
``alembic revision --autogenerate`` against ``SQLModel.metadata`` and applies
the result (``docs/ARCHITECTURE.md`` §3.1). That makes the declared metadata
the only place a schema defect can be caught, so the plan §16 migration gate
is asserted here rather than reviewed by hand on every model change:

* no enum column may compile to a native PostgreSQL ``ENUM`` type;
* every enum column keeps its database-level ``CHECK`` validation;
* the metadata must describe a freshly created database exactly, so the
  generated revision is complete and a second bootstrap detects no drift;
* partial unique indexes must carry both dialect predicates, or the
  invariant they encode silently disappears on one of the two engines.

The equivalent checks against a real PostgreSQL server live in
``tests/live/test_schema_postgres.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.schema import CreateTable
from sqlmodel import Session, SQLModel

from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.enums import ProcessTeacherStatus
from tests.factories import (
    make_assignment_process,
    make_process_teacher,
    make_teacher_profile,
)

# Every table model is imported by ``tests.conftest``, so the metadata is
# already fully populated by the time this module runs.
METADATA = SQLModel.metadata

DIALECTS = ("postgresql", "sqlite", "mysql")


def _enum_columns() -> list[tuple[str, str, sa.Enum]]:
    """Return ``(table_name, column_name, enum_type)`` for every enum column."""
    return [
        (name, column.name, column.type)
        for name in sorted(METADATA.tables)
        for column in METADATA.tables[name].columns
        if isinstance(column.type, sa.Enum)
    ]


def test_the_metadata_declares_enum_columns() -> None:
    """Guard the guards: the checks below would pass vacuously on no columns."""
    assert len(_enum_columns()) == 22


def test_no_enum_column_uses_a_native_database_enum() -> None:
    """Plan §16: string-backed enums, never a native PostgreSQL ``ENUM``.

    A native enum is a schema-level object: it needs ``ALTER TYPE`` migrations
    for every added member and it survives ``DROP TABLE``, which breaks the
    destructive development reset (``docs/ARCHITECTURE.md`` §3.2).
    """
    offenders = [
        f"{table}.{column}"
        for table, column, enum_type in _enum_columns()
        if enum_type.native_enum
    ]
    assert offenders == [], (
        "declare these with reparto_service.core.db_models.enum_column_type: "
        f"{offenders}"
    )


def test_every_enum_column_keeps_its_check_constraint() -> None:
    """Dropping the native type must not drop the database-level validation."""
    offenders = [
        f"{table}.{column}"
        for table, column, enum_type in _enum_columns()
        if not enum_type.create_constraint
    ]
    assert offenders == []


def test_no_dialect_emits_a_create_type_statement() -> None:
    """The rendered DDL must be free of enum type objects on every dialect."""
    for dialect_name in DIALECTS:
        dialect = sa.create_mock_engine(f"{dialect_name}://", executor=None).dialect
        for name in sorted(METADATA.tables):
            ddl = str(CreateTable(METADATA.tables[name]).compile(dialect=dialect))
            assert "CREATE TYPE" not in ddl.upper(), f"{dialect_name}:{name}"


def test_enum_check_constraints_reach_the_rendered_ddl() -> None:
    """A declared ``CHECK`` is worthless if the dialect drops it."""
    enum_tables = {table for table, _, _ in _enum_columns()}
    for dialect_name in DIALECTS:
        dialect = sa.create_mock_engine(f"{dialect_name}://", executor=None).dialect
        for name in sorted(enum_tables):
            ddl = str(CreateTable(METADATA.tables[name]).compile(dialect=dialect))
            assert "CHECK" in ddl.upper(), f"{dialect_name}:{name}"


def test_enum_columns_persist_the_member_name(session: Session) -> None:
    """The stored token is the member *name*, not its value.

    The partial-index predicates are written against that token
    (``WHERE status = 'ACTIVE'`` for ``ProcessTeacherStatus.ACTIVE``, whose
    value is ``"active"``), so a switch to value-based persistence would
    silently disable those unique constraints.
    """
    process = make_assignment_process(session)
    profile = make_teacher_profile(session)
    teacher = make_process_teacher(
        session, process, profile, status=ProcessTeacherStatus.ACTIVE
    )
    assert ProcessTeacherStatus.ACTIVE.value == "active"

    # Cast to text so the read goes around the enum type's result processor,
    # which would map the stored token back to a ProcessTeacherStatus member.
    table = ProcessTeacher.__table__
    stored = (
        session.connection()
        .execute(
            sa.select(sa.cast(table.c.status, sa.String)).where(
                table.c.id == teacher.id
            )
        )
        .scalar_one()
    )
    assert stored == "ACTIVE"


def test_metadata_matches_a_database_created_from_it(engine: sa.Engine) -> None:
    """A from-scratch autogenerate is complete, so the next one is empty.

    The ``engine`` fixture runs ``create_all`` from the same metadata the
    Compose bootstrap autogenerates against. An empty diff here is what makes
    the bootstrap's ``alembic check`` pass on the second run, i.e. what keeps
    Compose initialization idempotent (plan §16).
    """
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "target_metadata": METADATA},
        )
        assert compare_metadata(context, METADATA) == []


def test_partial_unique_indexes_declare_both_dialect_predicates() -> None:
    """A one-dialect predicate enforces the invariant on one engine only.

    Postgres is the deployment target and SQLite runs the suite, so a partial
    unique index missing either ``postgresql_where`` or ``sqlite_where`` is
    either untested or unenforced.
    """
    partial: list[str] = []
    for name in sorted(METADATA.tables):
        for index in METADATA.tables[name].indexes:
            options = index.dialect_options
            postgres_where = options["postgresql"].get("where")
            sqlite_where = options["sqlite"].get("where")
            if postgres_where is None and sqlite_where is None:
                continue
            partial.append(index.name or "")
            assert index.unique, index.name
            assert str(postgres_where) == str(sqlite_where), index.name

    assert sorted(partial) == [
        "uq_reparto_assignment_active_activity_teacher",
        "uq_reparto_assignment_active_requirement",
        "uq_reparto_hour_requirement_active_slot",
        "uq_reparto_teaching_activity_main_source",
    ]
