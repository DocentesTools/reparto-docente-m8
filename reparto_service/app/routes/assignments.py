"""Assignment routes (nested under an assignment process)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from reparto_service.app.deps import CurrentUser, SessionDep
from reparto_service.controllers.assignments import AssignmentController
from reparto_service.db_models.assignments import (
    AssignmentCreate,
    AssignmentDirectChoice,
    AssignmentPublic,
    AssignmentsPublic,
    AssignmentUpdate,
)

router = APIRouter(
    prefix="/assignment-processes/{process_id}/assignments",
    tags=["assignments"],
)


@router.get("/", response_model=AssignmentsPublic)
def list_assignments(session: SessionDep, process_id: uuid.UUID) -> AssignmentsPublic:
    return AssignmentController.list_assignments(session, process_id)


@router.post("/", response_model=AssignmentPublic, status_code=201)
def create_assignment(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    assignment_in: AssignmentCreate,
) -> AssignmentPublic:
    AssignmentController.require_writer(current_user)
    return AssignmentController.create_assignment(
        session, process_id, current_user, assignment_in
    )


@router.post("/direct-choice", response_model=AssignmentPublic, status_code=201)
def create_direct_choice(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    choice: AssignmentDirectChoice,
) -> AssignmentPublic:
    return AssignmentController.create_direct_choice(
        session, process_id, current_user, choice
    )


@router.get("/{assignment_id}", response_model=AssignmentPublic)
def get_assignment(
    session: SessionDep, process_id: uuid.UUID, assignment_id: uuid.UUID
) -> AssignmentPublic:
    return AssignmentController.get_assignment(session, process_id, assignment_id)


@router.patch("/{assignment_id}", response_model=AssignmentPublic)
def update_assignment(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
    assignment_in: AssignmentUpdate,
) -> AssignmentPublic:
    AssignmentController.require_writer(current_user)
    return AssignmentController.update_assignment(
        session, process_id, assignment_id, assignment_in, current_user
    )


@router.delete("/{assignment_id}", response_model=AssignmentPublic)
def delete_assignment(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
) -> AssignmentPublic:
    AssignmentController.require_writer(current_user)
    return AssignmentController.delete_assignment(session, process_id, assignment_id)
