"""Tests for the planning-stage validation service (plan §6.3, §6.4).

Covers every blocking finding (missing allocation, group imbalance, teacher-load
imbalance, main-subject not materialized, the three activity/group-link
problems, requirements not generated / stale, plan stale, feasibility not
confirmed) and the two warnings (authorized-extra participant, unmaterialized
secondary cells), plus the aggregate report (assignment-readiness, counts,
blocking-before-warning ordering). No feasibility solve is ever triggered
(plan §20.23): the stored ``feasibility_status`` is only read.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from reparto_service.enums import (
    FeasibilityStatus,
    HourRequirementStatus,
    ProcessTeacherStatus,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingPlanStatus,
    ValidationSeverity,
)
from reparto_service.schemas.planning import PlanValidationReport
from reparto_service.services.validations import (
    CODE_ACTIVITY_LINKED_SUBJECT_MISMATCH,
    CODE_ACTIVITY_MISSING_GROUPS,
    CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED,
    CODE_FEASIBILITY_NOT_CONFIRMED,
    CODE_GROUP_HOURS_IMBALANCED,
    CODE_MAIN_SUBJECT_NOT_MATERIALIZED,
    CODE_MISSING_ALLOCATION,
    CODE_PARTICIPANT_OVERLOADED,
    CODE_PLAN_STALE,
    CODE_REQUIREMENTS_NOT_GENERATED,
    CODE_REQUIREMENTS_STALE,
    CODE_SECONDARY_ACTIVITIES_AVAILABLE,
    CODE_TEACHER_LOAD_IMBALANCED,
    PlanValidationService,
)
from tests.factories import (
    make_allocation_revision,
    make_assignment_process,
    make_group_subject,
    make_hour_requirement,
    make_process_teacher,
    make_subject,
    make_teacher_profile,
    make_teaching_activity,
    make_teaching_activity_group,
    make_teaching_group,
    make_teaching_plan,
)

SERVICE = PlanValidationService

_COUNTER = iter(range(1, 100_000))


def _uid() -> int:
    return next(_COUNTER)


def _codes(report: PlanValidationReport) -> set[str]:
    return {m.code for m in report.messages}


def _process_with_plan(session: Session):
    process = make_assignment_process(session)
    plan = make_teaching_plan(session, process)
    return process, plan


def _make_feasible(session: Session, plan) -> None:
    """Force a plan's stored feasibility to FEASIBLE (no solve)."""
    plan.feasibility_status = FeasibilityStatus.FEASIBLE
    session.add(plan)
    session.commit()
    session.refresh(plan)


def _main_cell(
    session,
    process,
    *,
    group_weekly_hours: float = 10.0,
    teacher_weekly_hours_per_position: float = 10.0,
):
    """A single active MAIN group-subject cell in ``process``."""
    subject = make_subject(
        session,
        process,
        name=f"Main-{_uid()}",
        allocation_category=SubjectAllocationCategory.MAIN,
    )
    group = make_teaching_group(session, process, group_code=f"G{_uid()}")
    cell = make_group_subject(
        session,
        process,
        group,
        subject,
        group_weekly_hours=group_weekly_hours,
        teacher_weekly_hours_per_position=teacher_weekly_hours_per_position,
    )
    return subject, cell


def _materialize_main(
    session,
    plan,
    subject,
    cell,
    *,
    group_weekly_hours_per_group: float = 10.0,
    teacher_weekly_hours_per_position: float = 10.0,
    required_teacher_count: int = 1,
):
    """Create the MAIN_GENERATED activity for a main cell and link it."""
    activity = make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        source=TeachingActivitySource.MAIN_GENERATED,
        source_group_subject_id=cell.id,
        group_weekly_hours_per_group=group_weekly_hours_per_group,
        teacher_weekly_hours_per_position=teacher_weekly_hours_per_position,
        required_teacher_count=required_teacher_count,
        group_subjects=[cell],
    )
    return activity


# ── A fully assignment-ready plan (the all-green path) ──────────────────────────


def _ready_plan(session: Session):
    """Build a plan with no blocking finding and no warning."""
    process, plan = _process_with_plan(session)
    subject, cell = _main_cell(session, process)
    make_allocation_revision(session, process, allocated_group_weekly_hours=10.0)
    activity = _materialize_main(session, plan, subject, cell)
    make_hour_requirement(
        session,
        process,
        activity,
        required_teacher_hours=10.0,
        status=HourRequirementStatus.AVAILABLE,
    )
    profile = make_teacher_profile(session, display_name=f"T{_uid()}")
    make_process_teacher(session, process, profile, base_weekly_hours=10.0)
    _make_feasible(session, plan)
    return process, plan


