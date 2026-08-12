"""Feasibility of a plan that has not generated its slots yet (plan §20.1).

§13.6 walk-through finding. `evaluate` built the *current*-state snapshot
unconditionally. Before generation that state holds no requirement rows, so it
weighed the participant targets against zero slot hours and answered
`INFEASIBLE` with `incompatible_residual_totals` — for every plan, however
exactly balanced, and permanently: lock needs `FEASIBLE`, generation needs the
lock, and generation is the only thing that creates the slots the answer was
missing.

The evaluation therefore has to be of the state generation would produce, which
is the state `require_intended_feasible` already gates lock and generation on.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    FeasibilityStatus,
    SubjectAllocationCategory,
    TeachingPlanStatus,
)
from reparto_service.services.feasibility_witnesses import FeasibilityWitnessService
from tests import factories


def _balanced_pre_generation_process(session: Session):
    """A process balanced to 4 h/4 h whose plan has activities but no slots."""
    process = factories.make_assignment_process(session)
    factories.make_allocation_revision(
        session, process, allocated_group_weekly_hours=4.0
    )
    profile = factories.make_teacher_profile(session)
    factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0, extra_weekly_hours=0.0
    )
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session,
        process,
        allocation_category=SubjectAllocationCategory.MAIN,
        default_group_weekly_hours=4.0,
        default_teacher_weekly_hours_per_position=4.0,
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    factories.make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        group_weekly_hours_per_group=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=1,
        group_subjects=[cell],
    )
    return process, plan


def test_a_balanced_plan_is_feasible_before_its_slots_exist(
    session: Session,
) -> None:
    process, plan = _balanced_pre_generation_process(session)

    evaluation = FeasibilityWitnessService.evaluate(session, process.id)

    assert evaluation.status == FeasibilityStatus.FEASIBLE
    assert evaluation.witness_available is True
    session.expire(plan)
    stored = session.get(TeachingPlan, plan.id)
    assert stored is not None
    assert stored.feasibility_status == FeasibilityStatus.FEASIBLE


def test_the_evaluate_route_confirms_feasibility_before_generation(
    client: TestClient, session: Session
) -> None:
    process, _plan = _balanced_pre_generation_process(session)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/feasibility/evaluate"
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == FeasibilityStatus.FEASIBLE.value


def test_an_unbalanced_pre_generation_plan_is_still_infeasible(
    session: Session,
) -> None:
    """The pre-generation path must keep answering, not just say yes."""
    process, _plan = _balanced_pre_generation_process(session)
    teacher = factories.make_teacher_profile(session, display_name="Second")
    # A second participant nobody's slots can pay for: the intended state is
    # genuinely infeasible, and the evaluation has to say so.
    factories.make_process_teacher(
        session, process, teacher, base_weekly_hours=5.0, extra_weekly_hours=0.0
    )

    evaluation = FeasibilityWitnessService.evaluate(session, process.id)

    assert evaluation.status == FeasibilityStatus.INFEASIBLE


def test_after_generation_the_evaluation_returns_to_the_current_state(
    session: Session, client: TestClient
) -> None:
    """Once slots exist, the live rows are the state to evaluate."""
    process, plan = _balanced_pre_generation_process(session)
    session.expire(plan)
    balanced = session.get(TeachingPlan, plan.id)
    assert balanced is not None
    balanced.status = TeachingPlanStatus.BALANCED
    session.add(balanced)
    session.commit()

    assert (
        client.post(
            f"/reparto/assignment-processes/{process.id}/teaching-plan/lock"
        ).status_code
        == 200
    )
    generated = client.post(
        f"/reparto/assignment-processes/{process.id}/requirements/generate"
    )
    assert generated.status_code in (200, 201), generated.text

    evaluation = FeasibilityWitnessService.evaluate(session, process.id)
    assert evaluation.status == FeasibilityStatus.FEASIBLE
