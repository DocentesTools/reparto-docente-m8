"""Gate for the seeded configuration example (``reparto_service.initial_data``).

The module's whole claim is arithmetic: configure *this* department and the two
balances come out exact, at 120 group hours and 124 teacher hours, on the plan
§3.2 co-teaching numbers. A fixture that only inserted rows would prove nothing,
so the central test below completes stages 2 and 3's entry point over the seed —
materialising the main activities through the real controller and adding the
three secondary ones — and asks the calculation service for the result.

The rest guards the properties a seed needs to be safe: it never runs against a
populated domain, it is one transaction, and it invents no identity.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

import pytest
from auth_sdk_m8.schemas.user import UserModel
from sqlmodel import Session, func, select

from reparto_service import initial_data
from reparto_service.controllers.teaching_activities import TeachingActivityController
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.classroom_stages import ClassroomStage
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.schools import School
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityCreate,
)
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.enums import (
    ActivityType,
    SubjectAllocationCategory,
    TeachingActivitySource,
)
from reparto_service.services.calculations import PlanningCalculationService
from tests.factories import make_assignment_process, make_teaching_plan

PLANNING = PlanningCalculationService


def _count(session: Session, model: type) -> int:
    return session.exec(select(func.count()).select_from(model)).one()


# ── Stage 1: what the seed inserts ───────────────────────────────────────────


def test_seeding_an_empty_domain_configures_one_process(session: Session) -> None:
    """The seed is a complete stage-1 configuration and stops there."""
    process = initial_data.seed_example_configuration(session)

    assert process is not None
    assert _count(session, School) == 1
    assert _count(session, AssignmentProcess) == 1
    assert _count(session, ClassroomStage) == len(initial_data.CLASSROOM_STAGES)
    assert _count(session, TeachingGroup) == len(initial_data.TEACHING_GROUPS)
    assert _count(session, Subject) == len(initial_data.SUBJECTS)
    assert _count(session, ProcessTeacher) == len(initial_data.PARTICIPANTS)
    assert _count(session, TeacherProfile) == len(initial_data.PARTICIPANTS)
    assert _count(session, GroupSubject) == sum(
        len(labels) for *_, labels in initial_data.SUBJECTS
    )
    assert _count(session, DepartmentHourAllocationRevision) == 1

    # Stages 2 and 3 are the walk-through, not the fixture.
    assert _count(session, TeachingActivity) == 0


def test_the_seed_claims_no_real_identity(session: Session) -> None:
    """Seeded profiles stay unclaimed and the author is recognisably synthetic."""
    initial_data.seed_example_configuration(session)

    assert all(
        profile.user_id is None
        for profile in session.exec(select(TeacherProfile)).all()
    )
    assert initial_data.SEED_USER_ID == uuid.uuid5(
        uuid.NAMESPACE_DNS, "seed.reparto-docente-m8.example"
    )


def test_participant_targets_total_the_teacher_load(session: Session) -> None:
    """The six participants are the 124 h the plan will cost — exactly."""
    initial_data.seed_example_configuration(session)

    teachers = session.exec(select(ProcessTeacher)).all()
    assert sum(teacher.base_weekly_hours for teacher in teachers) == (
        initial_data.EXPECTED_TEACHER_LOAD
    )
    # An authorized overload is an audited action, never a seeded value.
    assert all(teacher.extra_weekly_hours == 0.0 for teacher in teachers)


def test_cells_resolve_their_hours_through_the_subject_defaults(
    session: Session,
) -> None:
    """Cells carry only the position count; hours come from the subject."""
    initial_data.seed_example_configuration(session)

    cells = session.exec(select(GroupSubject)).all()
    assert cells
    assert all(cell.group_weekly_hours is None for cell in cells)
    assert all(cell.teacher_weekly_hours_per_position is None for cell in cells)
    assert all(cell.required_teacher_count >= 1 for cell in cells)


# ── Stages 2–3: the balances the seed exists to produce ──────────────────────


def _complete_the_plan(session: Session, process: AssignmentProcess, user: UserModel):
    """Materialise the main activities and add the three secondary ones."""
    plan = make_teaching_plan(session, process)
    TeachingActivityController.materialize_main(session, process.id, user)

    for subject in session.exec(
        select(Subject)
        .where(Subject.assignment_process_id == process.id)
        .where(Subject.allocation_category == SubjectAllocationCategory.SECONDARY)
        .order_by(Subject.name)
    ).all():
        for cell in session.exec(
            select(GroupSubject).where(GroupSubject.subject_id == subject.id)
        ).all():
            TeachingActivityController.create_teaching_activity(
                session,
                process.id,
                TeachingActivityCreate(
                    subject_id=subject.id,
                    allocation_category=SubjectAllocationCategory.SECONDARY,
                    activity_type=subject.activity_type,
                    group_weekly_hours_per_group=(
                        subject.default_group_weekly_hours or 0.0
                    ),
                    teacher_weekly_hours_per_position=(
                        subject.default_teacher_weekly_hours_per_position or 0.0
                    ),
                    required_teacher_count=subject.default_required_teacher_count,
                    source=TeachingActivitySource.SECONDARY_MANUAL,
                    group_subject_ids=[cell.id],
                ),
                user,
            )
    session.refresh(plan)
    return plan


def test_the_seeded_example_balances_at_120_group_and_124_teacher_hours(
    session: Session, admin_user: UserModel
) -> None:
    """Plan §3.2: both balances exact at once, at two different numbers.

    This is the claim ``reparto_service.initial_data`` makes in prose. Nothing
    here recomputes it by hand — the activities come from the real
    materialisation and creation paths, and the totals from the calculation
    service the dashboard reads.
    """
    process = initial_data.seed_example_configuration(session)
    assert process is not None

    plan = _complete_the_plan(session, process, admin_user)
    balance = PLANNING.compute_plan_balance(session, plan)

    assert balance.group.total_group_load == Decimal("120.00")
    assert balance.group.allocated_group_weekly_hours == Decimal("120.00")
    assert balance.group.allocation_difference == Decimal("0.00")
    assert balance.group.is_balanced is True

    assert balance.teacher.total_teacher_load == Decimal("124.00")
    assert balance.teacher.participant_target_total == Decimal("124.00")
    assert balance.teacher.teacher_load_difference == Decimal("0.00")
    assert balance.teacher.is_balanced is True

    assert balance.is_exact is True


def test_the_surplus_comes_from_tutoring_and_co_teaching(
    session: Session, admin_user: UserModel
) -> None:
    """The 4 h gap between the two balances is the second-position hours.

    Guards the guard above: 120 and 124 would also both be "exact" if the
    secondary activities were missing and the numbers had been tuned to match,
    which is precisely the shape of example this item asked not to ship.
    """
    process = initial_data.seed_example_configuration(session)
    assert process is not None
    _complete_the_plan(session, process, admin_user)

    secondary = session.exec(
        select(TeachingActivity).where(
            TeachingActivity.allocation_category == SubjectAllocationCategory.SECONDARY
        )
    ).all()

    assert {activity.activity_type for activity in secondary} == {
        ActivityType.TUTORING,
        ActivityType.CO_TEACHING,
    }
    assert all(activity.required_teacher_count == 2 for activity in secondary)
    assert sum(
        PLANNING.compute_activity_teacher_load(activity)
        - PLANNING.compute_activity_group_load(activity, 1)
        for activity in secondary
    ) == Decimal("4.00")


# ── Safety: never against a populated domain, never partially ────────────────


def test_a_populated_domain_is_left_untouched(session: Session) -> None:
    """A process somebody else created is the stop condition."""
    make_assignment_process(session)

    assert initial_data.domain_is_empty(session) is False
    assert initial_data.seed_example_configuration(session) is None
    assert _count(session, School) == 1  # the factory's, not the seed's
    assert _count(session, Subject) == 0


def test_seeding_twice_inserts_the_example_once(session: Session) -> None:
    """The second Compose start must be a no-op."""
    assert initial_data.seed_example_configuration(session) is not None
    assert initial_data.seed_example_configuration(session) is None

    assert _count(session, AssignmentProcess) == 1
    assert _count(session, TeachingGroup) == len(initial_data.TEACHING_GROUPS)


def test_existing_classroom_stages_are_reused(session: Session) -> None:
    """Stages are global and uniquely keyed, so the seed adopts what is there."""
    stage, label, min_grade, max_grade = initial_data.CLASSROOM_STAGES[0]
    session.add(
        ClassroomStage(
            stage=stage, label=label, min_grade=min_grade, max_grade=max_grade
        )
    )
    session.commit()

    initial_data.seed_example_configuration(session)

    assert _count(session, ClassroomStage) == len(initial_data.CLASSROOM_STAGES)


# ── Entry point ──────────────────────────────────────────────────────────────


def test_main_does_nothing_while_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Off by default: a deployment must never invent rows."""
    monkeypatch.setattr(initial_data.settings, "SEED_EXAMPLE_DATA", False)

    def _no_session():  # pragma: no cover - asserted by not being called
        raise AssertionError("the engine must not be touched while the flag is off")

    monkeypatch.setattr(initial_data.engine, "session", _no_session)

    with caplog.at_level(logging.INFO):
        initial_data.main()

    assert "leaving the domain untouched" in caplog.text


def test_main_seeds_through_the_engine_session(
    session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With the flag on, the example lands and the process id is reported."""
    monkeypatch.setattr(initial_data.settings, "SEED_EXAMPLE_DATA", True)
    monkeypatch.setattr(
        initial_data.engine, "session", lambda: _NonClosingSession(session)
    )

    with caplog.at_level(logging.INFO):
        initial_data.main()

    assert _count(session, AssignmentProcess) == 1
    assert "Seeded the" in caplog.text


def test_main_reports_an_already_populated_domain(
    session: Session, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The operator is told why nothing happened, rather than left guessing."""
    make_assignment_process(session)
    monkeypatch.setattr(initial_data.settings, "SEED_EXAMPLE_DATA", True)
    monkeypatch.setattr(
        initial_data.engine, "session", lambda: _NonClosingSession(session)
    )

    with caplog.at_level(logging.INFO):
        initial_data.main()

    assert "nothing seeded" in caplog.text


class _NonClosingSession:
    """Hand ``main()`` the test session without letting it be closed."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *_: object) -> None:
        """Leave the session open — the fixture owns its lifetime."""
