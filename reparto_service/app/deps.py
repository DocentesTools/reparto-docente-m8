"""Re-export public dependencies consumed by route modules and tests."""

from reparto_service.core.deps import (
    CurrentReader,
    CurrentUser,
    SessionDep,
    get_current_user,
    get_db,
    require_reader,
)

__all__ = [
    "CurrentReader",
    "CurrentUser",
    "SessionDep",
    "get_current_user",
    "get_db",
    "require_reader",
]
