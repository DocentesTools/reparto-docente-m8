"""Dashboard controller.

The dashboard is a read-only composition of the per-process balances and
validation messages. It does not mutate any state.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session
from auth_sdk_m8.schemas.user import UserModel
from sqlmodel import select

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.enums import ValidationSeverity
from reparto_service.schemas.summary import (
    ProcessDashboard,
    ProcessSummary,
    TeacherLanSummary,
)
from reparto_service.services.summary import SummaryService


class DashboardController(DomainController):
    """Read-only controller for the department-head dashboard."""

    @staticmethod
    def get_dashboard(session: Session, process_id: uuid.UUID) -> ProcessDashboard:
        DomainController.get_process_or_404(session, process_id)
        return SummaryService.compute_dashboard(session, process_id)

    @staticmethod
    def get_summary(session: Session, process_id: uuid.UUID) -> ProcessSummary:
        DomainController.get_process_or_404(session, process_id)
        return SummaryService.compute_summary(session, process_id)

    @staticmethod
    def get_teacher_lan_summary(
        session: Session, process_id: uuid.UUID, current_user: UserModel
    ) -> TeacherLanSummary:
        DomainController.get_process_or_404(session, process_id)
        user_id = uuid.UUID(str(current_user.id))
        statement = (
            select(ProcessTeacher, TeacherProfile)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.teacher_profile_id == TeacherProfile.id)
            .where(TeacherProfile.user_id == user_id)
        )
        row = session.exec(statement).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No teacher profile is linked to this auth user.",
            )
        process_teacher, profile = row
        teacher_balance = next(
            (
                balance
                for balance in SummaryService.compute_teacher_balances(
                    session, process_id
                )
                if balance.process_teacher_id == process_teacher.id
            ),
            None,
        )
        if teacher_balance is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Linked teacher is not part of this assignment process.",
            )
        validations = SummaryService.compute_validations(session, process_id)
        blocking = sum(
            1
            for validation in validations
            if validation.severity == ValidationSeverity.BLOCKING
        )
        return TeacherLanSummary(
            process_id=process_id,
            teacher_profile_id=profile.id,
            process_teacher_id=process_teacher.id,
            generated_at=datetime.now(tz=timezone.utc),
            global_balance=SummaryService.compute_global_balance(session, process_id),
            teacher_balance=teacher_balance,
            current_turn=SummaryService.compute_current_turn(session, process_id),
            blocking_validation_count=blocking,
        )


__all__ = ["DashboardController"]
