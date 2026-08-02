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

from typing import Annotated

from auth_sdk_m8.schemas.user import UserModel
from fastapi import Depends
from fastapi_m8 import AuthDeps, DbEngine, build_auth_deps, create_db_engine
from sqlmodel import Session

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
