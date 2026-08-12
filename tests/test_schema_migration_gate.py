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

import configparser
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.schema import CreateTable
from sqlmodel import Session, SQLModel

from reparto_service.core.db_models import prefixed_tables
from reparto_service.core.migrations import (
    make_include_object,
    type_bound_check_constraint_names,
)
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

# Read rather than restated: the filter keyed on it is the bootstrap's own.
_ALEMBIC_INI = configparser.RawConfigParser()
_ALEMBIC_INI.read(
    Path(__file__).resolve().parents[1] / "reparto_service" / "alembic.ini"
)
VERSION_TABLE = _ALEMBIC_INI["alembic"]["version_table"]

#: Alembic gained the check-constraint comparison in 1.19; ``requirements_base``
#: still admits 1.18.5, where there is nothing for the filter to withhold.
COMPARES_CHECK_CONSTRAINTS = (
    importlib.util.find_spec("alembic.autogenerate.compare.check_constraints")
    is not None
)


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


def _bootstrap_diff(connection: sa.Connection, **opts: object) -> list:
    """Diff the metadata against *connection* the way the bootstrap does."""
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "target_metadata": METADATA, **opts},
    )
    return compare_metadata(context, METADATA)


def test_metadata_matches_a_database_created_from_it(engine: sa.Engine) -> None:
    """A from-scratch autogenerate is complete, so the next one is empty.

    The ``engine`` fixture runs ``create_all`` from the same metadata the
    Compose bootstrap autogenerates against, and the comparison is configured
    through the very filter ``reparto_service/alembic/env.py`` installs. An
    empty diff here is what makes the bootstrap's ``alembic check`` pass on the
    second run, i.e. what keeps Compose initialization idempotent (plan §16).
    """
    with engine.connect() as connection:
        assert (
            _bootstrap_diff(
                connection,
                include_object=make_include_object(VERSION_TABLE, METADATA),
            )
            == []
        )


@pytest.mark.skipif(
    not COMPARES_CHECK_CONSTRAINTS,
    reason="Alembic < 1.19 does not compare check constraints at all",
)
def test_enum_check_constraints_would_otherwise_be_autogenerated_away(
    engine: sa.Engine,
) -> None:
    """Guard the guard above: without the filter the diff is not empty.

    Alembic reads the metadata side of a check-constraint comparison through
    ``all_table_check_constraints``, which excludes type-bound constraints,
    while reflecting every named one from the database. Each enum ``CHECK``
    therefore compares as *removed*. Left unfiltered, the second Compose
    initialization generates a revision dropping all of them and applies it,
    so the database keeps the schema and silently loses the validation.

    This test fails if a future Alembic release fixes the comparison — at which
    point the filter is dead weight and should go, which is worth being told.
    """
    with engine.connect() as connection:
        dropped = {
            operation[1].name
            for diff in _bootstrap_diff(connection)
            for operation in (diff if isinstance(diff, list) else [diff])
            if operation[0] == "remove_constraint"
        }

    assert dropped == type_bound_check_constraint_names(METADATA)


def test_the_filter_names_every_declared_enum_check_constraint() -> None:
    """The exclusion set is derived, never a hand-kept list of names."""
    assert type_bound_check_constraint_names(METADATA) == {
        enum_type.name for _, _, enum_type in _enum_columns()
    }


def test_the_filter_collects_only_type_bound_enum_constraints() -> None:
    """Only a non-native, constraint-creating, named enum contributes a name.

    The declared metadata exercises one shape; the branches that reject the
    others are what stop the filter from silently swallowing an unrelated
    constraint, so they are asserted against metadata built for the purpose.
    """
    metadata = sa.MetaData()
    sa.Table(
        "probe",
        metadata,
        sa.Column("plain", sa.String(8)),
        sa.Column("native", sa.Enum("A", name="nativeprobe", native_enum=True)),
        sa.Column(
            "unconstrained",
            sa.Enum(
                "A",
                name="unconstrainedprobe",
                native_enum=False,
                create_constraint=False,
            ),
        ),
        sa.Column("anonymous", sa.Enum("A", native_enum=False, create_constraint=True)),
        sa.Column(
            "kept",
            sa.Enum("A", name="keptprobe", native_enum=False, create_constraint=True),
        ),
    )

    assert type_bound_check_constraint_names(metadata) == {"keptprobe"}


def test_the_filter_withholds_reflected_enum_checks_and_foreign_tables() -> None:
    """Exercise every decision the bootstrap's filter makes.

    A reflected enum ``CHECK`` is withheld because the metadata side cannot
    offer it; everything else — an unrecognised reflected constraint, the
    metadata side of the same comparison, this service's own tables and the
    version table — stays in, so the filter narrows the diff without blinding
    it.
    """
    include = make_include_object(VERSION_TABLE, METADATA)
    known = min(type_bound_check_constraint_names(METADATA))

    assert include(None, VERSION_TABLE, "table", True, None) is True
    assert include(None, "some_other_service_table", "table", True, None) is False
    assert include(None, prefixed_tables("subject"), "table", False, None) is True
    assert include(None, known, "check_constraint", True, None) is False
    assert include(None, "ck_hand_written", "check_constraint", True, None) is True
    assert include(None, known, "check_constraint", False, None) is True
    assert include(None, "status", "column", True, None) is True


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


def test_assignment_activity_denormalization_has_its_composite_fk_target() -> None:
    """Plan §20.9 is a DB guarantee, not a controller-only convention."""

    assignment = METADATA.tables[prefixed_tables("assignment")]
    requirement = METADATA.tables[prefixed_tables("hour_requirement")]
    foreign_key = next(
        constraint
        for constraint in assignment.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
        and constraint.name == "fk_reparto_assignment_requirement_activity"
    )
    assert [element.parent.name for element in foreign_key.elements] == [
        "hour_requirement_id",
        "teaching_activity_id",
    ]
    assert [element.column.name for element in foreign_key.elements] == [
        "id",
        "teaching_activity_id",
    ]
    assert any(
        isinstance(constraint, sa.UniqueConstraint)
        and constraint.name == "uq_reparto_hour_requirement_id_activity"
        and [column.name for column in constraint.columns]
        == ["id", "teaching_activity_id"]
        for constraint in requirement.constraints
    )
