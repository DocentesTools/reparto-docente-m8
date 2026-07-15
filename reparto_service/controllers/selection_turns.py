"""SelectionTurn controller for department-head-controlled meetings."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.assignments import AssignmentController
from reparto_service.controllers.base import DomainController
from reparto_service.controllers.meeting_sessions import MeetingSessionController
from reparto_service.db_models.assignments import AssignmentCreate
from reparto_service.db_models.meeting_sessions import MeetingSession
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.selection_turns import (
    SelectionTurn,
    SelectionTurnAction,
    SelectionTurnComplete,
    SelectionTurnPublic,
    SelectionTurnsPublic,
)
from reparto_service.enums import (
    MeetingSessionStatus,
    ProcessTeacherStatus,
    SelectionTurnStatus,
)


class SelectionTurnController(DomainController):
    """State transitions for turn-order meeting sessions."""

    @staticmethod
    def list_turns(
        session: Session, process_id: uuid.UUID, meeting_session_id: uuid.UUID
    ) -> SelectionTurnsPublic:
        SelectionTurnController._get_meeting_session(
            session, process_id, meeting_session_id
        )
        turns = SelectionTurnController._load_turns(session, meeting_session_id)
        return SelectionTurnsPublic(
            data=[SelectionTurnPublic.model_validate(turn) for turn in turns],
            count=len(turns),
        )

    @staticmethod
    def initialize_turns(
        session: Session, process_id: uuid.UUID, meeting_session_id: uuid.UUID
    ) -> SelectionTurnsPublic:
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        SelectionTurnController._get_meeting_session(
            session, process_id, meeting_session_id
        )
        if SelectionTurnController._load_turns(session, meeting_session_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selection turns already exist for this meeting session.",
            )
        teachers = SelectionTurnController._ordered_teachers(session, process_id)
        for teacher in teachers:
            session.add(
                SelectionTurn(
                    meeting_session_id=meeting_session_id,
                    process_teacher_id=teacher.id,
                    position=teacher.selection_position or 0,
                )
            )
        session.commit()
        return SelectionTurnController.list_turns(
            session, process_id, meeting_session_id
        )

    @staticmethod
    def start_turn(
        session: Session,
        process_id: uuid.UUID,
        meeting_session_id: uuid.UUID,
        turn_id: uuid.UUID,
        current_user: UserModel,
    ) -> SelectionTurnPublic:
        meeting_session = SelectionTurnController._ensure_session_can_select(
            session, process_id, meeting_session_id
        )
        turn = SelectionTurnController._get_or_404(session, meeting_session.id, turn_id)
        SelectionTurnController._ensure_no_active_turn(session, meeting_session.id)
        if turn.status != SelectionTurnStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only pending turns can be started.",
            )
        before = SelectionTurnController._snapshot_turn(turn)
        turn.status = SelectionTurnStatus.ACTIVE
        turn.started_at = datetime.now(tz=timezone.utc)
        meeting_session.status = MeetingSessionStatus.SELECTING
        session.add(meeting_session)
        session.add(turn)
        SelectionTurnController._record_turn_audit(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="selection_turn.started",
            before=before,
            after=turn,
        )
        session.commit()
        session.refresh(turn)
        return SelectionTurnPublic.model_validate(turn)

    @staticmethod
    def complete_turn(
        session: Session,
        process_id: uuid.UUID,
        meeting_session_id: uuid.UUID,
        turn_id: uuid.UUID,
        current_user: UserModel,
        payload: SelectionTurnComplete,
    ) -> SelectionTurnPublic:
        meeting_session = SelectionTurnController._ensure_session_can_select(
            session, process_id, meeting_session_id
        )
        turn = SelectionTurnController._get_or_404(session, meeting_session.id, turn_id)
        SelectionTurnController._ensure_active_turn(turn)
        before = SelectionTurnController._snapshot_turn(turn)
        if payload.assignment is not None:
            SelectionTurnController._record_turn_assignment(
                session, process_id, current_user, turn, payload.assignment
            )
        turn.status = SelectionTurnStatus.COMPLETED
        turn.completed_at = datetime.now(tz=timezone.utc)
        turn.notes = payload.notes if payload.notes is not None else turn.notes
        session.add(turn)
        SelectionTurnController._record_turn_audit(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="selection_turn.completed",
            before=before,
            after=turn,
        )
        session.commit()
        session.refresh(turn)
        return SelectionTurnPublic.model_validate(turn)

    @staticmethod
    def skip_turn(
        session: Session,
        process_id: uuid.UUID,
        meeting_session_id: uuid.UUID,
        turn_id: uuid.UUID,
        current_user: UserModel,
        payload: SelectionTurnAction,
    ) -> SelectionTurnPublic:
        SelectionTurnController._ensure_session_can_select(
            session, process_id, meeting_session_id
        )
        turn = SelectionTurnController._get_or_404(session, meeting_session_id, turn_id)
        if turn.status not in {
            SelectionTurnStatus.PENDING,
            SelectionTurnStatus.ACTIVE,
        }:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only pending or active turns can be skipped.",
            )
        before = SelectionTurnController._snapshot_turn(turn)
        turn.status = SelectionTurnStatus.SKIPPED
        turn.skip_reason = payload.reason
        turn.notes = payload.notes if payload.notes is not None else turn.notes
        turn.skipped_at = datetime.now(tz=timezone.utc)
        session.add(turn)
        SelectionTurnController._record_turn_audit(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="selection_turn.skipped",
            before=before,
            after=turn,
            reason=payload.reason,
        )
        session.commit()
        session.refresh(turn)
        return SelectionTurnPublic.model_validate(turn)

    @staticmethod
    def override_turn(
        session: Session,
        process_id: uuid.UUID,
        meeting_session_id: uuid.UUID,
        turn_id: uuid.UUID,
        current_user: UserModel,
        payload: SelectionTurnAction,
    ) -> SelectionTurnPublic:
        SelectionTurnController._ensure_session_can_select(
            session, process_id, meeting_session_id
        )
        turn = SelectionTurnController._get_or_404(session, meeting_session_id, turn_id)
        before = SelectionTurnController._snapshot_turn(turn)
        turn.status = SelectionTurnStatus.OVERRIDDEN
        turn.skip_reason = payload.reason
        turn.notes = payload.notes if payload.notes is not None else turn.notes
        turn.forced_by_user_id = uuid.UUID(str(current_user.id))
        turn.skipped_at = turn.skipped_at or datetime.now(tz=timezone.utc)
        session.add(turn)
        SelectionTurnController._record_turn_audit(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type="selection_turn.overridden",
            before=before,
            after=turn,
            reason=payload.reason,
        )
        session.commit()
        session.refresh(turn)
        return SelectionTurnPublic.model_validate(turn)

    @staticmethod
    def _record_turn_assignment(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
        turn: SelectionTurn,
        assignment_in: AssignmentCreate,
    ) -> None:
        """Record the department head's manual choice for the active turn.

        The meeting turn-completion flow is a department-head manual
        assignment, so it goes through the **same** shared complete-slot
        service as the standalone ``POST /assignments`` route and the teacher
        LAN direct choice — no separate assignment business logic lives here
        (plan §7.7). The only turn-specific rule is that the slot must be
        recorded for the teacher whose turn is active; the complete-slot
        invariants (slot availability, distinct teacher, exact target) are
        enforced inside ``create_manual_assignment``. It records the
        ``assignment.created`` audit event but does not commit — the enclosing
        ``complete_turn`` owns the transaction.
        """
        if assignment_in.process_teacher_id != turn.process_teacher_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Turn assignment must target the active turn teacher.",
            )
        AssignmentController.create_manual_assignment(
            session,
            process_id=process_id,
            current_user=current_user,
            hour_requirement_id=assignment_in.hour_requirement_id,
            process_teacher_id=assignment_in.process_teacher_id,
            notes=assignment_in.notes,
        )

    @staticmethod
    def _record_turn_audit(
        session: Session,
        *,
        process_id: uuid.UUID,
        current_user: UserModel,
        event_type: str,
        before: SelectionTurn,
        after: SelectionTurn,
        reason: str | None = None,
    ) -> None:
        SelectionTurnController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=event_type,
            entity_type="selection_turn",
            entity_id=after.id,
            before=before,
            after=after,
            reason=reason,
        )

    @staticmethod
    def _snapshot_turn(turn: SelectionTurn) -> SelectionTurn:
        return SelectionTurn.model_validate(turn.model_dump())

    @staticmethod
    def _ordered_teachers(
        session: Session, process_id: uuid.UUID
    ) -> list[ProcessTeacher]:
        statement = select(ProcessTeacher).where(
            ProcessTeacher.assignment_process_id == process_id,
            ProcessTeacher.status == ProcessTeacherStatus.ACTIVE,
            ProcessTeacher.participates_in_selection,
        )
        teachers = list(session.exec(statement).all())
        positions = [teacher.selection_position for teacher in teachers]
        if not teachers or any(position is None for position in positions):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Every participating teacher needs a selection_position.",
            )
        concrete = [int(position) for position in positions if position is not None]
        if len(set(concrete)) != len(concrete):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate selection positions are not allowed.",
            )
        return sorted(
            teachers, key=lambda teacher: int(teacher.selection_position or 0)
        )

    @staticmethod
    def _ensure_session_can_select(
        session: Session, process_id: uuid.UUID, meeting_session_id: uuid.UUID
    ) -> MeetingSession:
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        meeting_session = SelectionTurnController._get_meeting_session(
            session, process_id, meeting_session_id
        )
        if meeting_session.status not in {
            MeetingSessionStatus.OPEN,
            MeetingSessionStatus.SELECTING,
            MeetingSessionStatus.REOPENED,
        }:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Meeting session must be open before turns can run.",
            )
        return meeting_session

    @staticmethod
    def _get_meeting_session(
        session: Session, process_id: uuid.UUID, meeting_session_id: uuid.UUID
    ) -> MeetingSession:
        return MeetingSessionController._get_or_404(
            session, process_id, meeting_session_id
        )

    @staticmethod
    def _get_or_404(
        session: Session, meeting_session_id: uuid.UUID, turn_id: uuid.UUID
    ) -> SelectionTurn:
        statement = select(SelectionTurn).where(SelectionTurn.id == turn_id)
        turn = session.exec(statement).first()
        if turn is None or turn.meeting_session_id != meeting_session_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SelectionTurn {turn_id} not found.",
            )
        return turn

    @staticmethod
    def _load_turns(
        session: Session, meeting_session_id: uuid.UUID
    ) -> list[SelectionTurn]:
        statement = (
            select(SelectionTurn)
            .where(SelectionTurn.meeting_session_id == meeting_session_id)
            .order_by(col(SelectionTurn.position))
        )
        return list(session.exec(statement).all())

    @staticmethod
    def _ensure_no_active_turn(session: Session, meeting_session_id: uuid.UUID) -> None:
        statement = select(SelectionTurn).where(
            SelectionTurn.meeting_session_id == meeting_session_id,
            SelectionTurn.status == SelectionTurnStatus.ACTIVE,
        )
        if session.exec(statement).first() is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A selection turn is already active.",
            )

    @staticmethod
    def _ensure_active_turn(turn: SelectionTurn) -> None:
        if turn.status != SelectionTurnStatus.ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only the active turn can be completed.",
            )


__all__ = ["SelectionTurnController"]
