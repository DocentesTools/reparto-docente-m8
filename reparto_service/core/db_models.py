"""Database model helpers shared across all reparto domain models.

The ``UUIDString`` TypeDecorator is required for SQLite compatibility
(unit tests): the in-memory SQLite engine used by the test suite does not
auto-coerce ``uuid.UUID`` parameters into the ``VARCHAR`` slot, so plain
``Column(CHAR(36))`` would raise on bind. The decorator normalises the
value in both directions. Postgres, MySQL and MariaDB all accept the
``CHAR(36)`` payload natively.
"""

import uuid as _uuid

from sqlalchemy import CHAR, TypeDecorator

from reparto_service.core.config import settings


def prefixed_tables(name: str) -> str:
    """Return a table name prefixed with the configured TABLES_PREFIX."""
    return f"{settings.TABLES_PREFIX}_{name}"


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
