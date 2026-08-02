"""MeetingSession routes for assignment-process LAN read sessions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from reparto_service.app.deps import CurrentUser, SessionDep, require_visible_process
from reparto_service.controllers.meeting_sessions import MeetingSessionController
from reparto_service.db_models.meeting_sessions import (
    MeetingSessionCreate,
    MeetingSessionPublic,
    MeetingSessionsPublic,
    MeetingSessionUpdate,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/meeting-sessions",
    tags=["meeting-sessions"],
    # Read scope (plan §21.4): every route under this process — read and
    # mutation alike — is refused with 404 when the process lies outside the
    # caller's departments.
    dependencies=[Depends(require_visible_process)],
)


@router.get("/", response_model=MeetingSessionsPublic)
def list_sessions(session: SessionDep, process_id: uuid.UUID) -> MeetingSessionsPublic:
    return MeetingSessionController.list_sessions(session, process_id)


@router.post("/", response_model=MeetingSessionPublic, status_code=201)
def create_session(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    meeting_session_in: MeetingSessionCreate,
) -> MeetingSessionPublic:
    MeetingSessionController.require_department_head(current_user)
    return MeetingSessionController.create_session(
        session, process_id, current_user, meeting_session_in
    )


@router.get("/{meeting_session_id}", response_model=MeetingSessionPublic)
def get_session(
    session: SessionDep,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
) -> MeetingSessionPublic:
    return MeetingSessionController.get_session(session, process_id, meeting_session_id)


@router.patch("/{meeting_session_id}", response_model=MeetingSessionPublic)
def update_session(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
    meeting_session_in: MeetingSessionUpdate,
) -> MeetingSessionPublic:
    MeetingSessionController.require_department_head(current_user)
    return MeetingSessionController.update_session(
        session, process_id, meeting_session_id, current_user, meeting_session_in
    )


@router.post("/{meeting_session_id}/close", response_model=MeetingSessionPublic)
def close_session(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
) -> MeetingSessionPublic:
    MeetingSessionController.require_department_head(current_user)
    return MeetingSessionController.close_session(
        session, process_id, meeting_session_id
    )