def test_ready_plan_has_no_blocking_and_no_warning(session: Session):
    _, plan = _ready_plan(session)
    report = SERVICE.compute_plan_validations(session, plan)
    assert report.is_assignment_ready is True
    assert report.blocking_count == 0
    assert report.warning_count == 0
    assert report.messages == []
    assert report.teaching_plan_id == plan.id
    assert report.assignment_process_id == plan.assignment_process_id


# ── Balances (plan §6.3) ────────────────────────────────────────────────────────


def test_missing_allocation_is_blocking(session: Session):
    _, plan = _process_with_plan(session)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_MISSING_ALLOCATION in _codes(report)
    # No allocation ⇒ the group-imbalance code is suppressed (covered by missing).
    assert CODE_GROUP_HOURS_IMBALANCED not in _codes(report)
    assert report.is_assignment_ready is False


def test_group_imbalance_is_blocking(session: Session):
    process, plan = _ready_plan(session)
    # Allocation 10 already present; add a second (secondary) activity with a
    # linked group so group load = 10 + 5 ≠ 10, without touching teacher load.
    subject = make_subject(
        session,
        process,
        name=f"X-{_uid()}",
        allocation_category=SubjectAllocationCategory.SECONDARY,
    )
    group = make_teaching_group(session, process, group_code=f"G{_uid()}")
    cell = make_group_subject(session, process, group, subject)
    make_teaching_activity(
        session,
        plan,
        subject,
        group_weekly_hours_per_group=5.0,
        teacher_weekly_hours_per_position=0.0,
        required_teacher_count=1,
        group_subjects=[cell],
    )
    report = SERVICE.compute_plan_validations(session, plan)
    codes = _codes(report)
    assert CODE_GROUP_HOURS_IMBALANCED in codes
    assert CODE_MISSING_ALLOCATION not in codes


def test_group_balanced_emits_no_imbalance(session: Session):
    _, plan = _ready_plan(session)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_GROUP_HOURS_IMBALANCED not in _codes(report)


def test_teacher_load_imbalance_is_blocking(session: Session):
    process, plan = _ready_plan(session)
    # Add a participant target with no matching teacher load.
    profile = make_teacher_profile(session, display_name=f"Extra-{_uid()}")
    make_process_teacher(session, process, profile, base_weekly_hours=7.0)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_TEACHER_LOAD_IMBALANCED in _codes(report)


# ── Main materialization (plan §6.3) ────────────────────────────────────────────


def test_unmaterialized_main_cell_is_blocking(session: Session):
    process, plan = _process_with_plan(session)
    _subject, cell = _main_cell(session, process)
    report = SERVICE.compute_plan_validations(session, plan)
    matching = [
        m for m in report.messages if m.code == CODE_MAIN_SUBJECT_NOT_MATERIALIZED
    ]
    assert len(matching) == 1
    assert matching[0].entity_type == "group_subject"
    assert matching[0].entity_id == cell.id


def test_materialized_main_cell_is_clean(session: Session):
    process, plan = _process_with_plan(session)
    subject, cell = _main_cell(session, process)
    _materialize_main(session, plan, subject, cell)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_MAIN_SUBJECT_NOT_MATERIALIZED not in _codes(report)


def test_inactive_main_cell_not_required_to_materialize(session: Session):
    process, plan = _process_with_plan(session)
    subject = make_subject(
        session,
        process,
        name=f"Main-{_uid()}",
        allocation_category=SubjectAllocationCategory.MAIN,
    )
    group = make_teaching_group(session, process, group_code=f"G{_uid()}")
    make_group_subject(session, process, group, subject, active=False)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_MAIN_SUBJECT_NOT_MATERIALIZED not in _codes(report)


# ── Activity/group links (plan §5.7, §6.3) ──────────────────────────────────────


def test_activity_missing_groups_is_blocking(session: Session):
    process, plan = _process_with_plan(session)
    subject = make_subject(
        session, process, name=f"Z-{_uid()}", allows_zero_groups=False
    )
    activity = make_teaching_activity(session, plan, subject, group_subjects=[])
    report = SERVICE.compute_plan_validations(session, plan)
    matching = [m for m in report.messages if m.code == CODE_ACTIVITY_MISSING_GROUPS]
    assert len(matching) == 1
    assert matching[0].entity_type == "teaching_activity"
    assert matching[0].entity_id == activity.id


