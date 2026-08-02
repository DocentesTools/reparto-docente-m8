"""Build-once site for auth and database dependencies.

Import ``auth``, ``engine``, ``CurrentUser``, and ``SessionDep`` from here.
Never call ``build_auth_deps`` or ``create_db_engine`` a second time.

Authorization floor (plan §21.1/§21.4)
--------------------------------------
``require_reader`` is the minimum-role dependency every domain router mounts,
so no route can rely on bare authentication. It is the SDK-built
``get_current_active_reader``, not a local role comparison: only the SDK path
re-validates on the fresh, no-positive-cache user, which is what makes a
role-sensitive check observe a revocation committed after the last cache fill.
"""

from functools import partial
from typing import Annotated

from auth_sdk_m8.schemas.user import UserModel
from fastapi import Depends, Request
from fastapi_m8 import AuthDeps, DbEngine, build_auth_deps, create_db_engine
from sqlmodel import Session

from reparto_service.services.user_directory import (
    IssuerUserDirectory,
    UserRoleLookup,
)

from .config import settings

# Single instances shared across the entire process.
auth: AuthDeps = build_auth_deps(settings)
engine: DbEngine = create_db_engine(settings)

CurrentUser = auth.CurrentUser
get_current_user = auth.get_current_user

#: Minimum-role gate mounted on every domain router (plan §21.1): ``USER`` and
#: unauthenticated callers never reach a route function, including reads.
require_reader = auth.get_current_active_reader
CurrentReader = Annotated[UserModel, Depends(require_reader)]

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
