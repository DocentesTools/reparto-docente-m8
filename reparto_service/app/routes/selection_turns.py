"""SelectionTurn routes for meeting-session turn order."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.selection_turns import SelectionTurnController
from reparto_service.db_models.selection_turns import (
    SelectionTurnAction,
    SelectionTurnComplete,
    SelectionTurnPublic,
    SelectionTurnsPublic,
)

router = APIRouter(
    prefix=(
        "/assignment-processes/{process_id}/meeting-sessions/{meeting_session_id}/turns"
    ),
    tags=["selection-turns"],
)


@router.get("/", response_model=SelectionTurnsPublic)
def list_turns(
    session: SessionDep,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
) -> SelectionTurnsPublic:
    return SelectionTurnController.list_turns(session, process_id, meeting_session_id)


@router.post("/initialize", response_model=SelectionTurnsPublic, status_code=201)
def initialize_turns(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
) -> SelectionTurnsPublic:
    SelectionTurnController.require_process_writer(session, current_user, process_id)
    return SelectionTurnController.initialize_turns(
        session, process_id, meeting_session_id
    )


@router.post("/{turn_id}/start", response_model=SelectionTurnPublic)
def start_turn(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
    turn_id: uuid.UUID,
) -> SelectionTurnPublic:
    SelectionTurnController.require_process_writer(session, current_user, process_id)
    return SelectionTurnController.start_turn(
        session, process_id, meeting_session_id, turn_id, current_user
    )


@router.post("/{turn_id}/complete", response_model=SelectionTurnPublic)
def complete_turn(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
    turn_id: uuid.UUID,
    payload: SelectionTurnComplete,
) -> SelectionTurnPublic:
    SelectionTurnController.require_process_writer(session, current_user, process_id)
    return SelectionTurnController.complete_turn(
        session, process_id, meeting_session_id, turn_id, current_user, payload
    )


@router.post("/{turn_id}/skip", response_model=SelectionTurnPublic)
def skip_turn(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
    turn_id: uuid.UUID,
    payload: SelectionTurnAction,
) -> SelectionTurnPublic:
    SelectionTurnController.require_process_writer(session, current_user, process_id)
    return SelectionTurnController.skip_turn(
        session, process_id, meeting_session_id, turn_id, current_user, payload
    )


@router.post("/{turn_id}/override", response_model=SelectionTurnPublic)
def override_turn(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    meeting_session_id: uuid.UUID,
    turn_id: uuid.UUID,
    payload: SelectionTurnAction,
) -> SelectionTurnPublic:
    SelectionTurnController.require_process_writer(session, current_user, process_id)
    return SelectionTurnController.override_turn(
        session, process_id, meeting_session_id, turn_id, current_user, payload
    )
