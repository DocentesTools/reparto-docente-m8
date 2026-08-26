"""Re-export public dependencies consumed by route modules and tests.

There is deliberately no bare ``CurrentUser`` here. Every domain route names the
role it needs — ``CurrentReader``, ``CurrentWriter`` or ``CurrentAdmin`` — so a
new route cannot accidentally settle for "authenticated" (plan §21.1), and every
principal is resolved through the SDK's fresh, no-positive-cache path (§21.6).
"""

from reparto_service.core.deps import (
    CurrentAdmin,
    CurrentReader,
    CurrentWriter,
    SessionDep,
    UserRoleLookupDep,
    get_current_user,
    get_db,
    require_admin,
    require_reader,
    require_visible_process,
    require_writer,
)

__all__ = [
    "CurrentAdmin",
    "CurrentReader",
    "CurrentWriter",
    "SessionDep",
    "UserRoleLookupDep",
    "get_current_user",
    "get_db",
    "require_admin",
    "require_reader",
    "require_visible_process",
    "require_writer",
]
