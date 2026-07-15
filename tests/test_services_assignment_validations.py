"""Tests for the assignment-stage validation service (plan §6.3, §6.4).

Covers every assignment blocking finding (unassigned indivisible slots,
participant over their exact target, active participant below target) and the
authorized-overload warning, plus the aggregate report (final-readiness, counts,
blocking-before-warning ordering and deterministic per-teacher ordering). The
report is solver-free (plan §20.23): no feasibility evaluation is ever triggered.
"""

from __future__ import annotations

from sqlmodel import Session, select

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentStatus,
    ProcessTeacherStatus,
    ValidationSeverity,
)
from reparto_service.schemas.planning import AssignmentValidationReport
from reparto_service.services.validations import (
    CODE_PARTICIPANT_BELOW_TARGET,
    CODE_PARTICIPANT_OVERLOADED,
    CODE_PARTICIPANT_OVER_TARGET,
    CODE_REQUIREMENTS_UNASSIGNED,
    AssignmentValidationService,
)
from tests.factories import (
    make_assignment,
    make_assignment_process,
    make_hour_requirement,
    make_process_teacher,
    make_subject,
    make_teacher_profile,
    make_teaching_activity,
    make_teaching_plan,
)

SERVICE = AssignmentValidationService

_COUNTER = iter(range(1, 100_000))


def _uid() -> int:
    return next(_COUNTER)


def _codes(report: AssignmentValidationReport) -> set[str]:
    return {m.code for m in report.messages}


def _process(session: Session) -> AssignmentProcess:
    process = make_assignment_process(session)
    make_teaching_plan(session, process)
    return process


def _activity(session: Session, process: AssignmentProcess):
    """One secondary activity on the process's (single) plan."""
    subject = make_subject(session, process, name=f"Subject {_uid()}")
    plan = session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == process.id)
    ).first()
    assert plan is not None
    return make_teaching_activity(session, plan, subject)


def _teacher(
    session: Session,
    process: AssignmentProcess,
    *,
    base: float = 4.0,
    extra: float = 0.0,
    status: ProcessTeacherStatus = ProcessTeacherStatus.ACTIVE,
    participates: bool = True,
) -> ProcessTeacher:
    profile = make_teacher_profile(session, display_name=f"Teacher {_uid()}")
    return make_process_teacher(
        session,
        process,
        profile,
        base_weekly_hours=base,
        extra_weekly_hours=extra,
        status=status,
        participates_in_selection=participates,
    )


# ── All-green / readiness ─────────────────────────────────────────────────────


