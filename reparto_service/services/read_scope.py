"""Per-tenant read scoping (plan §21.4, closes `RBAC-02`).

The decision
------------
`READER` and `WRITER` see only the departments they belong to; `ADMIN` and
`SUPERADMIN` see the whole deployment.

The open question §21.4 recorded was *what* a tenant is here. This service has
no tenant column of its own, and the token's `tenant_id` is not populated in
this deployment, so inventing either would have meant inventing data. What the
domain already knows is **participation**: a `TeacherProfile` linked to an auth
user, joined to the `ProcessTeacher` rows that place that teacher in a process,
which belongs to exactly one department of exactly one school. Membership is
therefore derived, not stored — the scope of a teacher who joins a second
department widens the moment they are added to a process there, and narrows
again when that participation is removed, with no second place to keep in sync.

Membership is by **department**, not by process. A teacher who participates in
this year's process can read last year's process of the same department — which
is the whole point of the previous-year comparison — but cannot read another
department's, in this year or any other.

Consequences taken deliberately
-------------------------------
* A `READER`/`WRITER` with no linked teacher profile sees nothing: empty lists,
  and `404` on anything nested under a process. "Authenticated" has never meant
  "belongs here", and this is the case where the difference matters.
* An out-of-scope row is a `404`, not a `403`. A `403` would confirm the row
  exists, which is precisely what a caller outside the tenant must not learn.
* Academic years and classroom stages stay unscoped. They are a calendar and a
  grade vocabulary — deployment-wide reference data that names nothing about
  any one school's operations, and every scoped view needs them to render.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from fastapi_m8 import RoleType, UserModel, has_minimum_role
from sqlmodel import Session, col, select

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.departments import Department
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teacher_profiles import TeacherProfile

#: Returned instead of a set when the caller's reads are not restricted at all.
#: A distinct sentinel rather than "the set of every department id": an
#: unrestricted caller must not pay for a scan, and a query builder must not be
#: able to confuse "sees everything" with "happens to see everything today".
UNRESTRICTED = None


def is_unrestricted(current_user: UserModel) -> bool:
    """Return whether *current_user* reads the whole deployment (§21.4)."""
    return has_minimum_role(current_user.role, RoleType.ADMIN)


def visible_department_ids(
    session: Session, current_user: UserModel
) -> set[uuid.UUID] | None:
    """Return the departments *current_user* may read, or ``UNRESTRICTED``.

    Args:
        session: The active database session.
        current_user: The authenticated caller.

    Returns:
        ``None`` for a department head or platform administrator; otherwise the
        set of department ids the caller participates in, which may be empty.
    """
    if is_unrestricted(current_user):
        return UNRESTRICTED
    statement = (
        select(AssignmentProcess.department_id)
        .join(
            ProcessTeacher,
            col(ProcessTeacher.assignment_process_id) == col(AssignmentProcess.id),
        )
        .join(
            TeacherProfile,
            col(TeacherProfile.id) == col(ProcessTeacher.teacher_profile_id),
        )
        .where(TeacherProfile.user_id == uuid.UUID(str(current_user.id)))
    )
    return set(session.exec(statement).all())


def visible_school_ids(
    session: Session, current_user: UserModel
) -> set[uuid.UUID] | None:
    """Return the schools *current_user* may read, or ``UNRESTRICTED``."""
    departments = visible_department_ids(session, current_user)
    if departments is UNRESTRICTED:
        return UNRESTRICTED
    if not departments:
        return set()
    statement = select(Department.school_id).where(col(Department.id).in_(departments))
    return set(session.exec(statement).all())


def ensure_process_visible(
    session: Session, current_user: UserModel, process_id: uuid.UUID
) -> AssignmentProcess:
    """Return the process, or ``404`` when it is outside the caller's scope.

    The single choke point for every read nested under
    ``/assignment-processes/{process_id}/…``: mounted as a router dependency so
    a resource added later is scoped by construction rather than by memory.

    Raises:
        HTTPException: ``404`` when the process does not exist *or* lies
            outside the caller's departments — the two are deliberately
            indistinguishable to the caller.
    """
    process = session.get(AssignmentProcess, process_id)
    departments = visible_department_ids(session, current_user)
    if process is None or (
        departments is not UNRESTRICTED and process.department_id not in departments
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AssignmentProcess {process_id} not found.",
        )
    return process


__all__ = [
    "UNRESTRICTED",
    "ensure_process_visible",
    "is_unrestricted",
    "visible_department_ids",
    "visible_school_ids",
]
