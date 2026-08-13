"""Build-once site for auth and database dependencies.

Import ``auth``, ``engine``, the role vocabulary (``CurrentReader`` /
``CurrentWriter`` / ``CurrentAdmin`` and the ``require_*`` gates they wrap) and
``SessionDep`` from here. Never call ``build_auth_deps`` or ``create_db_engine``
a second time. There is deliberately no bare ``CurrentUser`` alias to import:
see the floor note below.

Authorization floor (plan §21.1/§21.4)
--------------------------------------
``require_reader`` is the minimum-role dependency every domain router mounts,
so no route can rely on bare authentication. It is the SDK-built
``get_current_active_reader``, not a local role comparison: only the SDK path
re-validates on the fresh, no-positive-cache user, which is what makes a
role-sensitive check observe a revocation committed after the last cache fill.
"""

import uuid
from functools import partial
from typing import Annotated

from auth_sdk_m8.schemas.user import UserModel
from fastapi import Depends, Request
from fastapi_m8 import AuthDeps, DbEngine, build_auth_deps, create_db_engine
from sqlmodel import Session

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.services.read_scope import ensure_process_visible
from reparto_service.services.user_directory import (
    IssuerUserDirectory,
    UserRoleLookup,
)

from .config import settings

# Single instances shared across the entire process.
auth: AuthDeps = build_auth_deps(settings)
engine: DbEngine = create_db_engine(settings)

# ``auth.CurrentUser`` is deliberately *not* re-exported under a local name: an
# alias here is an invitation to annotate a route with bare authentication, and
# the whole point of the vocabulary below is that a route has to name the role
# it needs. ``get_current_user`` stays because the test suite needs it as the
# ``dependency_overrides`` seam — it is not a supported route principal.
get_current_user = auth.get_current_user

# ── Role gates (plan §21.1/§21.6) ────────────────────────────────────────────
# All three are the SDK-built dependencies, never a local role comparison. They
# authenticate through fastapi-m8's fresh, no-positive-cache user path, so a
# role-sensitive decision always observes a revocation committed after the last
# cache fill — a demoted writer stops being a writer on their next request
# rather than at the end of the cache TTL. A hand-rolled check over
# ``get_current_user`` cannot offer that, which is the whole reason `RBAC-03`
# asked for this migration rather than leaving the equivalent local comparison
# in place.

#: Minimum-role gate mounted on every domain router (plan §21.1): ``USER`` and
#: unauthenticated callers never reach a route function, including reads.
require_reader = auth.get_current_active_reader
CurrentReader = Annotated[UserModel, Depends(require_reader)]

#: Own-data mutations (plan §21.3). Ownership is proven separately.
require_writer = auth.get_current_active_writer
CurrentWriter = Annotated[UserModel, Depends(require_writer)]

#: Department-head and platform-administration mutations (plan §21.2/§21.3).
require_admin = auth.get_current_active_admin
CurrentAdmin = Annotated[UserModel, Depends(require_admin)]

get_db = engine.session_dep
SessionDep = Annotated[Session, Depends(get_db)]

# Issuer user directory — the only way this consumer can learn what role a
# candidate ``department_head_user_id`` holds, since the user table belongs to
# the auth service and is never read from here (plan §21.2).
user_directory = IssuerUserDirectory.from_settings(settings)


def _bearer_token(request: Request) -> str:
    """Return the request's raw bearer token, or an empty string."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    return token if scheme.lower() == "bearer" else ""


def get_user_role_lookup(request: Request) -> UserRoleLookup:
    """Bind the issuer role lookup to this request's bearer token.

    The token is forwarded so the lookup is authorized as the caller, never as
    the service: nothing here can read a user the caller could not read.
    """
    return partial(user_directory.role_of, bearer_token=_bearer_token(request))


UserRoleLookupDep = Annotated[UserRoleLookup, Depends(get_user_role_lookup)]


def require_visible_process(
    session: SessionDep, current_user: CurrentReader, process_id: uuid.UUID
) -> AssignmentProcess:
    """Read-scope gate for every router nested under a process (plan §21.4).

    Mounted on the router rather than called per handler, for the same reason
    the reader floor is: a resource added later inherits the scope instead of
    depending on somebody remembering it.
    """
    return ensure_process_visible(session, current_user, process_id)
