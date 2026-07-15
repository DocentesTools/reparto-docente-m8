"""Unit tests for ``reparto_service.services.summary``.

These tests drive the calculation service against an in-memory SQLite
session and assert the expected global, teacher, requirement and
validation states for each scenario from the plan (plan 9, first slice).
"""

from __future__ import annotations


import pytest
from sqlmodel import Session

from reparto_service.enums import (
    AssignmentStatus,
    GlobalBalanceState,
    ProcessTeacherStatus,
    RequirementBalanceState,
    TeacherBalanceState,
    ValidationSeverity,
)
from reparto_service.services.summary import (
    CODE_PROCESS_BALANCED,
    CODE_PROCESS_HAS_PENDING,
    CODE_REQ_NOT_FULLY_ASSIGNED,
    CODE_REQ_OVER_ASSIGNED,
    CODE_TEACHER_BALANCED,
    CODE_TEACHER_OVERLOADED,
    SummaryService,
)
from tests import factories


# ── Global balance ───────────────────────────────────────────────────────────


def test_global_balance_empty_process_is_pending(session: Session):
    process = factories.make_assignment_process(session)
    balance = SummaryService.compute_global_balance(session, process.id)

    assert balance.total_required_hours == 0
    assert balance.total_available_hours == 0
    assert balance.total_assigned_hours == 0
    assert balance.pending_required_hours == 0
    assert balance.availability_difference == 0
    assert balance.uncovered_requirements == 0
    assert balance.overloaded_teachers == 0
    assert balance.state == GlobalBalanceState.BALANCED


def test_global_balance_pending_when_requirements_uncovered(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    factories.make_process_teacher(session, process, profile, base_weekly_hours=18.0)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )

    balance = SummaryService.compute_global_balance(session, process.id)
    assert balance.total_required_hours == 4.0
    assert balance.total_available_hours == 18.0
    assert balance.total_assigned_hours == 0.0
    assert balance.pending_required_hours == 4.0
    assert balance.availability_difference == pytest.approx(14.0)
    assert balance.uncovered_requirements == 1
    assert balance.state == GlobalBalanceState.PENDING


def test_global_balance_balanced_when_full_match(session: Session):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=4.0)

    balance = SummaryService.compute_global_balance(session, process.id)
    assert balance.total_assigned_hours == 4.0
    assert balance.pending_required_hours == 0.0
    assert balance.state == GlobalBalanceState.BALANCED


def test_global_balance_exceeded_when_teacher_overloaded_without_override(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    # Teacher gets more than available, without override.
    factories.make_assignment(session, process, requirement, pt, assigned_hours=5.0)

    balance = SummaryService.compute_global_balance(session, process.id)
    assert balance.overloaded_teachers == 1
    assert balance.state == GlobalBalanceState.EXCEEDED


def test_global_balance_warning_when_teacher_overloaded_with_override(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(
        session,
        process,
        requirement,
        pt,
        assigned_hours=5.0,
        override_reason="Department head approved",
    )

    balance = SummaryService.compute_global_balance(session, process.id)
    assert balance.overloaded_teachers == 1
    assert balance.state == GlobalBalanceState.WARNING


def test_global_balance_excluded_cancelled_assignments(session: Session):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=4.0)
    factories.make_assignment(
        session,
        process,
        requirement,
        pt,
        assigned_hours=2.0,
        status=AssignmentStatus.CANCELLED,
    )

    balance = SummaryService.compute_global_balance(session, process.id)
    assert balance.total_assigned_hours == 4.0  # cancelled not counted
    assert balance.state == GlobalBalanceState.BALANCED


# ── Teacher balance ──────────────────────────────────────────────────────────


def test_teacher_balance_inactive_when_status_inactive(session: Session):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session,
        process,
        profile,
        base_weekly_hours=10.0,
        status=ProcessTeacherStatus.INACTIVE,
    )
    balance = SummaryService.compute_teacher_balances(session, process.id)
    assert len(balance) == 1
    assert balance[0].state == TeacherBalanceState.INACTIVE
    assert balance[0].display_name == profile.display_name
    del pt


def test_teacher_balance_pending_when_assigned_less_than_available(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=10.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=2.0)
    balances = SummaryService.compute_teacher_balances(session, process.id)
    assert balances[0].assigned_hours == 2.0
    assert balances[0].remaining_hours == 8.0
    assert balances[0].excess_hours == 0.0
    assert balances[0].state == TeacherBalanceState.PENDING


