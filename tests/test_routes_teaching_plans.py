"""API + controller tests for the per-process teaching plan (plan §5.2, §20)."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.teaching_plans import TeachingPlanController
from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.enums import (
    AssignmentProcessStatus,
    FeasibilityStatus,
    TeachingPlanStatus,
)
from tests import factories

_BASE = "/reparto/assignment-processes"


# ── Create ────────────────────────────────────────────────────────────────────


def test_create_plan(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.post(f"{_BASE}/{process.id}/teaching-plan")
    assert resp.status_code == 201
    body = resp.json()
    assert body["assignment_process_id"] == str(process.id)
    assert body["status"] == "draft"
    assert body["current_generation_number"] == 0
    assert body["feasibility_status"] == "not_evaluated"
    assert body["allocation_revision_id"] is None
    assert body["locked_at"] is None
    assert body["stale_reason"] is None
    assert body["feasibility_input_fingerprint"] is None
    assert body["feasibility_solver_version"] is None


def test_create_plan_duplicate_conflict(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    assert client.post(f"{_BASE}/{process.id}/teaching-plan").status_code == 201
    resp = client.post(f"{_BASE}/{process.id}/teaching-plan")
    assert resp.status_code == 409


def test_create_plan_records_audit_event(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    client.post(f"{_BASE}/{process.id}/teaching-plan")
    resp = client.get(f"{_BASE}/{process.id}/audit-events/")
    assert resp.status_code == 200
    events = resp.json()["data"]
    assert any(
        e["event_type"] == "teaching_plan.created"
        and e["entity_type"] == "teaching_plan"
        for e in events
    )


def test_create_plan_superadmin_allowed(
    superuser_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = superuser_client.post(f"{_BASE}/{process.id}/teaching-plan")
    assert resp.status_code == 201


def test_create_plan_process_not_found(client: TestClient) -> None:
    resp = client.post(f"{_BASE}/{uuid.uuid4()}/teaching-plan")
    assert resp.status_code == 404


def test_create_plan_blocked_on_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(f"{_BASE}/{process.id}/teaching-plan")
    assert resp.status_code == 400


def test_create_plan_forbidden_for_reader(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    resp = reader_client.post(f"{_BASE}/{process.id}/teaching-plan")
    assert resp.status_code == 403


# ── Get ───────────────────────────────────────────────────────────────────────


def test_get_plan(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    client.post(f"{_BASE}/{process.id}/teaching-plan")
    resp = client.get(f"{_BASE}/{process.id}/teaching-plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft"
    assert body["feasibility_status"] == "not_evaluated"
    assert "created_at" in body and "updated_at" in body


def test_get_plan_none(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"{_BASE}/{process.id}/teaching-plan")
    assert resp.status_code == 404


def test_get_plan_process_not_found(client: TestClient) -> None:
    resp = client.get(f"{_BASE}/{uuid.uuid4()}/teaching-plan")
    assert resp.status_code == 404


# ── Summary (plan §3.1, §6.1, §7.3) ───────────────────────────────────────────


def test_get_summary_reports_both_balances(
    client: TestClient, session: Session
) -> None:
    """Both axes are reported side by side and never summed (plan §3.1, §3.2)."""
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    factories.make_allocation_revision(
        session, process, allocated_group_weekly_hours=6.0
    )
    profile = factories.make_teacher_profile(session)
    factories.make_process_teacher(session, process, profile, base_weekly_hours=4.0)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    factories.make_teaching_activity(
        session,
        plan,
        subject,
        group_weekly_hours_per_group=6.0,
        teacher_weekly_hours_per_position=4.0,
        group_subjects=[cell],
    )

    resp = client.get(f"{_BASE}/{process.id}/teaching-plan/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["teaching_plan_id"] == str(plan.id)
    assert body["assignment_process_id"] == str(process.id)
    # Group axis: 6.00 planned against a 6.00 leadership allocation.
    assert body["group"]["total_group_load"] == "6.00"
    assert body["group"]["allocated_group_weekly_hours"] == "6.00"
    assert body["group"]["allocation_difference"] == "0.00"
    assert body["group"]["is_balanced"] is True
    # Teacher axis: an independent 4.00 against the 4.00 participant target.
    assert body["teacher"]["total_teacher_load"] == "4.00"
    assert body["teacher"]["participant_target_total"] == "4.00"
    assert body["teacher"]["is_balanced"] is True
    assert body["is_exact"] is True


def test_get_summary_plan_not_found(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"{_BASE}/{process.id}/teaching-plan/summary")
    assert resp.status_code == 404


def test_get_summary_process_not_found(client: TestClient) -> None:
    resp = client.get(f"{_BASE}/{uuid.uuid4()}/teaching-plan/summary")
    assert resp.status_code == 404


def test_get_summary_reader_allowed(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    resp = reader_client.get(f"{_BASE}/{process.id}/teaching-plan/summary")
    assert resp.status_code == 200


# ── Validations (plan §6.3, §6.4, §7.3) ───────────────────────────────────────


def test_get_validations_reports_blocking_findings(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    resp = client.get(f"{_BASE}/{process.id}/teaching-plan/validations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["assignment_process_id"] == str(process.id)
    assert body["is_assignment_ready"] is False
    assert body["blocking_count"] >= 1
    codes = {m["code"] for m in body["messages"]}
    # A bare plan is missing its allocation and its requirement slots.
    assert "plan.missing_allocation" in codes
    assert "plan.requirements_not_generated" in codes
    assert "plan.feasibility_not_confirmed" in codes


def test_get_validations_plan_not_found(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    resp = client.get(f"{_BASE}/{process.id}/teaching-plan/validations")
    assert resp.status_code == 404


def test_get_validations_process_not_found(client: TestClient) -> None:
    resp = client.get(f"{_BASE}/{uuid.uuid4()}/teaching-plan/validations")
    assert resp.status_code == 404


def test_get_validations_reader_allowed(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    resp = reader_client.get(f"{_BASE}/{process.id}/teaching-plan/validations")
    assert resp.status_code == 200


# ── Lifecycle guard: mark_stale (plan §3.11, §9, §20.14) ──────────────────────


def test_mark_stale_from_locked_resets_feasibility(
    session: Session, current_user: UserModel
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(
        session,
        process,
        status=TeachingPlanStatus.LOCKED,
        feasibility_status=FeasibilityStatus.FEASIBLE,
    )
    result = TeachingPlanController.mark_stale(
        session, process.id, "Allocation revised", current_user
    )
    assert result.status == TeachingPlanStatus.STALE
    assert result.stale_reason == "Allocation revised"
    # Any relevant change resets feasibility to NOT_EVALUATED (plan §20.14).
    assert result.feasibility_status == FeasibilityStatus.NOT_EVALUATED

    events = session.exec(
        select(AuditEvent).where(AuditEvent.assignment_process_id == process.id)
    ).all()
    assert any(e.event_type == "teaching_plan.stale" for e in events)


def test_mark_stale_from_requirements_generated(
    session: Session, current_user: UserModel
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(
        session, process, status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    result = TeachingPlanController.mark_stale(
        session, process.id, "Reallocated", current_user
    )
    assert result.status == TeachingPlanStatus.STALE


def test_mark_stale_illegal_from_draft(
    session: Session, current_user: UserModel
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process, status=TeachingPlanStatus.DRAFT)
    with pytest.raises(HTTPException) as exc:
        TeachingPlanController.mark_stale(session, process.id, "Nope", current_user)
    assert exc.value.status_code == 409


def test_mark_stale_plan_not_found(session: Session, current_user: UserModel) -> None:
    process = factories.make_assignment_process(session)
    with pytest.raises(HTTPException) as exc:
        TeachingPlanController.mark_stale(session, process.id, "No plan", current_user)
    assert exc.value.status_code == 404


def test_apply_transition_out_of_stale_clears_reason(
    session: Session,
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(
        session,
        process,
        status=TeachingPlanStatus.STALE,
        stale_reason="was stale",
    )
    # STALE → REQUIREMENTS_GENERATED is a legal edge (plan §9, §20.14).
    TeachingPlanController.apply_status_transition(
        plan, TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED
    assert plan.stale_reason is None
