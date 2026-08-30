"""A plan must reach `BALANCED` by *adding* activities (plan §20.14).

§13.6 walk-through finding. Only activity retirement and group-subject edits
recomputed the plan's balance status, so the one route that actually raises the
planned totals toward the targets — creating and materialising activities —
left the plan in `DRAFT` no matter how exact the balance became. Lock requires
`BALANCED`, so a department head who balanced a plan on the live stack could
never lock it, and no unit test noticed because they all construct a plan that
is already `BALANCED` instead of arriving there.

Each test below arrives at the state rather than declaring it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import SubjectAllocationCategory, TeachingPlanStatus
from tests import factories


def _process_with_one_exact_cell(session: Session):
    """A process whose single main cell balances a 4 h allocation exactly.

    One group of 4 group hours against a 4 h allocation, and one participant
    whose 4 h target matches the single 4 h teacher position: both balances are
    exact the moment the cell is materialised, and inexact before it.
    """
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
    return process, plan, subject, cell


def _status(session: Session, plan: TeachingPlan) -> TeachingPlanStatus:
    session.expire(plan)
    stored = session.get(TeachingPlan, plan.id)
    assert stored is not None
    return stored.status


def test_materializing_main_activities_balances_the_plan(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, _cell = _process_with_one_exact_cell(session)
    assert _status(session, plan) == TeachingPlanStatus.DRAFT

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/materialize-main"
    )
    assert resp.status_code == 200
    assert resp.json()["created_count"] == 1
    assert _status(session, plan) == TeachingPlanStatus.BALANCED


def test_creating_a_secondary_activity_unbalances_a_balanced_plan(
    client: TestClient, session: Session
) -> None:
    process, plan, subject, cell = _process_with_one_exact_cell(session)
    client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/materialize-main"
    )
    assert _status(session, plan) == TeachingPlanStatus.BALANCED

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json={
            "subject_id": str(subject.id),
            "group_weekly_hours_per_group": "1.00",
            "teacher_weekly_hours_per_position": "1.00",
            "group_subject_ids": [str(cell.id)],
        },
    )
    assert resp.status_code == 201
    # Adding hours to an exact plan must take the lock away again.
    assert _status(session, plan) == TeachingPlanStatus.UNBALANCED


def test_updating_an_activity_back_to_the_target_rebalances_the_plan(
    client: TestClient, session: Session
) -> None:
    process, plan, subject, cell = _process_with_one_exact_cell(session)
    created = client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-activities/",
        json={
            "subject_id": str(subject.id),
            "group_weekly_hours_per_group": "1.00",
            "teacher_weekly_hours_per_position": "1.00",
            "group_subject_ids": [str(cell.id)],
        },
    )
    assert created.status_code == 201
    assert _status(session, plan) == TeachingPlanStatus.UNBALANCED

    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}"
        f"/teaching-activities/{created.json()['id']}",
        json={
            "group_weekly_hours_per_group": "4.00",
            "teacher_weekly_hours_per_position": "4.00",
        },
    )
    assert resp.status_code == 200
    assert _status(session, plan) == TeachingPlanStatus.BALANCED


def test_a_balanced_plan_reached_by_materialization_can_be_locked(
    client: TestClient, session: Session
) -> None:
    """The end of the §13.6 Stage 2 walk: balance, then lock."""
    process, _plan, _subject, _cell = _process_with_one_exact_cell(session)
    client.post(
        f"/reparto/assignment-processes/{process.id}/teaching-plan/materialize-main"
    )
    resp = client.post(f"/reparto/assignment-processes/{process.id}/teaching-plan/lock")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == TeachingPlanStatus.LOCKED.value