def test_teacher_balance_overloaded_when_assigned_more_than_available(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=5.0)
    balances = SummaryService.compute_teacher_balances(session, process.id)
    assert balances[0].assigned_hours == 5.0
    assert balances[0].excess_hours == 1.0
    assert balances[0].state == TeacherBalanceState.OVERLOADED
    assert balances[0].has_override is False


def test_teacher_balance_overloaded_with_override_marks_flag(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(
        session,
        process,
        requirement,
        pt,
        assigned_hours=5.0,
        override_reason="Approved by department head",
    )
    balances = SummaryService.compute_teacher_balances(session, process.id)
    assert balances[0].state == TeacherBalanceState.OVERLOADED
    assert balances[0].has_override is True


def test_teacher_balance_sorted_by_display_name(session: Session):
    process = factories.make_assignment_process(session)
    factories.make_process_teacher(
        session,
        process,
        factories.make_teacher_profile(session, display_name="Zoe"),
        base_weekly_hours=10.0,
    )
    factories.make_process_teacher(
        session,
        process,
        factories.make_teacher_profile(session, display_name="Alice"),
        base_weekly_hours=10.0,
    )
    balances = SummaryService.compute_teacher_balances(session, process.id)
    assert [b.display_name for b in balances] == ["Alice", "Zoe"]


# ── Requirement balance ──────────────────────────────────────────────────────


def test_requirement_balance_uncovered_when_no_assignments(
    session: Session,
):
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    balances = SummaryService.compute_requirement_balances(session, process.id)
    assert len(balances) == 1
    assert balances[0].state == RequirementBalanceState.UNCOVERED
    assert balances[0].assigned_hours == 0.0
    assert balances[0].pending_hours == 4.0


def test_requirement_balance_partial_when_partial_assignment(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=2.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=2.0)
    balances = SummaryService.compute_requirement_balances(session, process.id)
    assert balances[0].state == RequirementBalanceState.PARTIAL
    assert balances[0].pending_hours == 2.0


def test_requirement_balance_covered_when_full_match(session: Session):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=4.0)
    balances = SummaryService.compute_requirement_balances(session, process.id)
    assert balances[0].state == RequirementBalanceState.COVERED
    assert balances[0].pending_hours == 0.0


def test_requirement_balance_over_assigned_blocks_when_no_override(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=5.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=5.0)
    balances = SummaryService.compute_requirement_balances(session, process.id)
    assert balances[0].state == RequirementBalanceState.OVER_ASSIGNED
    assert balances[0].has_override is False


def test_requirement_balance_explicitly_shared_when_split_with_override(
    session: Session,
):
    process = factories.make_assignment_process(session)
    p1 = factories.make_teacher_profile(session, display_name="Anna")
    p2 = factories.make_teacher_profile(session, display_name="Bob")
    pt1 = factories.make_process_teacher(session, process, p1, base_weekly_hours=2.0)
    pt2 = factories.make_process_teacher(session, process, p2, base_weekly_hours=2.0)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(
        session,
        process,
        requirement,
        pt1,
        assigned_hours=2.0,
        override_reason="Split between two teachers",
    )
    factories.make_assignment(
        session,
        process,
        requirement,
        pt2,
        assigned_hours=2.0,
        override_reason="Split between two teachers",
    )
    balances = SummaryService.compute_requirement_balances(session, process.id)
    assert balances[0].state == RequirementBalanceState.EXPLICITLY_SHARED
    assert balances[0].has_override is True
    assert balances[0].assignment_count == 2


# ── Validations ──────────────────────────────────────────────────────────────


def test_validations_empty_process_has_no_blocking(session: Session):
    process = factories.make_assignment_process(session)
    validations = SummaryService.compute_validations(session, process.id)
    assert all(v.severity != ValidationSeverity.BLOCKING for v in validations)


def test_validations_blocking_when_uncovered_requirement(session: Session):
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    validations = SummaryService.compute_validations(session, process.id)
    blocking = [v for v in validations if v.severity == ValidationSeverity.BLOCKING]
    # Per-requirement "not_fully_assigned" + process-level "has_pending".
    assert len(blocking) == 2
    requirement_messages = [v for v in blocking if v.entity_type == "requirement"]
    process_messages = [v for v in blocking if v.entity_type == "process"]
    assert len(requirement_messages) == 1
    assert requirement_messages[0].code == CODE_REQ_NOT_FULLY_ASSIGNED
    assert any(v.code == CODE_PROCESS_HAS_PENDING for v in process_messages)


