"""Controller-level helpers shared by every reparto domain resource."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.controllers.base import BaseController
from auth_sdk_m8.schemas.user import UserModel
from sqlmodel import SQLModel

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.departments import Department
from reparto_service.enums import AssignmentProcessStatus

# Child resources cannot be mutated when the parent process is in one of
# these statuses. ``final`` is locked by plan §8.4; ``archived`` is the
# terminal status and the lifecycle service refuses any edge out of it.
_IMMUTABLE_PROCESS_STATUSES: frozenset[AssignmentProcessStatus] = frozenset(
    {
        AssignmentProcessStatus.FINAL,
        AssignmentProcessStatus.ARCHIVED,
    }
)
_MUTATION_ROLES: frozenset[str] = frozenset(
    {
        "superadmin",
        "admin",
        "writer",
    }
)


class DomainController(BaseController):
    """Common domain helpers layered on top of ``auth_sdk_m8``'s ``BaseController``.

    Provides:

    * a "must mutate" permission helper (superuser or above the reader role),
    * lookup-or-404 helpers for every owned parent (process, teacher profile, etc.),
    * a ``ensure_process_mutable`` guard that every child resource
      controller calls before a write, enforcing plan §8.4's
      "final process is immutable" rule.
    """

    @staticmethod
    def require_writer(current_user: UserModel) -> None:
        """Raise 403 unless the caller may mutate the domain.

        Writer role or superuser are accepted. ``reader`` and ``user`` roles
        can still call GET endpoints — they are blocked only on POST/PATCH/DELETE.
        """
        if current_user.is_superuser:
            return
        role = current_user.role
        role_value = role.value if hasattr(role, "value") else str(role)
        if role_value in _MUTATION_ROLES:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Department-head role required to mutate this resource.",
        )

    @staticmethod
    def get_or_404(session: Session, model: type[SQLModel], item_id: uuid.UUID) -> Any:
        """Return the row with ``item_id`` or raise a 404."""
        item = session.get(model, item_id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{model.__name__} {item_id} not found.",
            )
        return item

    @staticmethod
    def require_process_writer(
        session: Session, current_user: UserModel, process_id: uuid.UUID
    ) -> None:
        """Raise 403 unless the caller can mutate this process.

        Platform writer/admin roles keep broad setup access. A regular auth
        user can also mutate the process when the process department explicitly
        binds them as ``department_head_user_id``.
        """
        try:
            DomainController.require_writer(current_user)
            return
        except HTTPException:
            pass
        process = DomainController.get_process_or_404(session, process_id)
        department = session.get(Department, process.department_id)
        if department is not None and department.department_head_user_id == uuid.UUID(
            str(current_user.id)
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Department-head role required to mutate this process.",
        )

    @staticmethod
    def get_process_or_404(
        session: Session, process_id: uuid.UUID
    ) -> AssignmentProcess:
        """Return the process with ``process_id`` or raise a 404."""
        statement = select(AssignmentProcess).where(AssignmentProcess.id == process_id)
        process = session.exec(statement).first()
        if process is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"AssignmentProcess {process_id} not found.",
            )
        return process

    @staticmethod
    def ensure_process_mutable(process: AssignmentProcess) -> AssignmentProcess:
        """Raise 400 when the process is in a non-mutable status.

        The check is centralised here so every child resource controller
        enforces plan §8.4's immutability rule with one rule of thumb.
        """
        if process.status in _IMMUTABLE_PROCESS_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cannot mutate a process in status {process.status.value}; "
                    "reopen it first."
                ),
            )
        return process
