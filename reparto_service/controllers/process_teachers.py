"""ProcessTeacher controller (teacher-per-process binding)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.process_teachers import (
    ProcessTeacher,
    ProcessTeacherCreate,
    ProcessTeacherPublic,
    ProcessTeachersPublic,
    ProcessTeacherUpdate,
)
from reparto_service.db_models.teacher_profiles import TeacherProfile


class ProcessTeacherController(DomainController):
    """CRUD logic for teachers bound to one assignment process."""

    @staticmethod
    def list_process_teachers(
        session: Session, process_id: uuid.UUID
    ) -> ProcessTeachersPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(ProcessTeacher).where(
            ProcessTeacher.assignment_process_id == process_id
        )
        items = list(session.exec(statement).all())
        return ProcessTeachersPublic(
            data=[ProcessTeacherPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_process_teacher(
        session: Session, process_id: uuid.UUID, process_teacher_id: uuid.UUID
    ) -> ProcessTeacherPublic:
        process_teacher = ProcessTeacherController._get_or_404(
            session, process_id, process_teacher_id
        )
        return ProcessTeacherPublic.model_validate(process_teacher)

    @staticmethod
    def create_process_teacher(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        process_teacher_in: ProcessTeacherCreate,
    ) -> ProcessTeacherPublic:
        DomainController.get_process_or_404(session, process_id)
        DomainController.get_or_404(
            session, TeacherProfile, process_teacher_in.teacher_profile_id
        )
        if process_teacher_in.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "assignment_process_id in the payload does not match the "
                    "URL process_id."
                ),
            )
        process_teacher = ProcessTeacher.model_validate(process_teacher_in.model_dump())
        session.add(process_teacher)
        try:
            session.commit()
        except Exception as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Could not create process teacher: a binding for this "
                    "teacher profile already exists in the process."
                ),
            ) from exc
        session.refresh(process_teacher)
        return ProcessTeacherPublic.model_validate(process_teacher)

    @staticmethod
    def update_process_teacher(
        session: Session,
        process_id: uuid.UUID,
        process_teacher_id: uuid.UUID,
        process_teacher_in: ProcessTeacherUpdate,
        current_user: UserModel,
    ) -> ProcessTeacherPublic:
        del current_user
        process_teacher = ProcessTeacherController._get_or_404(
            session, process_id, process_teacher_id
        )
        process_teacher.sqlmodel_update(
            process_teacher_in.model_dump(exclude_unset=True)
        )
        session.add(process_teacher)
        session.commit()
        session.refresh(process_teacher)
        return ProcessTeacherPublic.model_validate(process_teacher)

    @staticmethod
    def delete_process_teacher(
        session: Session, process_id: uuid.UUID, process_teacher_id: uuid.UUID
    ) -> ProcessTeacherPublic:
        process_teacher = ProcessTeacherController._get_or_404(
            session, process_id, process_teacher_id
        )
        session.delete(process_teacher)
        session.commit()
        return ProcessTeacherPublic.model_validate(process_teacher)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, process_teacher_id: uuid.UUID
    ) -> ProcessTeacher:
        DomainController.get_process_or_404(session, process_id)
        statement = select(ProcessTeacher).where(
            ProcessTeacher.id == process_teacher_id
        )
        process_teacher = session.exec(statement).first()
        if (
            process_teacher is None
            or process_teacher.assignment_process_id != process_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"ProcessTeacher {process_teacher_id} not found in process "
                    f"{process_id}."
                ),
            )
        return process_teacher


__all__ = ["ProcessTeacherController"]