def test_zero_group_allowed_activity_is_clean(session: Session):
    process, plan = _process_with_plan(session)
    subject = make_subject(
        session, process, name=f"Z-{_uid()}", allows_zero_groups=True
    )
    make_teaching_activity(session, plan, subject, group_subjects=[])
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_ACTIVITY_MISSING_GROUPS not in _codes(report)


def test_multiple_groups_not_allowed_is_blocking(session: Session):
    process, plan = _process_with_plan(session)
    subject = make_subject(
        session, process, name=f"M-{_uid()}", allows_multiple_groups=False
    )
    cells = []
    for _ in range(2):
        group = make_teaching_group(session, process, group_code=f"G{_uid()}")
        cells.append(make_group_subject(session, process, group, subject))
    make_teaching_activity(session, plan, subject, group_subjects=cells)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED in _codes(report)


def test_multiple_groups_allowed_is_clean(session: Session):
    process, plan = _process_with_plan(session)
    subject = make_subject(
        session, process, name=f"M-{_uid()}", allows_multiple_groups=True
    )
    cells = []
    for _ in range(2):
        group = make_teaching_group(session, process, group_code=f"G{_uid()}")
        cells.append(make_group_subject(session, process, group, subject))
    make_teaching_activity(session, plan, subject, group_subjects=cells)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED not in _codes(report)


def test_linked_subject_mismatch_is_blocking(session: Session):
    process, plan = _process_with_plan(session)
    subject_a = make_subject(session, process, name=f"A-{_uid()}")
    subject_b = make_subject(session, process, name=f"B-{_uid()}")
    group = make_teaching_group(session, process, group_code=f"G{_uid()}")
    # A cell of the *other* subject, linked to an activity of subject A.
    cell_b = make_group_subject(session, process, group, subject_b)
    activity = make_teaching_activity(session, plan, subject_a, group_subjects=[])
    make_teaching_activity_group(session, activity, cell_b)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_ACTIVITY_LINKED_SUBJECT_MISMATCH in _codes(report)


def test_retired_activity_skipped_by_link_checks(session: Session):
    process, plan = _process_with_plan(session)
    subject = make_subject(
        session, process, name=f"R-{_uid()}", allows_zero_groups=False
    )
    activity = make_teaching_activity(session, plan, subject, group_subjects=[])
    activity.retired_at = datetime.now(tz=timezone.utc)
    session.add(activity)
    session.commit()
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_ACTIVITY_MISSING_GROUPS not in _codes(report)


# ── Requirement generation / staleness (plan §6.3, §3.11) ───────────────────────


def test_requirements_not_generated_is_blocking(session: Session):
    _, plan = _process_with_plan(session)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_REQUIREMENTS_NOT_GENERATED in _codes(report)
    assert CODE_REQUIREMENTS_STALE not in _codes(report)


def test_stale_requirement_is_blocking(session: Session):
    process, plan = _ready_plan(session)
    subject = make_subject(
        session, process, name=f"S-{_uid()}", allows_zero_groups=True
    )
    activity = make_teaching_activity(
        session,
        plan,
        subject,
        group_weekly_hours_per_group=0.0,
        teacher_weekly_hours_per_position=0.0,
        required_teacher_count=1,
    )
    make_hour_requirement(
        session,
        process,
        activity,
        required_teacher_hours=0.0,
        status=HourRequirementStatus.STALE,
    )
    report = SERVICE.compute_plan_validations(session, plan)
    codes = _codes(report)
    assert CODE_REQUIREMENTS_STALE in codes
    assert CODE_REQUIREMENTS_NOT_GENERATED not in codes


def test_retired_requirement_does_not_count_as_generated(session: Session):
    process, plan = _process_with_plan(session)
    subject = make_subject(
        session, process, name=f"S-{_uid()}", allows_zero_groups=True
    )
    activity = make_teaching_activity(
        session,
        plan,
        subject,
        group_weekly_hours_per_group=0.0,
        teacher_weekly_hours_per_position=0.0,
    )
    make_hour_requirement(
        session, process, activity, required_teacher_hours=0.0, retired_generation=2
    )
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_REQUIREMENTS_NOT_GENERATED in _codes(report)


def test_stale_plan_is_blocking(session: Session):
    process, plan = _ready_plan(session)
    plan.status = TeachingPlanStatus.STALE
    session.add(plan)
    session.commit()
    session.refresh(plan)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_PLAN_STALE in _codes(report)