def test_balanced_process_is_final_ready(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    slot = make_hour_requirement(session, process, activity, required_teacher_hours=4.0)
    teacher = _teacher(session, process, base=4.0)
    make_assignment(session, process, slot, teacher, status=AssignmentStatus.ACTIVE)

    report = SERVICE.compute_assignment_validations(session, process)

    assert report.assignment_process_id == process.id
    assert report.is_final_ready is True
    assert report.blocking_count == 0
    assert report.warning_count == 0
    assert report.messages == []


def test_report_shape_counts_match_messages(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    make_hour_requirement(session, process, activity)  # unassigned slot
    _teacher(session, process, base=6.0)  # below target

    report = SERVICE.compute_assignment_validations(session, process)

    blocking = [m for m in report.messages if m.severity == ValidationSeverity.BLOCKING]
    warning = [m for m in report.messages if m.severity == ValidationSeverity.WARNING]
    assert report.blocking_count == len(blocking)
    assert report.warning_count == len(warning)
    # Blocking findings always precede warnings.
    assert report.messages[: report.blocking_count] == blocking


# ── Unassigned slots (indivisibility / coverage) ──────────────────────────────


def test_unassigned_slot_blocks_final_close(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    make_hour_requirement(session, process, activity)
    # A balanced (target 0) teacher so only the slot is a problem.
    _teacher(session, process, base=0.0)

    report = SERVICE.compute_assignment_validations(session, process)

    assert report.is_final_ready is False
    assert _codes(report) == {CODE_REQUIREMENTS_UNASSIGNED}
    msg = report.messages[0]
    assert msg.entity_type == "assignment_process"
    assert msg.entity_id == process.id


def test_no_unassigned_finding_when_every_slot_occupied(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    slot = make_hour_requirement(session, process, activity, required_teacher_hours=4.0)
    teacher = _teacher(session, process, base=4.0)
    make_assignment(session, process, slot, teacher, status=AssignmentStatus.ACTIVE)

    report = SERVICE.compute_assignment_validations(session, process)

    assert CODE_REQUIREMENTS_UNASSIGNED not in _codes(report)


def test_cancelled_assignment_leaves_slot_unassigned(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    slot = make_hour_requirement(session, process, activity, required_teacher_hours=4.0)
    teacher = _teacher(session, process, base=0.0)
    make_assignment(session, process, slot, teacher, status=AssignmentStatus.CANCELLED)

    report = SERVICE.compute_assignment_validations(session, process)

    assert CODE_REQUIREMENTS_UNASSIGNED in _codes(report)


# ── Exact participant target: over target (no overload bypass) ─────────────────


def test_over_target_blocks(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    slot = make_hour_requirement(session, process, activity, required_teacher_hours=4.0)
    teacher = _teacher(session, process, base=2.0)  # target 2 < assigned 4
    make_assignment(session, process, slot, teacher, status=AssignmentStatus.ACTIVE)

    report = SERVICE.compute_assignment_validations(session, process)

    assert report.is_final_ready is False
    assert CODE_PARTICIPANT_OVER_TARGET in _codes(report)
    over = next(m for m in report.messages if m.code == CODE_PARTICIPANT_OVER_TARGET)
    assert over.entity_type == "teacher"
    assert over.entity_id == teacher.id
    assert over.severity == ValidationSeverity.BLOCKING


def test_over_target_ignored_for_inactive_teacher(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    slot = make_hour_requirement(session, process, activity, required_teacher_hours=4.0)
    teacher = _teacher(session, process, base=2.0, status=ProcessTeacherStatus.INACTIVE)
    make_assignment(session, process, slot, teacher, status=AssignmentStatus.ACTIVE)

    report = SERVICE.compute_assignment_validations(session, process)

    assert CODE_PARTICIPANT_OVER_TARGET not in _codes(report)
    assert report.is_final_ready is True


# ── Exact participant target: below target ────────────────────────────────────


def test_below_target_blocks_for_active_participant(session: Session) -> None:
    process = _process(session)
    _teacher(session, process, base=6.0)  # no assignment, remaining 6

    report = SERVICE.compute_assignment_validations(session, process)

    assert report.is_final_ready is False
    assert _codes(report) == {CODE_PARTICIPANT_BELOW_TARGET}


def test_below_target_skipped_for_non_participating(session: Session) -> None:
    process = _process(session)
    _teacher(session, process, base=6.0, participates=False)

    report = SERVICE.compute_assignment_validations(session, process)

    assert CODE_PARTICIPANT_BELOW_TARGET not in _codes(report)
    assert report.is_final_ready is True


def test_below_target_skipped_for_inactive(session: Session) -> None:
    process = _process(session)
    _teacher(session, process, base=6.0, status=ProcessTeacherStatus.INACTIVE)

    report = SERVICE.compute_assignment_validations(session, process)

    assert report.messages == []
    assert report.is_final_ready is True


# ── Authorized overload warning ───────────────────────────────────────────────


def test_authorized_overload_is_warning_only(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    slot = make_hour_requirement(session, process, activity, required_teacher_hours=6.0)
    teacher = _teacher(session, process, base=4.0, extra=2.0)  # target 6, assigned 6
    make_assignment(session, process, slot, teacher, status=AssignmentStatus.ACTIVE)

    report = SERVICE.compute_assignment_validations(session, process)

    assert report.is_final_ready is True
    assert report.blocking_count == 0
    assert report.warning_count == 1
    warn = report.messages[0]
    assert warn.code == CODE_PARTICIPANT_OVERLOADED
    assert warn.severity == ValidationSeverity.WARNING
    assert warn.entity_id == teacher.id


# ── Ordering and aggregation ──────────────────────────────────────────────────


def test_blocking_first_then_warning_and_deterministic(session: Session) -> None:
    process = _process(session)
    activity = _activity(session, process)
    # One unassigned slot (process-wide blocking).
    make_hour_requirement(session, process, activity)
    # Two below-target participants and one overloaded (warning).
    _teacher(session, process, base=6.0)
    _teacher(session, process, base=8.0)
    over_slot = make_hour_requirement(
        session, process, activity, position_index=1, required_teacher_hours=6.0
    )
    overloaded = _teacher(session, process, base=4.0, extra=2.0)
    make_assignment(
        session, process, over_slot, overloaded, status=AssignmentStatus.ACTIVE
    )

    report = SERVICE.compute_assignment_validations(session, process)

    severities = [m.severity for m in report.messages]
    # Every blocking finding precedes every warning.
    first_warning = severities.index(ValidationSeverity.WARNING)
    assert all(s == ValidationSeverity.BLOCKING for s in severities[:first_warning])
    assert all(s == ValidationSeverity.WARNING for s in severities[first_warning:])
    # The process-wide unassigned finding leads.
    assert report.messages[0].code == CODE_REQUIREMENTS_UNASSIGNED
    # Per-teacher below-target findings are ordered by teacher id.
    below_ids = [
        str(m.entity_id)
        for m in report.messages
        if m.code == CODE_PARTICIPANT_BELOW_TARGET
    ]
    assert below_ids == sorted(below_ids)