def test_validations_blocking_when_requirement_over_assigned(
    session: Session,
):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=5.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=5.0)
    validations = SummaryService.compute_validations(session, process.id)
    over_assigned = [
        v
        for v in validations
        if v.severity == ValidationSeverity.BLOCKING
        and v.code == CODE_REQ_OVER_ASSIGNED
    ]
    assert len(over_assigned) == 1


def test_validations_warning_when_requirement_over_assigned_with_override(
    session: Session,
):
    from reparto_service.services.summary import CODE_REQ_OVER_ASSIGNED_OVERRIDDEN

    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=5.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(
        session,
        process,
        requirement,
        pt,
        assigned_hours=5.0,
        override_reason="Head approved",
    )
    validations = SummaryService.compute_validations(session, process.id)
    blocking = [
        v
        for v in validations
        if v.severity == ValidationSeverity.BLOCKING
        and v.code == CODE_REQ_OVER_ASSIGNED
    ]
    assert blocking == []
    warnings = [v for v in validations if v.severity == ValidationSeverity.WARNING]
    assert any(v.code == CODE_REQ_OVER_ASSIGNED_OVERRIDDEN for v in warnings)


def test_validations_blocking_when_teacher_overloaded(session: Session):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=5.0)
    validations = SummaryService.compute_validations(session, process.id)
    blocked_teachers = [
        v
        for v in validations
        if v.severity == ValidationSeverity.BLOCKING
        and v.code == CODE_TEACHER_OVERLOADED
    ]
    assert len(blocked_teachers) == 1


def test_validations_info_when_teacher_balanced(session: Session):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=4.0)
    validations = SummaryService.compute_validations(session, process.id)
    info = [v for v in validations if v.severity == ValidationSeverity.INFO]
    codes = {v.code for v in info}
    assert CODE_TEACHER_BALANCED in codes
    assert CODE_PROCESS_BALANCED in codes


def test_validations_blocking_when_process_has_pending(session: Session):
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    validations = SummaryService.compute_validations(session, process.id)
    pending = [
        v
        for v in validations
        if v.severity == ValidationSeverity.BLOCKING
        and v.code == CODE_PROCESS_HAS_PENDING
    ]
    assert len(pending) >= 1


# ── Summary / dashboard ──────────────────────────────────────────────────────


def test_summary_aggregates_blocking_count(session: Session):
    process = factories.make_assignment_process(session)
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    summary = SummaryService.compute_summary(session, process.id)
    assert summary.process_id == process.id
    assert summary.blocking_validation_count >= 1


def test_dashboard_combines_all_sections(session: Session):
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    subject = factories.make_subject(session, process)
    group = factories.make_teaching_group(session, process)
    requirement = factories.make_hour_requirement(
        session, process, group, subject, required_hours=4.0
    )
    factories.make_assignment(session, process, requirement, pt, assigned_hours=4.0)
    dashboard = SummaryService.compute_dashboard(session, process.id)
    assert dashboard.process_id == process.id
    assert len(dashboard.teacher_balances) == 1
    assert len(dashboard.requirement_balances) == 1
    assert dashboard.global_balance.state == GlobalBalanceState.BALANCED
    assert any(v.code == CODE_TEACHER_BALANCED for v in dashboard.validations)


# ── Process status filter ────────────────────────────────────────────────────


def test_assignments_belonging_to_other_process_are_excluded(
    session: Session,
):
    process_a = factories.make_assignment_process(session)
    process_b = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    pt_a = factories.make_process_teacher(
        session, process_a, profile, base_weekly_hours=4.0
    )
    subject_a = factories.make_subject(session, process_a)
    group_a = factories.make_teaching_group(session, process_a)
    requirement_a = factories.make_hour_requirement(
        session, process_a, group_a, subject_a, required_hours=4.0
    )
    factories.make_assignment(
        session, process_a, requirement_a, pt_a, assigned_hours=4.0
    )

    # Process B has its own (unrelated) requirement.
    subject_b = factories.make_subject(session, process_b, name="Other")
    group_b = factories.make_teaching_group(session, process_b, label="Other group")
    factories.make_hour_requirement(
        session, process_b, group_b, subject_b, required_hours=10.0
    )

    balance = SummaryService.compute_global_balance(session, process_a.id)
    assert balance.total_required_hours == 4.0  # NOT 14.0
    assert balance.uncovered_requirements == 0
