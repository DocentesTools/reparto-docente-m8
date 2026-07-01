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


class DomainController(BaseController):
    """Common domain helpers layered on top of ``auth_sdk_m8``'s ``BaseController``.

    Provides:

    * a "must mutate" permission helper (superuser or above the reader role),
    * lookup-or-404 helpers for every owned parent (process, teacher profile, etc.).
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
        if role_value in {"superadmin", "admin", "writer"}:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Writer role required to mutate this resource.",
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
