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
    AssignmentReassign,
    AssignmentUndo,
    AssignmentsPublic,
    AssignmentUpdate,
)
from reparto_service.schemas.planning import AssignmentValidationReport

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
    AssignmentController.require_department_head(current_user)
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
    # Own-data mutation (plan §21.3): ``WRITER`` is the floor, and the
    # controller binds the assignment to the caller's *own* participation row —
    # there is no participant id in the payload to point somewhere else.
    AssignmentController.require_writer(current_user)
    return AssignmentController.create_direct_choice(
        session, process_id, current_user, choice
    )


@router.get("/validations", response_model=AssignmentValidationReport)
def get_assignment_validations(
    session: SessionDep, process_id: uuid.UUID
) -> AssignmentValidationReport:
    return AssignmentController.get_validations(session, process_id)


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
    AssignmentController.require_department_head(current_user)
    return AssignmentController.update_assignment(
        session, process_id, assignment_id, assignment_in, current_user
    )


@router.post("/{assignment_id}/undo", response_model=AssignmentPublic)
def undo_assignment(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
    action: AssignmentUndo,
) -> AssignmentPublic:
    AssignmentController.require_admin(current_user)
    return AssignmentController.undo_assignment(
        session, process_id, assignment_id, current_user, action
    )


@router.post(
    "/{assignment_id}/reassign", response_model=AssignmentPublic, status_code=201
)
def reassign_assignment(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
    action: AssignmentReassign,
) -> AssignmentPublic:
    AssignmentController.require_admin(current_user)
    return AssignmentController.reassign_assignment(
        session, process_id, assignment_id, current_user, action
    )


@router.delete(
    "/{assignment_id}",
    response_model=AssignmentPublic,
    deprecated=True,
    include_in_schema=False,
)
def delete_assignment(
    session: SessionDep,
    current_user: CurrentUser,
    process_id: uuid.UUID,
    assignment_id: uuid.UUID,
    action: AssignmentUndo,
) -> AssignmentPublic:
    """Compatibility alias for the explicit, reason-required undo action."""
    AssignmentController.require_admin(current_user)
    return AssignmentController.undo_assignment(
        session, process_id, assignment_id, current_user, action
    )
