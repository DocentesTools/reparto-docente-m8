"""Tests for the dual planning and assignment calculation services (plan §6.1, §6.2).

Covers the per-activity formulas, the two plan-wide balances (including the
plan §3.2 co-teaching example that must land on exactly 120 group hours / 124
teacher-load hours), and the per-participant / per-requirement / process-wide
assignment calculations, plus the canonical decimal-string serialisation of the
planning schemas.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlmodel import Session

from reparto_service.enums import (
    AssignmentStatus,
    HourRequirementStatus,
    ParticipantBalanceState,
    ProcessTeacherStatus,
    SubjectAllocationCategory,
)
from reparto_service.schemas.planning import GroupBalance, ParticipantBalance
from reparto_service.services.calculations import (
    AssignmentCalculationService,
    PlanningCalculationService,
    _dec,
)
from tests.factories import (
    make_allocation_revision,
    make_assignment,
    make_assignment_process,
    make_group_subject,
    make_hour_requirement,
    make_process_teacher,
    make_subject,
    make_teacher_profile,
    make_teaching_activity,
    make_teaching_group,
    make_teaching_plan,
)

PLANNING = PlanningCalculationService
ASSIGN = AssignmentCalculationService

# Monotonic id so every group/subject a helper makes is unique within a process
# (teaching-group label and per-process rows are uniqueness-constrained).
_COUNTER = iter(range(1, 10_000))


def _uid() -> int:
    return next(_COUNTER)


# ── helpers ───────────────────────────────────────────────────────────────────


def _process_with_plan(session: Session):
    process = make_assignment_process(session)
    plan = make_teaching_plan(session, process)
    return process, plan


def _linked_activity(
    session,
    plan,
    process,
    *,
    group_weekly_hours_per_group: float,
    teacher_weekly_hours_per_position: float,
    required_teacher_count: int,
    group_count: int = 1,
    retired_at=None,
):
    """Create an activity linked to ``group_count`` fresh group-subject cells."""
    subject = make_subject(session, process, name=f"Subj-{_uid()}")
    cells = []
    for _ in range(group_count):
        group = make_teaching_group(session, process, group_code=f"G{_uid()}")
        cells.append(make_group_subject(session, process, group, subject))
    activity = make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        group_weekly_hours_per_group=group_weekly_hours_per_group,
        teacher_weekly_hours_per_position=teacher_weekly_hours_per_position,
        required_teacher_count=required_teacher_count,
        group_subjects=cells,
    )
    if retired_at is not None:
        activity.retired_at = retired_at
        session.add(activity)
        session.commit()
        session.refresh(activity)
    return activity


# ── per-activity formulas (plan §6.1) ──────────────────────────────────────────


def test_activity_group_load_counts_hours_once_per_group(session: Session):
    _, plan = _process_with_plan(session)
    process = make_assignment_process(session)
    activity = _linked_activity(
        session,
        plan,
        process,
        group_weekly_hours_per_group=2.0,
        teacher_weekly_hours_per_position=2.0,
        required_teacher_count=1,
        group_count=2,
    )
    # 2h × 2 groups = 4 group hours (plan §3.4).
    assert PLANNING.compute_activity_group_load(activity, 2) == Decimal("4.00")


def test_activity_teacher_load_multiplies_positions(session: Session):
    _, plan = _process_with_plan(session)
    process = make_assignment_process(session)
    activity = _linked_activity(
        session,
        plan,
        process,
        group_weekly_hours_per_group=2.0,
        teacher_weekly_hours_per_position=2.0,
        required_teacher_count=2,
        group_count=1,
    )
    # 2h × 2 positions = 4 teacher-load hours (plan §3.1).
    assert PLANNING.compute_activity_teacher_load(activity) == Decimal("4.00")


# ── plan-wide co-teaching example: 120 group / 124 teacher (plan §3.2) ──────────


def test_plan_balance_confirms_120_124_coteaching_example(session: Session):
    process, plan = _process_with_plan(session)
    make_allocation_revision(session, process, allocated_group_weekly_hours=120.0)
    # Main subject: 116 group hours, 116 teacher hours (one position, one group).
    _linked_activity(
        session,
        plan,
        process,
        group_weekly_hours_per_group=116.0,
        teacher_weekly_hours_per_position=116.0,
        required_teacher_count=1,
    )
    # Two co-teaching activities: 2 group hours each (4), 2h × 2 teachers each (8).
    for _ in range(2):
        _linked_activity(
            session,
            plan,
            process,
            group_weekly_hours_per_group=2.0,
            teacher_weekly_hours_per_position=2.0,
            required_teacher_count=2,
        )
    # Participant targets sum to 124 (2 × 62).
    for name in ("Ana", "Beto"):
        profile = make_teacher_profile(session, display_name=name)
        make_process_teacher(session, process, profile, base_weekly_hours=62.0)

    balance = PLANNING.compute_plan_balance(session, plan)

    assert balance.group.total_group_load == Decimal("120.00")
    assert balance.group.allocated_group_weekly_hours == Decimal("120.00")
    assert balance.group.allocation_difference == Decimal("0.00")
    assert balance.group.is_balanced is True
    assert balance.teacher.total_teacher_load == Decimal("124.00")
    assert balance.teacher.participant_target_total == Decimal("124.00")
    assert balance.teacher.teacher_load_difference == Decimal("0.00")
    assert balance.teacher.is_balanced is True
    # Both balances exact even though 120 != 124 (plan §3.2 — both correct).
    assert balance.is_exact is True


def test_retired_activity_excluded_from_totals(session: Session):
    from datetime import datetime, timezone

    process, plan = _process_with_plan(session)
    _linked_activity(
        session,
        plan,
        process,
        group_weekly_hours_per_group=10.0,
        teacher_weekly_hours_per_position=10.0,
        required_teacher_count=1,
    )
    _linked_activity(
        session,
        plan,
        process,
        group_weekly_hours_per_group=5.0,
        teacher_weekly_hours_per_position=5.0,
        required_teacher_count=1,
        retired_at=datetime.now(tz=timezone.utc),
    )
    assert PLANNING.compute_total_group_load(session, plan) == Decimal("10.00")
    assert PLANNING.compute_total_teacher_load(session, plan) == Decimal("10.00")


# ── allocation resolution & differences ─────────────────────────────────────────


def test_current_allocation_ignores_superseded_and_missing(session: Session):
    process, plan = _process_with_plan(session)
    assert PLANNING.compute_current_allocation(session, process.id) is None
    from datetime import datetime, timezone

    make_allocation_revision(
        session,
        process,
        revision_number=1,
        allocated_group_weekly_hours=100.0,
        superseded_at=datetime.now(tz=timezone.utc),
    )
    make_allocation_revision(
        session, process, revision_number=2, allocated_group_weekly_hours=130.0
    )
    assert PLANNING.compute_current_allocation(session, process.id) == Decimal("130.00")


def test_group_allocation_difference_none_without_allocation(session: Session):
    process, plan = _process_with_plan(session)
    _linked_activity(
        session,
        plan,
        process,
        group_weekly_hours_per_group=8.0,
        teacher_weekly_hours_per_position=8.0,
        required_teacher_count=1,
    )
    assert PLANNING.compute_group_allocation_difference(session, plan) is None
    make_allocation_revision(session, process, allocated_group_weekly_hours=5.0)
    assert PLANNING.compute_group_allocation_difference(session, plan) == Decimal(
        "3.00"
    )


def test_participant_target_total_active_only(session: Session):
    process, plan = _process_with_plan(session)
    active = make_teacher_profile(session, display_name="Active")
    make_process_teacher(
        session, process, active, base_weekly_hours=18.0, extra_weekly_hours=2.0
    )
    inactive = make_teacher_profile(session, display_name="Inactive")
    make_process_teacher(
        session,
        process,
        inactive,
        base_weekly_hours=99.0,
        status=ProcessTeacherStatus.INACTIVE,
    )
    # Only the active participant's base+extra (20) counts.
    assert PLANNING.compute_participant_target_total(session, process.id) == Decimal(
        "20.00"
    )


def test_teacher_load_difference_signed(session: Session):
    process, plan = _process_with_plan(session)
    _linked_activity(
        session,
        plan,
        process,
        group_weekly_hours_per_group=1.0,
        teacher_weekly_hours_per_position=10.0,
        required_teacher_count=1,
    )
    profile = make_teacher_profile(session)
    make_process_teacher(session, process, profile, base_weekly_hours=18.0)
    # load 10 − target 18 = -8 (signed).
    assert PLANNING.compute_teacher_load_difference(session, plan) == Decimal("-8.00")


def test_plan_balance_unbalanced_without_allocation(session: Session):
    process, plan = _process_with_plan(session)
    _linked_activity(
        session,
        plan,
        process,
        group_weekly_hours_per_group=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=1,
    )
    balance = PLANNING.compute_plan_balance(session, plan)
    assert balance.group.allocation_difference is None
    assert balance.group.is_balanced is False
    # No participants → teacher target 0, load 4 → not balanced.
    assert balance.teacher.is_balanced is False
    assert balance.is_exact is False


# ── assignment calculations (plan §6.2) ─────────────────────────────────────────


def _assigned_teacher_scenario(session, process, plan, *, hours: float):
    subject = make_subject(session, process, name=f"AS-{_uid()}")
    group = make_teaching_group(session, process, group_code=f"A{_uid()}")
    cell = make_group_subject(session, process, group, subject)
    activity = make_teaching_activity(
        session,
        plan,
        subject,
        teacher_weekly_hours_per_position=hours,
        required_teacher_count=1,
        group_subjects=[cell],
    )
    requirement = make_hour_requirement(
        session, process, activity, required_teacher_hours=hours
    )
    return activity, requirement


def test_participant_assigned_and_remaining_hours(session: Session):
    process, plan = _process_with_plan(session)
    profile = make_teacher_profile(session)
    teacher = make_process_teacher(session, process, profile, base_weekly_hours=18.0)

    _, req_a = _assigned_teacher_scenario(session, process, plan, hours=4.0)
    _, req_b = _assigned_teacher_scenario(session, process, plan, hours=6.0)
    make_assignment(session, process, req_a, teacher)
    make_assignment(session, process, req_b, teacher)
    # A cancelled assignment on a third slot must not count.
    _, req_c = _assigned_teacher_scenario(session, process, plan, hours=5.0)
    make_assignment(session, process, req_c, teacher, status=AssignmentStatus.CANCELLED)

    assert ASSIGN.compute_participant_assigned_hours(session, teacher) == Decimal(
        "10.00"
    )
    assert ASSIGN.compute_participant_remaining_hours(session, teacher) == Decimal(
        "8.00"
    )


@pytest.mark.parametrize(
    ("status", "participates", "extra", "remaining", "expected"),
    [
        (
            ProcessTeacherStatus.INACTIVE,
            True,
            0.0,
            Decimal("5"),
            ParticipantBalanceState.INACTIVE,
        ),
        (
            ProcessTeacherStatus.ACTIVE,
            False,
            0.0,
            Decimal("5"),
            ParticipantBalanceState.NOT_PARTICIPATING,
        ),
        (
            ProcessTeacherStatus.ACTIVE,
            True,
            2.0,
            Decimal("5"),
            ParticipantBalanceState.OVERLOADED_AUTHORIZED,
        ),
        (
            ProcessTeacherStatus.ACTIVE,
            True,
            0.0,
            Decimal("5"),
            ParticipantBalanceState.PENDING,
        ),
        (
            ProcessTeacherStatus.ACTIVE,
            True,
            0.0,
            Decimal("0"),
            ParticipantBalanceState.BALANCED,
        ),
    ],
)
def test_participant_state_precedence(
    session: Session, status, participates, extra, remaining, expected
):
    process, _ = _process_with_plan(session)
    profile = make_teacher_profile(session)
    teacher = make_process_teacher(
        session,
        process,
        profile,
        base_weekly_hours=18.0,
        extra_weekly_hours=extra,
        status=status,
        participates_in_selection=participates,
    )
    assert ASSIGN.compute_participant_state(teacher, remaining) == expected


def test_requirement_state_available_assigned_and_generation_states(session: Session):
    process, plan = _process_with_plan(session)
    profile = make_teacher_profile(session)
    teacher = make_process_teacher(session, process, profile)

    _, available = _assigned_teacher_scenario(session, process, plan, hours=4.0)
    assert (
        ASSIGN.compute_requirement_state(session, available)
        == HourRequirementStatus.AVAILABLE
    )

    _, assigned = _assigned_teacher_scenario(session, process, plan, hours=4.0)
    make_assignment(session, process, assigned, teacher)
    assert (
        ASSIGN.compute_requirement_state(session, assigned)
        == HourRequirementStatus.ASSIGNED
    )

    # A CANCELLED assignment leaves the slot AVAILABLE.
    _, freed = _assigned_teacher_scenario(session, process, plan, hours=4.0)
    make_assignment(session, process, freed, teacher, status=AssignmentStatus.CANCELLED)
    assert (
        ASSIGN.compute_requirement_state(session, freed)
        == HourRequirementStatus.AVAILABLE
    )

    _, stale = _assigned_teacher_scenario(session, process, plan, hours=4.0)
    stale.status = HourRequirementStatus.STALE
    session.add(stale)
    session.commit()
    assert (
        ASSIGN.compute_requirement_state(session, stale) == HourRequirementStatus.STALE
    )

    _, recon = _assigned_teacher_scenario(session, process, plan, hours=4.0)
    recon.status = HourRequirementStatus.RECONCILIATION_REQUIRED
    session.add(recon)
    session.commit()
    assert (
        ASSIGN.compute_requirement_state(session, recon)
        == HourRequirementStatus.RECONCILIATION_REQUIRED
    )


def test_assignment_summary_totals_states_and_slots(session: Session):
    process, plan = _process_with_plan(session)

    # Active participant "Bruno" fully at target (18 base, 18 assigned).
    bruno = make_teacher_profile(session, display_name="Bruno")
    bruno_pt = make_process_teacher(session, process, bruno, base_weekly_hours=18.0)
    _, req_full = _assigned_teacher_scenario(session, process, plan, hours=18.0)
    make_assignment(session, process, req_full, bruno_pt)

    # Active participant "Alba" pending (18 base, 0 assigned) — sorts before Bruno.
    alba = make_teacher_profile(session, display_name="Alba")
    make_process_teacher(session, process, alba, base_weekly_hours=18.0)

    # Inactive participant excluded from the target total.
    carol = make_teacher_profile(session, display_name="Carol")
    make_process_teacher(
        session,
        process,
        carol,
        base_weekly_hours=99.0,
        status=ProcessTeacherStatus.INACTIVE,
    )

    # One extra live-but-unassigned slot, and one retired slot (excluded).
    _assigned_teacher_scenario(session, process, plan, hours=3.0)
    _, retired = _assigned_teacher_scenario(session, process, plan, hours=7.0)
    retired.retired_generation = 1
    session.add(retired)
    session.commit()

    summary = ASSIGN.compute_assignment_summary(session, process)

    # Totals over ACTIVE participants only: 18 + 18 = 36 target, 18 assigned.
    assert summary.total_target_hours == Decimal("36.00")
    assert summary.total_assigned_hours == Decimal("18.00")
    assert summary.total_remaining_hours == Decimal("18.00")
    # Live slots: req_full (assigned) + unassigned = 2; retired excluded.
    assert summary.total_slots == 2
    assert summary.assigned_slots == 1
    assert summary.available_slots == 1
    # Ordered by display name: Alba, Bruno, Carol.
    assert [p.display_name for p in summary.participants] == ["Alba", "Bruno", "Carol"]
    alba_row = summary.participants[0]
    bruno_row = summary.participants[1]
    carol_row = summary.participants[2]
    assert alba_row.state == ParticipantBalanceState.PENDING
    assert alba_row.assignment_count == 0
    assert bruno_row.state == ParticipantBalanceState.BALANCED
    assert bruno_row.assigned_weekly_hours == Decimal("18.00")
    assert bruno_row.assignment_count == 1
    assert carol_row.state == ParticipantBalanceState.INACTIVE


# ── schema serialisation (plan §3.9 canonical decimal strings) ──────────────────


def test_dec_helper_normalizes_decimal_and_float():
    assert _dec(2) == Decimal("2.00")
    assert _dec(Decimal("2.5")) == Decimal("2.50")


def test_hours_field_serialises_canonical_strings():
    # Constructed from an int and a float to exercise the lenient coercion.
    balance = GroupBalance(
        total_group_load=120,
        allocated_group_weekly_hours=120.0,
        allocation_difference=Decimal("-0.00"),
        is_balanced=True,
    )
    # Python mode keeps a real Decimal for arithmetic.
    assert balance.total_group_load == Decimal("120.00")
    dumped = balance.model_dump(mode="json")
    assert dumped["total_group_load"] == "120.00"
    assert dumped["allocated_group_weekly_hours"] == "120.00"
    # Negative-zero difference collapses to the canonical "0.00".
    assert dumped["allocation_difference"] == "0.00"


def test_optional_hours_field_handles_none():
    balance = GroupBalance(
        total_group_load=Decimal("4.00"),
        allocated_group_weekly_hours=None,
        allocation_difference=None,
        is_balanced=False,
    )
    dumped = balance.model_dump(mode="json")
    assert dumped["allocated_group_weekly_hours"] is None
    assert dumped["allocation_difference"] is None


def test_signed_hours_field_serialises_negative():
    row = ParticipantBalance(
        process_teacher_id=__import__("uuid").uuid4(),
        teacher_profile_id=__import__("uuid").uuid4(),
        display_name="Neg",
        base_weekly_hours=Decimal("18.00"),
        extra_weekly_hours=Decimal("0.00"),
        target_weekly_hours=Decimal("18.00"),
        assigned_weekly_hours=Decimal("20.00"),
        remaining_weekly_hours=Decimal("-2.00"),
        is_overloaded=False,
        assignment_count=2,
        state=ParticipantBalanceState.BALANCED,
    )
    assert row.model_dump(mode="json")["remaining_weekly_hours"] == "-2.00"
