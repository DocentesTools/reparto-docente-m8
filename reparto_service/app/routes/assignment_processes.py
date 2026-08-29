"""AssignmentProcess routes.

Hosts the parent CRUD for an annual assignment process, the lifecycle
endpoints introduced for the Phase 1 state machine (plan §8.4, §10.2)
and the read-only summary/dashboard endpoints used by the
department-head view. Per-resource child endpoints (teachers, subjects,
groups, requirements, assignments) live in their own route files but
are mounted under the ``/assignment-processes/{process_id}/...``
namespace — including the SSE stream
(:mod:`reparto_service.app.routes.process_events`).
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends

from reparto_service.app.deps import (
    CurrentAdmin,
    CurrentReader,
    SessionDep,
    require_visible_process,
)
from reparto_service.controllers.assignment_processes import (
    AssignmentProcessController,
)
from reparto_service.controllers.dashboard import DashboardController
from reparto_service.db_models.assignment_processes import (
    AssignmentProcessCreate,
    AssignmentProcessesPublic,
    AssignmentProcessPublic,
    AssignmentProcessUpdate,
    ProcessCopyRequest,
    ProcessReopenRequest,
    ProcessTransitionRequest,
)
from reparto_service.schemas.dashboard import (
    ProcessDashboard,
    ProcessSummary,
    TeacherLanSummary,
)
from reparto_service.services.read_scope import ensure_process_visible

router = APIRouter(prefix="/assignment-processes", tags=["assignment-processes"])


@router.get("/", response_model=AssignmentProcessesPublic)
def list_processes(
    session: SessionDep,
    current_user: CurrentReader,
    academic_year_id: Optional[uuid.UUID] = None,
    skip: int = 0,
    limit: int = 100,
) -> AssignmentProcessesPublic:
    return AssignmentProcessController.list_processes(
        session, current_user, academic_year_id=academic_year_id, skip=skip, limit=limit
    )


@router.post("/", response_model=AssignmentProcessPublic, status_code=201)
def create_process(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_in: AssignmentProcessCreate,
) -> AssignmentProcessPublic:
    return AssignmentProcessController.create_process(session, current_user, process_in)


@router.get("/{process_id}", response_model=AssignmentProcessPublic)
def get_process(
    session: SessionDep, current_user: CurrentReader, process_id: uuid.UUID
) -> AssignmentProcessPublic:
    ensure_process_visible(session, current_user, process_id)
    return AssignmentProcessController.get_process(session, process_id)


@router.patch("/{process_id}", response_model=AssignmentProcessPublic)
def update_process(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    process_in: AssignmentProcessUpdate,
) -> AssignmentProcessPublic:
    return AssignmentProcessController.update_process(
        session, process_id, process_in, current_user
    )


# ── Lifecycle (plan §8.4, §10.2) ──────────────────────────────────────────────


@router.post("/{process_id}/transition", response_model=AssignmentProcessPublic)
def transition_process(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    request: ProcessTransitionRequest,
) -> AssignmentProcessPublic:
    return AssignmentProcessController.transition_process(
        session, process_id, current_user, request
    )


@router.post("/{process_id}/reopen", response_model=AssignmentProcessPublic)
def reopen_process(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    request: ProcessReopenRequest,
) -> AssignmentProcessPublic:
    return AssignmentProcessController.reopen_process(
        session, process_id, current_user, request
    )


@router.post(
    "/{process_id}/copy-from/{source_process_id}",
    response_model=AssignmentProcessPublic,
)
def copy_from_process(
    session: SessionDep,
    current_user: CurrentAdmin,
    process_id: uuid.UUID,
    source_process_id: uuid.UUID,
    request: ProcessCopyRequest,
) -> AssignmentProcessPublic:
    return AssignmentProcessController.copy_from_process(
        session, process_id, source_process_id, request, current_user
    )


# ── Summary / dashboard read endpoints ────────────────────────────────────────
#
# One process, three confidentiality tiers, one endpoint each (plan §11, §20.25,
# remediation `W5.3`):
#
# * ``/dashboard`` — the department-head tier. Per-participant rows and
#   validation findings that name the participant they are about, so it is an
#   administrator read like the feasibility witness, not a scoped one.
# * ``/lan/me`` — the teacher tier. The caller's own participation row and
#   nothing else, with the identifier-free aggregate balances beside it.
# * ``/summary`` — the shared-screen tier. Aggregates and nameless counts, safe
#   to project into a room (plan §8.7, ``RBAC-07``).
#
# Read scope (§21.4) decides *which processes* a caller may read; it never
# decided *which tier* they receive, and the two rules disagreed here until the
# floor below was raised: a participant cleared the scope check and was handed
# the head's payload, hour figures, extra-hours reasons and all.


@router.get("/{process_id}/summary", response_model=ProcessSummary)
def get_process_summary(
    session: SessionDep, current_user: CurrentReader, process_id: uuid.UUID
) -> ProcessSummary:
    ensure_process_visible(session, current_user, process_id)
    return DashboardController.get_summary(session, process_id)


@router.get(
    "/{process_id}/dashboard",
    response_model=ProcessDashboard,
    # Scope before role, as on every process-nested router: an out-of-scope
    # process is a 404 first, so the department-head floor below can never be
    # the thing that tells a stranger this process exists (§21.4).
    dependencies=[Depends(require_visible_process)],
)
def get_process_dashboard(
    session: SessionDep, current_user: CurrentAdmin, process_id: uuid.UUID
) -> ProcessDashboard:
    """Expose the full department-head payload only to an administrator.

    A participant asking for their own view asks ``/lan/me``; a projected
    screen asks ``/summary``. Both existed before this floor did, which is why
    narrowing costs no screen.
    """
    return DashboardController.get_dashboard(session, process_id)


@router.get("/{process_id}/lan/me", response_model=TeacherLanSummary)
def get_teacher_lan_summary(
    session: SessionDep,
    current_user: CurrentReader,
    process_id: uuid.UUID,
) -> TeacherLanSummary:
    ensure_process_visible(session, current_user, process_id)
    return DashboardController.get_teacher_lan_summary(
        session, process_id, current_user
    )
