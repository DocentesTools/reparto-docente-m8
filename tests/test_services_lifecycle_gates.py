"""Unit tests for the plan-readiness lifecycle gates (plan §3.10, §3.11, §9).

These exercise :class:`PlanReadinessGate` directly against an in-memory session
so every status branch and reason string is covered without an HTTP stack. The
route-level integration is tested in ``test_routes_meeting_sessions.py`` (strict
stage-entry gate) and ``test_routes_assignments.py`` (lenient assignment gate).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from reparto_service.enums import TeachingPlanStatus
from reparto_service.services.lifecycle_gates import PlanReadinessGate
from tests import factories


def _process(session: Session):
    return factories.make_assignment_process(session)


# ── Strict stage-entry gate ───────────────────────────────────────────────────


def test_ready_gate_passes_when_requirements_generated(session: Session) -> None:
    process = _process(session)
    plan = factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    returned = PlanReadinessGate.ensure_ready_for_assignment_stage(
        session, process.id, operation="open a meeting"
    )
    assert returned.id == plan.id


def test_ready_gate_rejects_missing_plan(session: Session) -> None:
    process = _process(session)
    with pytest.raises(HTTPException) as exc:
        PlanReadinessGate.ensure_ready_for_assignment_stage(
            session, process.id, operation="open a meeting"
        )
    assert exc.value.status_code == 409
    assert "no teaching plan" in exc.value.detail
    assert "open a meeting" in exc.value.detail


@pytest.mark.parametrize(
    ("plan_status", "needle"),
    [
        (TeachingPlanStatus.DRAFT, "draft"),
        (TeachingPlanStatus.UNBALANCED, "unbalanced"),
        (TeachingPlanStatus.BALANCED, "balanced"),
        (TeachingPlanStatus.LOCKED, "locked"),
        (TeachingPlanStatus.STALE, "stale"),
        (TeachingPlanStatus.RECONCILIATION_REQUIRED, "reconciliation"),
    ],
)
def test_ready_gate_rejects_non_generated_statuses(
    session: Session, plan_status: TeachingPlanStatus, needle: str
) -> None:
    process = _process(session)
    factories.make_teaching_plan(session, process, status=plan_status)
    with pytest.raises(HTTPException) as exc:
        PlanReadinessGate.ensure_ready_for_assignment_stage(
            session, process.id, operation="open a meeting"
        )
    assert exc.value.status_code == 409
    assert needle in exc.value.detail


# ── Lenient mid-flight assignment gate ────────────────────────────────────────


def test_unblocked_gate_allows_missing_plan(session: Session) -> None:
    process = _process(session)
    # No plan → nothing to block (the slot lookup 404s on its own).
    PlanReadinessGate.ensure_assignments_unblocked(
        session, process.id, operation="create an assignment"
    )


@pytest.mark.parametrize(
    "plan_status",
    [
        TeachingPlanStatus.DRAFT,
        TeachingPlanStatus.BALANCED,
        TeachingPlanStatus.LOCKED,
        TeachingPlanStatus.REQUIREMENTS_GENERATED,
    ],
)
def test_unblocked_gate_allows_non_blocking_statuses(
    session: Session, plan_status: TeachingPlanStatus
) -> None:
    process = _process(session)
    factories.make_teaching_plan(session, process, status=plan_status)
    PlanReadinessGate.ensure_assignments_unblocked(
        session, process.id, operation="create an assignment"
    )


@pytest.mark.parametrize(
    ("plan_status", "needle"),
    [
        (TeachingPlanStatus.STALE, "stale"),
        (TeachingPlanStatus.RECONCILIATION_REQUIRED, "reconciliation"),
    ],
)
def test_unblocked_gate_rejects_pending_reconciliation(
    session: Session, plan_status: TeachingPlanStatus, needle: str
) -> None:
    process = _process(session)
    factories.make_teaching_plan(session, process, status=plan_status)
    with pytest.raises(HTTPException) as exc:
        PlanReadinessGate.ensure_assignments_unblocked(
            session, process.id, operation="create an assignment"
        )
    assert exc.value.status_code == 409
    assert needle in exc.value.detail
    assert "create an assignment" in exc.value.detail


def test_gate_isolated_per_process(session: Session) -> None:
    """A ready plan on one process does not satisfy another process's gate."""
    ready = _process(session)
    factories.make_teaching_plan(
        session, ready, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    other = uuid.uuid4()
    with pytest.raises(HTTPException) as exc:
        PlanReadinessGate.ensure_ready_for_assignment_stage(
            session, other, operation="open a meeting"
        )
    assert exc.value.status_code == 409
