"""MeetingSession controller for Phase 2 LAN read mode."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.meeting_sessions import (
    MeetingSession,
    MeetingSessionCreate,
    MeetingSessionPublic,
    MeetingSessionsPublic,
    MeetingSessionUpdate,
)
from reparto_service.enums import (
    AssignmentProcessStatus,
    MeetingSessionStatus,
)
from reparto_service.services.lifecycle_gates import PlanReadinessGate

_ACTIVE_SESSION_STATUSES: frozenset[MeetingSessionStatus] = frozenset(
    {
        MeetingSessionStatus.PREPARED,
        MeetingSessionStatus.OPEN,
        MeetingSessionStatus.SELECTING,
        MeetingSessionStatus.PAUSED,
        MeetingSessionStatus.REOPENED,
    }
)

_STARTED_SESSION_STATUSES: frozenset[MeetingSessionStatus] = frozenset(
    {
        MeetingSessionStatus.OPEN,
        MeetingSessionStatus.SELECTING,
        MeetingSessionStatus.REOPENED,
    }
)


class MeetingSessionController(DomainController):
    """CRUD and state logic for meeting sessions."""

    @staticmethod
    def list_sessions(session: Session, process_id: uuid.UUID) -> MeetingSessionsPublic:
        DomainController.get_process_or_404(session, process_id)
        statement = select(MeetingSession).where(
            MeetingSession.assignment_process_id == process_id
        )
        items = list(session.exec(statement).all())
        return MeetingSessionsPublic(
            data=[MeetingSessionPublic.model_validate(item) for item in items],
            count=len(items),
        )

    @staticmethod
    def get_session(
        session: Session, process_id: uuid.UUID, meeting_session_id: uuid.UUID
    ) -> MeetingSessionPublic:
        meeting_session = MeetingSessionController._get_or_404(
            session, process_id, meeting_session_id
        )
        return MeetingSessionPublic.model_validate(meeting_session)

    @staticmethod
    def create_session(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        meeting_session_in: MeetingSessionCreate,
    ) -> MeetingSessionPublic:
        process = DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        if meeting_session_in.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "assignment_process_id in the payload does not match the "
                    "URL process_id."
                ),
            )
        MeetingSessionController._ensure_no_active_session(session, process_id)
        meeting_session = MeetingSession.model_validate(meeting_session_in.model_dump())
        now = datetime.now(tz=timezone.utc)
        if meeting_session.status in _STARTED_SESSION_STATUSES:
            # Opening a meeting is a stage-entry operation (plan §3.10): refuse it
            # unless the plan is balanced, locked and generated.
            PlanReadinessGate.ensure_ready_for_assignment_stage(
                session, process_id, operation="open a meeting"
            )
            meeting_session.started_at = now
            meeting_session.started_by_user_id = uuid.UUID(str(current_user.id))
            process.status = AssignmentProcessStatus.MEETING_OPEN
        if meeting_session.status == MeetingSessionStatus.PAUSED:
            meeting_session.paused_at = now
        MeetingSessionController._sync_process_flags(process, meeting_session)
        session.add(process)
        session.add(meeting_session)
        session.commit()
        session.refresh(meeting_session)
        return MeetingSessionPublic.model_validate(meeting_session)

    @staticmethod
    def update_session(
        session: Session,
        process_id: uuid.UUID,
        meeting_session_id: uuid.UUID,
        current_user: UserModel,
        meeting_session_in: MeetingSessionUpdate,
    ) -> MeetingSessionPublic:
        process = DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        meeting_session = MeetingSessionController._get_or_404(
            session, process_id, meeting_session_id
        )
        update_dict = meeting_session_in.model_dump(exclude_unset=True)
        lan_enabled = update_dict.get(
            "lan_access_enabled", meeting_session.lan_access_enabled
        )
        direct_enabled = update_dict.get(
            "direct_teacher_selection_enabled",
            meeting_session.direct_teacher_selection_enabled,
        )
        if direct_enabled and not lan_enabled:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Direct teacher selection requires LAN access.",
            )
        meeting_session.sqlmodel_update(update_dict)
        now = datetime.now(tz=timezone.utc)
        if (
            meeting_session.status in _STARTED_SESSION_STATUSES
            and meeting_session.started_at is None
        ):
            # Opening a meeting is a stage-entry operation (plan §3.10): refuse it
            # unless the plan is balanced, locked and generated.
            PlanReadinessGate.ensure_ready_for_assignment_stage(
                session, process_id, operation="open a meeting"
            )
            meeting_session.started_at = now
            meeting_session.started_by_user_id = uuid.UUID(str(current_user.id))
            process.status = AssignmentProcessStatus.MEETING_OPEN
        if meeting_session.status == MeetingSessionStatus.PAUSED:
            meeting_session.paused_at = meeting_session.paused_at or now
        if meeting_session.status == MeetingSessionStatus.CLOSED:
            meeting_session.closed_at = meeting_session.closed_at or now
        MeetingSessionController._sync_process_flags(process, meeting_session)
        session.add(process)
        session.add(meeting_session)
        session.commit()
        session.refresh(meeting_session)
        return MeetingSessionPublic.model_validate(meeting_session)

    @staticmethod
    def close_session(
        session: Session,
        process_id: uuid.UUID,
        meeting_session_id: uuid.UUID,
    ) -> MeetingSessionPublic:
        process = DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        meeting_session = MeetingSessionController._get_or_404(
            session, process_id, meeting_session_id
        )
        meeting_session.status = MeetingSessionStatus.CLOSED
        meeting_session.closed_at = meeting_session.closed_at or datetime.now(
            tz=timezone.utc
        )
        process.lan_access_enabled = False
        process.direct_teacher_selection_enabled = False
        session.add(process)
        session.add(meeting_session)
        session.commit()
        session.refresh(meeting_session)
        return MeetingSessionPublic.model_validate(meeting_session)

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, meeting_session_id: uuid.UUID
    ) -> MeetingSession:
        DomainController.get_process_or_404(session, process_id)
        statement = select(MeetingSession).where(
            MeetingSession.id == meeting_session_id
        )
        meeting_session = session.exec(statement).first()
        if (
            meeting_session is None
            or meeting_session.assignment_process_id != process_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"MeetingSession {meeting_session_id} not found in process "
                    f"{process_id}."
                ),
            )
        return meeting_session

    @staticmethod
    def _ensure_no_active_session(session: Session, process_id: uuid.UUID) -> None:
        statement = select(MeetingSession).where(
            MeetingSession.assignment_process_id == process_id,
            col(MeetingSession.status).in_(_ACTIVE_SESSION_STATUSES),
        )
        if session.exec(statement).first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="An active meeting session already exists for this process.",
            )

    @staticmethod
    def _sync_process_flags(
        process: AssignmentProcess, meeting_session: MeetingSession
    ) -> None:
        process.lan_access_enabled = meeting_session.lan_access_enabled
        process.direct_teacher_selection_enabled = (
            meeting_session.direct_teacher_selection_enabled
        )
        process.selection_order_mode = meeting_session.selection_mode


__all__ = ["MeetingSessionController"]
