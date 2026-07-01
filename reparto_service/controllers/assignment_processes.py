"""AssignmentProcess controller."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, func, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.db_models.assignment_processes import (
    AssignmentProcess,
    AssignmentProcessCreate,
    AssignmentProcessPublic,
    AssignmentProcessesPublic,
    AssignmentProcessUpdate,
)
from reparto_service.db_models.departments import Department
from reparto_service.db_models.schools import School
from reparto_service.enums import AssignmentProcessStatus


class AssignmentProcessController(DomainController):
    """CRUD logic for assignment processes."""

    @staticmethod
    def list_processes(
        session: Session,
        academic_year_id: uuid.UUID | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> AssignmentProcessesPublic:
        count_stmt = select(func.count()).select_from(AssignmentProcess)
        list_stmt = select(AssignmentProcess)
        if academic_year_id is not None:
            count_stmt = count_stmt.where(
                AssignmentProcess.academic_year_id == academic_year_id
            )
            list_stmt = list_stmt.where(
                AssignmentProcess.academic_year_id == academic_year_id
            )
        count = session.exec(count_stmt).one()
        items = list(session.exec(list_stmt.offset(skip).limit(limit)).all())
        return AssignmentProcessesPublic(
            data=[AssignmentProcessPublic.model_validate(item) for item in items],
            count=count,
        )

    @staticmethod
    def get_process(session: Session, process_id: uuid.UUID) -> AssignmentProcessPublic:
        process = DomainController.get_process_or_404(session, process_id)
        return AssignmentProcessPublic.model_validate(process)

    @staticmethod
    def create_process(
        session: Session,
        current_user: UserModel,
        process_in: AssignmentProcessCreate,
    ) -> AssignmentProcessPublic:
        # Validate parent references exist.
        DomainController.get_or_404(session, AcademicYear, process_in.academic_year_id)
        DomainController.get_or_404(session, School, process_in.school_id)
        DomainController.get_or_404(session, Department, process_in.department_id)
        process = AssignmentProcess.model_validate(
            process_in.model_dump(),
            update={"created_by_user_id": current_user.id},
        )
        session.add(process)
        session.commit()
        session.refresh(process)
        return AssignmentProcessPublic.model_validate(process)

    @staticmethod
    def update_process(
        session: Session,
        process_id: uuid.UUID,
        process_in: AssignmentProcessUpdate,
    ) -> AssignmentProcessPublic:
        process = DomainController.get_process_or_404(session, process_id)
        update_dict = process_in.model_dump(exclude_unset=True)
        if update_dict.get("status") == AssignmentProcessStatus.FINAL:
            if process.status == AssignmentProcessStatus.FINAL:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Process is already final.",
                )
            update_dict["closed_at"] = update_dict.get("closed_at")
        process.sqlmodel_update(update_dict)
        session.add(process)
        session.commit()
        session.refresh(process)
        return AssignmentProcessPublic.model_validate(process)


__all__ = ["AssignmentProcessController"]