# ── Feasibility (plan §20.19 — read only) ───────────────────────────────────────


def test_feasibility_not_confirmed_is_blocking(session: Session):
    _, plan = _process_with_plan(session)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_FEASIBILITY_NOT_CONFIRMED in _codes(report)


def test_feasible_plan_has_no_feasibility_finding(session: Session):
    _, plan = _ready_plan(session)
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_FEASIBILITY_NOT_CONFIRMED not in _codes(report)


# ── Warnings (plan §6.4) ────────────────────────────────────────────────────────


def test_overloaded_participant_is_a_warning(session: Session):
    process, plan = _ready_plan(session)
    profile = make_teacher_profile(session, display_name=f"OL-{_uid()}")
    teacher = make_process_teacher(
        session, process, profile, base_weekly_hours=3.0, extra_weekly_hours=2.0
    )
    report = SERVICE.compute_plan_validations(session, plan)
    matching = [m for m in report.messages if m.code == CODE_PARTICIPANT_OVERLOADED]
    assert len(matching) == 1
    assert matching[0].severity == ValidationSeverity.WARNING
    assert matching[0].entity_id == teacher.id


def test_inactive_overloaded_participant_is_ignored(session: Session):
    process, plan = _ready_plan(session)
    profile = make_teacher_profile(session, display_name=f"IN-{_uid()}")
    make_process_teacher(
        session,
        process,
        profile,
        base_weekly_hours=0.0,
        extra_weekly_hours=2.0,
        status=ProcessTeacherStatus.INACTIVE,
    )
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_PARTICIPANT_OVERLOADED not in _codes(report)


def test_unmaterialized_secondary_cell_is_a_warning(session: Session):
    process, plan = _ready_plan(session)
    secondary = make_subject(
        session,
        process,
        name=f"Sec-{_uid()}",
        allocation_category=SubjectAllocationCategory.SECONDARY,
    )
    group = make_teaching_group(session, process, group_code=f"G{_uid()}")
    make_group_subject(session, process, group, secondary)
    report = SERVICE.compute_plan_validations(session, plan)
    matching = [
        m for m in report.messages if m.code == CODE_SECONDARY_ACTIVITIES_AVAILABLE
    ]
    assert len(matching) == 1
    assert matching[0].severity == ValidationSeverity.WARNING


def test_linked_secondary_cell_is_not_warned(session: Session):
    process, plan = _ready_plan(session)
    secondary = make_subject(
        session,
        process,
        name=f"Sec-{_uid()}",
        allocation_category=SubjectAllocationCategory.SECONDARY,
        allows_zero_groups=True,
    )
    group = make_teaching_group(session, process, group_code=f"G{_uid()}")
    cell = make_group_subject(
        session,
        process,
        group,
        secondary,
        group_weekly_hours=0.0,
        teacher_weekly_hours_per_position=0.0,
    )
    make_teaching_activity(
        session,
        plan,
        secondary,
        group_weekly_hours_per_group=0.0,
        teacher_weekly_hours_per_position=0.0,
        group_subjects=[cell],
    )
    report = SERVICE.compute_plan_validations(session, plan)
    assert CODE_SECONDARY_ACTIVITIES_AVAILABLE not in _codes(report)


# ── Aggregate report shape ──────────────────────────────────────────────────────


def test_report_counts_and_blocking_before_warnings(session: Session):
    process, plan = _process_with_plan(session)
    # One warning source (overloaded participant) plus several blocking sources.
    profile = make_teacher_profile(session, display_name=f"OL-{_uid()}")
    make_process_teacher(
        session, process, profile, base_weekly_hours=0.0, extra_weekly_hours=2.0
    )
    report = SERVICE.compute_plan_validations(session, plan)

    blocking = [m for m in report.messages if m.severity == ValidationSeverity.BLOCKING]
    warnings = [m for m in report.messages if m.severity == ValidationSeverity.WARNING]
    assert report.blocking_count == len(blocking)
    assert report.warning_count == len(warnings)
    assert report.blocking_count >= 1
    assert report.warning_count == 1
    # Every blocking message precedes every warning message.
    severities = [m.severity for m in report.messages]
    assert severities == [ValidationSeverity.BLOCKING] * len(blocking) + [
        ValidationSeverity.WARNING
    ] * len(warnings)
    assert report.is_assignment_ready is False
