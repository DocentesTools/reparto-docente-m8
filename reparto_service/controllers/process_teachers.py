"""ProcessTeacher controller (teacher-per-process binding)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.process_teachers import (
    ProcessTeacher,
    ProcessTeacherCreate,
    ProcessTeacherExtraHoursUpdate,
    ProcessTeacherPublic,
    ProcessTeachersPublic,
    ProcessTeacherUpdate,
)
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.enums import AssignmentStatus

# Tolerance for the decimal-hour comparison guarding an extra-hours
# reduction. Hours are still stored as floats until the §3.9 Decimal
# sweep; a sub-epsilon gap counts as equal.
_HOUR_EPSILON = 1e-6


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
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
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
        ProcessTeacherController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="process_teacher.created",
            entity_type="process_teacher",
            entity_id=process_teacher.id,
            before=None,
            after=process_teacher,
        )
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
        process, process_teacher = ProcessTeacherController._get_for_update_or_404(
            session, process_id, process_teacher_id
        )
        DomainController.ensure_process_mutable(process)
        before = ProcessTeacher.model_validate(process_teacher.model_dump())
        process_teacher.sqlmodel_update(
            process_teacher_in.model_dump(exclude_unset=True)
        )
        session.add(process_teacher)
        ProcessTeacherController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="process_teacher.updated",
            entity_type="process_teacher",
            entity_id=process_teacher.id,
            before=before,
            after=process_teacher,
        )
        session.commit()
        session.refresh(process_teacher)
        return ProcessTeacherPublic.model_validate(process_teacher)

    @staticmethod
    def update_extra_hours(
        session: Session,
        process_id: uuid.UUID,
        process_teacher_id: uuid.UUID,
        payload: ProcessTeacherExtraHoursUpdate,
        current_user: UserModel,
    ) -> ProcessTeacherPublic:
        """Set authorized extra hours through the audited dedicated action.

        Enforces plan §3.8: the change carries a mandatory reason and an
        audit event, and a reduction is blocked when the new target would
        fall below the hours already assigned to the participant.
        """
        process, process_teacher = ProcessTeacherController._get_for_update_or_404(
            session, process_id, process_teacher_id
        )
        DomainController.ensure_process_mutable(process)
        new_target = process_teacher.base_weekly_hours + payload.extra_weekly_hours
        assigned = ProcessTeacherController._assigned_hours(session, process_teacher.id)
        if new_target + _HOUR_EPSILON < assigned:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot reduce extra hours below the hours already assigned: "
                    f"new target {new_target:.2f} h < assigned {assigned:.2f} h."
                ),
            )
        before = ProcessTeacher.model_validate(process_teacher.model_dump())
        process_teacher.extra_weekly_hours = payload.extra_weekly_hours
        process_teacher.extra_hours_reason = payload.reason
        process_teacher.extra_hours_updated_by_user_id = uuid.UUID(str(current_user.id))
        process_teacher.extra_hours_updated_at = datetime.now(tz=timezone.utc)
        session.add(process_teacher)
        ProcessTeacherController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="process_teacher.extra_hours_updated",
            entity_type="process_teacher",
            entity_id=process_teacher.id,
            before=before,
            after=process_teacher,
            reason=payload.reason,
        )
        session.commit()
        session.refresh(process_teacher)
        return ProcessTeacherPublic.model_validate(process_teacher)

    @staticmethod
    def delete_process_teacher(
        session: Session,
        process_id: uuid.UUID,
        process_teacher_id: uuid.UUID,
        current_user: UserModel,
    ) -> ProcessTeacherPublic:
        process, process_teacher = ProcessTeacherController._get_for_update_or_404(
            session, process_id, process_teacher_id
        )
        DomainController.ensure_process_mutable(process)
        before = ProcessTeacher.model_validate(process_teacher.model_dump())
        session.delete(process_teacher)
        ProcessTeacherController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="process_teacher.deleted",
            entity_type="process_teacher",
            entity_id=process_teacher.id,
            before=before,
            after=None,
        )
        session.commit()
        return ProcessTeacherPublic.model_validate(process_teacher)

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _assigned_hours(session: Session, process_teacher_id: uuid.UUID) -> float:
        """Return the participant's currently assigned hours (active only)."""
        statement = select(Assignment).where(
            Assignment.process_teacher_id == process_teacher_id
        )
        return sum(
            assignment.assigned_hours
            for assignment in session.exec(statement).all()
            if assignment.status != AssignmentStatus.CANCELLED
        )

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

    @staticmethod
    def _get_for_update_or_404(
        session: Session, process_id: uuid.UUID, process_teacher_id: uuid.UUID
    ) -> tuple[AssignmentProcess, ProcessTeacher]:
        """Return both the process and the process teacher in one shot.

        Update / delete paths need the process for the immutability
        check; this helper avoids two round-trips to the database.
        """
        process = DomainController.get_process_or_404(session, process_id)
        process_teacher = ProcessTeacherController._get_or_404(
            session, process_id, process_teacher_id
        )
        return process, process_teacher


__all__ = ["ProcessTeacherController"]
