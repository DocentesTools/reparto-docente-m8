"""Autogenerate filters shared by the Alembic environment and its gate test.

**This module holds no migration content.** It contains no revision, no schema
operation and no DDL — only the ``include_object`` predicate Alembic consults
while *comparing* the declared models against a live database. Revisions
themselves are never hand-authored in this repository: the Compose bootstrap
runs ``alembic check`` and, on drift, ``alembic revision --autogenerate``
against ``SQLModel.metadata`` before applying the result
(``docs/ARCHITECTURE.md`` §3.1), and the generated files live outside the
repository under ``shared_migrations/``.

Every filter that shapes that comparison belongs to the service rather than to
the environment script, so ``tests/test_schema_migration_gate`` can assert the
*same* comparison the bootstrap performs rather than an approximation of it.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import Enum as SAEnum
from sqlalchemy import MetaData

#: Signature Alembic calls an ``include_object`` filter with.
IncludeObject = Callable[[object, str, str, bool, object], bool]


def type_bound_check_constraint_names(metadata: MetaData) -> frozenset[str]:
    """Return the names of the ``CHECK`` constraints bound to a column *type*.

    ``enum_column_type`` declares every enum column as
    ``Enum(..., native_enum=False, create_constraint=True)``, and SQLAlchemy
    attaches the resulting ``CHECK`` to the table as a *type-bound* constraint
    named after the enum class (``academicyearstatus``, ``assignmentstatus``,
    …). It is emitted with ``CREATE TABLE`` and reflected back like any other
    named check constraint.

    Alembic's check-constraint comparison, however, reads the metadata side
    through ``all_table_check_constraints``, which excludes exactly the
    type-bound ones, while the database side reflects all of them. Every enum
    ``CHECK`` therefore compares as *removed*, which is why the names collected
    here are excluded from the comparison — see :func:`make_include_object`.
    """
    names: set[str] = set()
    for table in metadata.tables.values():
        for column in table.columns:
            column_type = column.type
            if not isinstance(column_type, SAEnum):
                continue
            if column_type.native_enum or not column_type.create_constraint:
                continue
            if column_type.name:
                names.add(str(column_type.name))
    return frozenset(names)


def make_include_object(version_table: str, metadata: MetaData) -> IncludeObject:
    """Build the ``include_object`` filter used for autogenerate comparisons.

    Two objects are withheld from the comparison:

    * reflected tables other than *version_table* — the database may host
      several services' schemas (the Compose stacks share one PostgreSQL
      instance), and only this service's declared tables are ours to diff;
    * reflected type-bound enum ``CHECK`` constraints that the metadata still
      declares. Without this, a second Compose initialization autogenerates a
      revision dropping every enum ``CHECK`` and applies it, silently removing
      the database-level validation plan §16 requires — the schema stays
      readable and the service starts, so nothing else reports the loss.

    The exclusion is derived from the *current* metadata, so it is not a
    blanket suppression: drop an enum column from the models and its name
    leaves the set, letting the stale constraint be dropped as it should be.
    """
    type_bound_checks = type_bound_check_constraint_names(metadata)

    def include_object(
        object: object,  # Alembic's documented parameter name
        name: str,
        type_: str,
        reflected: bool,
        compare_to: object,
    ) -> bool:
        """Filter database objects included in migrations."""
        if type_ == "table":
            if name == version_table:
                return True
            return not reflected

        if type_ == "check_constraint" and reflected:
            return name not in type_bound_checks

        return True

    return include_object
