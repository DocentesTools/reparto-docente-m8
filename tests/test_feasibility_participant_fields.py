"""A participant edit invalidates only when it moves a solver input (§20.25).

The generic participant ``PATCH`` carries two unrelated kinds of field: the two
hour columns behind the target the solver reads, and the meeting choreography —
selection position, points, criteria label, notes, the participation flag and
the order lock — that it never sees. Invalidating on both dropped the stored
evaluation and announced *Not evaluated* the moment a head recorded a selection
order, a false alarm in the middle of a meeting.

The split is **proved against the real snapshot builder**, not restated: every
field is mutated in turn and checked against the fingerprint
:func:`build_feasibility_snapshot` produces, which is the exact input a stored
result is keyed on. A column that starts feeding the solver therefore fails
this module until it is named in ``PARTICIPANT_FEASIBILITY_INPUT_FIELDS``,
which is the guard against the real risk here — under-invalidating.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    FeasibilityStatus,
    MeetingSessionStatus,
    ProcessTeacherStatus,
    SelectionTurnStatus,
    SseEventType,
)
from reparto_service.schemas.events import DomainEvent
from reparto_service.services import sse
from reparto_service.services.feasibility_witnesses import (
    PARTICIPANT_FEASIBILITY_INPUT_FIELDS,
    build_feasibility_snapshot,
    participant_change_affects_feasibility,
)
from tests import factories

PREFIX = "/reparto/assignment-processes"

#: Every ``ProcessTeacher`` column a caller can change, paired with a value that
#: differs from the factory default, so the parametrisation below covers the row
#: exhaustively rather than sampling it.
MUTABLE_FIELDS: dict[str, Any] = {
    "base_weekly_hours": Decimal("20.00"),
    "extra_weekly_hours": Decimal("2.00"),
    "status": ProcessTeacherStatus.INACTIVE,
    "participates_in_selection": False,
    "selection_position": 3,
    "selection_points": 12.5,
    "selection_criteria_label": "Seniority",
    "selection_notes": "Asked to go last",
    "order_locked": True,
}


@pytest.fixture
def subscribe():
    """Attach a real subscription to the process-wide broker, detached on teardown."""
    created: list[sse.Subscription] = []

    def _subscribe(process_id: uuid.UUID) -> sse.Subscription:
        subscription = sse.event_broker.subscribe(process_id)
        created.append(subscription)
        return subscription

    yield _subscribe
    for subscription in created:
        subscription.close()


def _store_result(session: Session, plan: TeachingPlan) -> None:
    """Give the plan a stored evaluation, so an invalidation is a real transition."""
    plan.feasibility_status = FeasibilityStatus.FEASIBLE
    plan.feasibility_generation = plan.current_generation_number
    plan.feasibility_input_fingerprint = "f" * 64
    plan.feasibility_solver_version = "bounded-dfs-v1"
    session.add(plan)
    session.commit()


def _invalidations(subscription: sse.Subscription) -> list[DomainEvent]:
    events, dropped = subscription.drain()
    assert dropped == 0
    return [
        event
        for event in events
        if event.event_type == SseEventType.TEACHING_PLAN_FEASIBILITY_INVALIDATED
    ]


def _participant(session: Session):
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    profile = factories.make_teacher_profile(session)
    participant = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=18.0
    )
    return process, plan, participant


# ── The dependent-field list, derived from the snapshot builder ───────────────


@pytest.mark.parametrize("field", sorted(MUTABLE_FIELDS))
def test_the_fingerprint_moves_exactly_for_the_declared_fields(
    session: Session, field: str
) -> None:
    """Only the declared fields change what a stored result is keyed on."""
    process, _plan, participant = _participant(session)
    before = build_feasibility_snapshot(session, process.id).fingerprint

    setattr(participant, field, MUTABLE_FIELDS[field])
    session.add(participant)
    session.commit()

    after = build_feasibility_snapshot(session, process.id).fingerprint
    assert (before != after) is (field in PARTICIPANT_FEASIBILITY_INPUT_FIELDS)


@pytest.mark.parametrize("field", sorted(MUTABLE_FIELDS))
def test_the_predicate_agrees_with_the_fingerprint(
    session: Session, field: str
) -> None:
    """The predicate the controller consults says the same thing."""
    _process, _plan, participant = _participant(session)
    before = ProcessTeacher.model_validate(participant.model_dump())

    setattr(participant, field, MUTABLE_FIELDS[field])

    assert participant_change_affects_feasibility(before, participant) is (
        field in PARTICIPANT_FEASIBILITY_INPUT_FIELDS
    )


def test_an_unchanged_value_is_not_a_change(session: Session) -> None:
    """Re-sending the current target hours must not drop the evaluation."""
    _process, _plan, participant = _participant(session)
    before = ProcessTeacher.model_validate(participant.model_dump())

    participant.base_weekly_hours = Decimal("18.000")

    assert participant_change_affects_feasibility(before, participant) is False


# ── The participant endpoint (plan §7.6) ──────────────────────────────────────


def test_recording_a_selection_position_keeps_the_evaluation(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, participant = _participant(session)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.patch(
        f"{PREFIX}/{process.id}/teachers/{participant.id}",
        json={
            "selection_position": 3,
            "selection_points": 12.5,
            "selection_criteria_label": "Seniority",
            "selection_notes": "Asked to go last",
            "order_locked": True,
            "participates_in_selection": False,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["selection_position"] == 3
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.FEASIBLE
    assert plan.feasibility_input_fingerprint == "f" * 64
    assert _invalidations(subscription) == []


def test_changing_the_base_hours_still_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, participant = _participant(session)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.patch(
        f"{PREFIX}/{process.id}/teachers/{participant.id}",
        json={"base_weekly_hours": "20.00", "selection_position": 3},
    )

    assert resp.status_code == 200
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED
    assert len(_invalidations(subscription)) == 1


def test_deactivating_a_participant_still_invalidates(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, participant = _participant(session)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.patch(
        f"{PREFIX}/{process.id}/teachers/{participant.id}",
        json={"status": "inactive"},
    )

    assert resp.status_code == 200
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED
    assert len(_invalidations(subscription)) == 1


def test_a_patch_that_changes_nothing_keeps_the_evaluation(
    client: TestClient, session: Session, subscribe
) -> None:
    process, plan, participant = _participant(session)
    _store_result(session, plan)
    subscription = subscribe(process.id)

    resp = client.patch(
        f"{PREFIX}/{process.id}/teachers/{participant.id}",
        json={"base_weekly_hours": "18.00"},
    )

    assert resp.status_code == 200
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.FEASIBLE
    assert _invalidations(subscription) == []


# ── A meeting from order to assignment (plan §7.7) ────────────────────


def test_a_simulated_meeting_never_drops_to_not_evaluated(
    admin_client: TestClient, session: Session, subscribe
) -> None:
    """Evaluate, record the order, then run every turn to its assignment.

    The evaluation here is the real one rather than a planted row, because the
    assignment hot path loads the witness it produced: a meeting that dropped to
    *Not evaluated* halfway would not merely look wrong on the shared screen, it
    would take the bounded local repair away from the remaining turns.
    """
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(
        session,
        plan,
        subject,
        required_teacher_count=2,
        teacher_weekly_hours_per_position=4.0,
    )
    requirements = [
        factories.make_hour_requirement(
            session,
            process,
            activity,
            position_index=index,
            required_teacher_hours=4.0,
        )
        for index in range(2)
    ]
    participants = [
        factories.make_process_teacher(
            session,
            process,
            factories.make_teacher_profile(session, display_name=name),
            base_weekly_hours=4.0,
        )
        for name in ("A", "B")
    ]
    meeting = factories.make_meeting_session(
        session, process, status=MeetingSessionStatus.OPEN
    )

    evaluate = admin_client.post(
        f"{PREFIX}/{process.id}/teaching-plan/feasibility/evaluate"
    )
    assert evaluate.status_code == 200
    assert evaluate.json()["status"] == FeasibilityStatus.FEASIBLE.value
    subscription = subscribe(process.id)

    # The head records the agreed order on the participant rows themselves.
    for position, participant in enumerate(participants):
        resp = admin_client.patch(
            f"{PREFIX}/{process.id}/teachers/{participant.id}",
            json={
                "selection_position": position,
                "selection_points": 10.0 - position,
                "selection_criteria_label": "Seniority",
            },
        )
        assert resp.status_code == 200

    turns_path = f"{PREFIX}/{process.id}/meeting-sessions/{meeting.id}/turns"
    resp = admin_client.post(f"{turns_path}/initialize")
    assert resp.status_code == 201
    turns = resp.json()["data"]
    assert len(turns) == 2

    for turn, participant, requirement in zip(turns, participants, requirements):
        assert admin_client.post(f"{turns_path}/{turn['id']}/start").status_code == 200
        resp = admin_client.post(
            f"{turns_path}/{turn['id']}/complete",
            json={
                "assignment": {
                    "hour_requirement_id": str(requirement.id),
                    "process_teacher_id": str(participant.id),
                }
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == SelectionTurnStatus.COMPLETED.value

    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.FEASIBLE
    assert _invalidations(subscription) == []
