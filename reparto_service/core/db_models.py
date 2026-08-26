"""Database model helpers shared across all reparto domain models.

The ``UUIDString`` TypeDecorator is required for SQLite compatibility
(unit tests): the in-memory SQLite engine used by the test suite does not
auto-coerce ``uuid.UUID`` parameters into the ``VARCHAR`` slot, so plain
``Column(CHAR(36))`` would raise on bind. The decorator normalises the
value in both directions. Postgres, MySQL and MariaDB all accept the
``CHAR(36)`` payload natively.

``enum_column_type`` is the mandatory declaration for every enum-backed
column, so the schema Alembic generates from these models stays free of
native PostgreSQL ``ENUM`` types (plan §16).
"""

import uuid as _uuid
from enum import Enum

from sqlalchemy import CHAR, TypeDecorator
from sqlalchemy import Enum as SAEnum

from reparto_service.core.config import settings


def prefixed_tables(name: str) -> str:
    """Return a table name prefixed with the configured TABLES_PREFIX."""
    return f"{settings.TABLES_PREFIX}_{name}"


def enum_column_type(enum_cls: type[Enum]) -> SAEnum:
    """Return a string-backed column type for ``enum_cls``.

    SQLModel maps a bare ``(str, Enum)`` annotation to
    ``sqlalchemy.Enum(..., native_enum=True)``, which on PostgreSQL becomes
    a first-class ``ENUM`` type. Two consequences make that unusable here
    (plan §16):

    * every added or renamed member needs an ``ALTER TYPE`` migration
      instead of a plain column change;
    * the type is schema-level, not table-level, so it survives
      ``DROP TABLE`` and leaks into the destructive development reset
      documented in ``docs/ARCHITECTURE.md`` §3.2.

    ``native_enum=False`` stores the member name in a ``VARCHAR`` sized to
    the longest name, and ``create_constraint=True`` keeps the database-level
    validation as a portable ``CHECK`` constraint. The persisted token is the
    member *name* in both cases, so this is a pure schema change: the values
    already written by the test engine and by the partial-index predicates
    (``status = 'ACTIVE'``) are unchanged.

    Call it once per column — the returned instance carries the ``CHECK``
    constraint that gets attached to the declaring table.
    """
    return SAEnum(enum_cls, native_enum=False, create_constraint=True)


class UUIDString(TypeDecorator):
    """CHAR(36) column that accepts ``uuid.UUID`` on bind and returns it on load."""

    impl = CHAR(36)
    cache_ok = True

    def process_bind_param(
        self, value: _uuid.UUID | str | None, dialect: object
    ) -> str | None:
        if value is None:
            return None
        return str(value)

    def process_result_value(
        self, value: str | None, dialect: object
    ) -> _uuid.UUID | None:
        if value is None:
            return None
        return _uuid.UUID(value)
