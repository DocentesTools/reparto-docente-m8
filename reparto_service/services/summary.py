"""Pure summary/balance/validation service for assignment processes.

The service reads from a ``sqlmodel.Session`` and returns plain
Pydantic schemas (``reparto_service.schemas.summary``). It does not
issue HTTP responses and does not own transactions: callers control
the session lifecycle. This keeps the math easy to unit-test and
reusable from any route (including the SSE event payloads in Phase 2).

Calculation policy (plan 9, first implementation slice):

* ``Assignment.status == CANCELLED`` is excluded from all totals.
* A requirement's *assigned* total exceeds the cap only when
  ``total > required`` AND no assignment on the requirement carries a
  department head override.
* A process teacher's *assigned* total exceeds the cap only when
  ``total > available`` AND no assignment for that teacher carries a
  department head override.
* Negative or zero ``assigned_hours`` is rejected by the schema
  (``Assignment.assigned_hours`` has ``gt=0``); the schema layer is
  the first line of defence and the service trusts it for the happy
  path.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

from sqlmodel import Session, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.enums import (
    AssignmentStatus,
    GlobalBalanceState,
    ProcessTeacherStatus,
    RequirementBalanceState,
    TeacherBalanceState,
    ValidationSeverity,
)
from reparto_service.schemas.summary import (
    GlobalBalance,
    ProcessDashboard,
    ProcessSummary,
    RequirementBalance,
    TeacherBalance,
    ValidationMessage,
)

# Tolerance for floating-point comparison. Plan 9 expresses everything
# in hours; the catalogue rarely uses fractions of an hour, but the
# schema accepts them, so the comparisons treat a sub-epsilon gap as
# equal.
_EPSILON = 1e-6

# Stable validation codes (used by the API and the frontend).
CODE_REQ_OVER_ASSIGNED = "requirement.over_assigned"
CODE_REQ_OVER_ASSIGNED_OVERRIDDEN = "requirement.over_assigned_overridden"
CODE_REQ_NOT_FULLY_ASSIGNED = "requirement.not_fully_assigned"
CODE_REQ_FULLY_ASSIGNED = "requirement.fully_assigned"
CODE_TEACHER_OVERLOADED = "teacher.overloaded"
CODE_TEACHER_OVERLOADED_OVERRIDDEN = "teacher.overloaded_overridden"
CODE_TEACHER_BALANCED = "teacher.balanced"
CODE_PROCESS_BALANCED = "process.balanced"
CODE_PROCESS_HAS_PENDING = "process.has_pending"
CODE_PROCESS_HAS_OVERAGE = "process.has_overage"


class SummaryService:
    """Stateless calculator. All methods are pure functions of the session state."""

    # ── Lookups (one-shot, reused by every compute) ──────────────────────────

    @staticmethod
    def _load_process_teachers(
        session: Session, process_id: uuid.UUID
    ) -> list[tuple[ProcessTeacher, TeacherProfile]]:
        """Return ``(ProcessTeacher, TeacherProfile)`` rows for the process."""
        statement = (
            select(ProcessTeacher, TeacherProfile)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.teacher_profile_id == TeacherProfile.id)
        )
        return list(session.exec(statement).all())

    @staticmethod
    def _load_requirements(
        session: Session, process_id: uuid.UUID
    ) -> list[tuple[HourRequirement, TeachingGroup, Subject]]:
        statement = (
            select(HourRequirement, TeachingGroup, Subject)
            .where(HourRequirement.assignment_process_id == process_id)
            .where(HourRequirement.teaching_group_id == TeachingGroup.id)
            .where(HourRequirement.subject_id == Subject.id)
        )
        return list(session.exec(statement).all())

    @staticmethod
    def _load_active_assignments(
        session: Session, process_id: uuid.UUID
    ) -> list[Assignment]:
        statement = select(Assignment).where(
            Assignment.assignment_process_id == process_id
        )
        rows = list(session.exec(statement).all())
        return [row for row in rows if row.status != AssignmentStatus.CANCELLED]

    # ── Per-row balances ────────────────────────────────────────────────────

    @staticmethod
    def _aggregate_by_teacher(
        assignments: Iterable[Assignment],
    ) -> dict[uuid.UUID, tuple[float, int, bool]]:
        """Return ``process_teacher_id -> (assigned_hours, count, has_override)``."""
        totals: dict[uuid.UUID, list[float | int | bool]] = defaultdict(
            lambda: [0.0, 0, False]
        )
        for assignment in assignments:
            entry = totals[assignment.process_teacher_id]
            entry[0] = float(entry[0]) + assignment.assigned_hours
            entry[1] = int(entry[1]) + 1
            if assignment.override_reason is not None:
                entry[2] = True
        return {
            teacher_id: (
                float(values[0]),
                int(values[1]),
                bool(values[2]),
            )
            for teacher_id, values in totals.items()
        }

    @staticmethod
    def _aggregate_by_requirement(
        assignments: Iterable[Assignment],
    ) -> dict[uuid.UUID, tuple[float, int, bool]]:
        totals: dict[uuid.UUID, list[float | int | bool]] = defaultdict(
            lambda: [0.0, 0, False]
        )
        for assignment in assignments:
            entry = totals[assignment.hour_requirement_id]
            entry[0] = float(entry[0]) + assignment.assigned_hours
            entry[1] = int(entry[1]) + 1
            if assignment.override_reason is not None:
                entry[2] = True
        return {
            requirement_id: (
                float(values[0]),
                int(values[1]),
                bool(values[2]),
            )
            for requirement_id, values in totals.items()
        }

    # ── Public API ──────────────────────────────────────────────────────────

    @staticmethod
    def compute_teacher_balances(
        session: Session, process_id: uuid.UUID
    ) -> list[TeacherBalance]:
        rows = SummaryService._load_process_teachers(session, process_id)
        assignments = SummaryService._load_active_assignments(session, process_id)
        per_teacher = SummaryService._aggregate_by_teacher(assignments)

        balances: list[TeacherBalance] = []
        for process_teacher, profile in rows:
            assigned, count, has_override = per_teacher.get(
                process_teacher.id, (0.0, 0, False)
            )
            available = process_teacher.available_hours
            remaining = available - assigned
            excess = max(0.0, assigned - available)
            if process_teacher.status != ProcessTeacherStatus.ACTIVE:
                state = TeacherBalanceState.INACTIVE
            elif not process_teacher.participates_in_selection:
                state = TeacherBalanceState.NOT_PARTICIPATING
            elif assigned > available + _EPSILON:
                state = TeacherBalanceState.OVERLOADED
            elif remaining > _EPSILON:
                state = TeacherBalanceState.PENDING
            else:
                state = TeacherBalanceState.BALANCED
            balances.append(
                TeacherBalance(
                    process_teacher_id=process_teacher.id,
                    teacher_profile_id=profile.id,
                    display_name=profile.display_name,
                    available_hours=available,
                    assigned_hours=assigned,
                    remaining_hours=remaining,
                    excess_hours=excess,
                    assignment_count=count,
                    has_override=has_override,
                    state=state,
                )
            )
        balances.sort(
            key=lambda b: (
                b.display_name.casefold(),
                str(b.process_teacher_id),
            )
        )
        return balances

    @staticmethod
    def compute_requirement_balances(
        session: Session, process_id: uuid.UUID
    ) -> list[RequirementBalance]:
        rows = SummaryService._load_requirements(session, process_id)
        assignments = SummaryService._load_active_assignments(session, process_id)
        per_requirement = SummaryService._aggregate_by_requirement(assignments)

        balances: list[RequirementBalance] = []
        for requirement, group, subject in rows:
            assigned, count, has_override = per_requirement.get(
                requirement.id, (0.0, 0, False)
            )
            pending = max(0.0, requirement.required_hours - assigned)
            if assigned > requirement.required_hours + _EPSILON:
                state = RequirementBalanceState.OVER_ASSIGNED
            elif count > 1 and has_override:
                state = RequirementBalanceState.EXPLICITLY_SHARED
            elif assigned <= _EPSILON:
                state = RequirementBalanceState.UNCOVERED
            elif assigned + _EPSILON < requirement.required_hours:
                state = RequirementBalanceState.PARTIAL
            else:
                state = RequirementBalanceState.COVERED
            balances.append(
                RequirementBalance(
                    hour_requirement_id=requirement.id,
                    teaching_group_id=group.id,
                    teaching_group_label=group.label,
                    subject_id=subject.id,
                    subject_name=subject.name,
                    required_hours=requirement.required_hours,
                    assigned_hours=assigned,
                    pending_hours=pending,
                    assignment_count=count,
                    has_override=has_override,
                    state=state,
                )
            )
        balances.sort(
            key=lambda b: (
                b.teaching_group_label.casefold(),
                b.subject_name.casefold(),
                str(b.hour_requirement_id),
            )
        )
        return balances

    @staticmethod
    def compute_global_balance(
        session: Session, process_id: uuid.UUID
    ) -> GlobalBalance:
        teacher_rows = SummaryService._load_process_teachers(session, process_id)
        requirement_rows = SummaryService._load_requirements(session, process_id)
        assignments = SummaryService._load_active_assignments(session, process_id)

        total_available = sum(
            pt.available_hours
            for pt, _ in teacher_rows
            if pt.status == ProcessTeacherStatus.ACTIVE
        )
        total_required = sum(
            requirement.required_hours for requirement, _, _ in requirement_rows
        )
        total_assigned = sum(a.assigned_hours for a in assignments)
        pending = total_required - total_assigned
        availability_diff = total_available - total_required

        per_teacher = SummaryService._aggregate_by_teacher(assignments)
        per_requirement = SummaryService._aggregate_by_requirement(assignments)

        uncovered = sum(
            1
            for requirement, _, _ in requirement_rows
            if per_requirement.get(requirement.id, (0.0, 0, False))[0] <= _EPSILON
        )
        overloaded = sum(
            1
            for pt, _ in teacher_rows
            if pt.status == ProcessTeacherStatus.ACTIVE
            and per_teacher.get(pt.id, (0.0, 0, False))[0]
            > pt.available_hours + _EPSILON
        )

        has_teaching_overrides = any(
            has_override for _, _, has_override in per_teacher.values()
        )  # noqa: E501
        has_requirement_overrides = any(
            has_override for _, _, has_override in per_requirement.values()
        )
        has_any_override = has_teaching_overrides or has_requirement_overrides
        has_unresolved_overage = any(
            assigned > pt.available_hours + _EPSILON and not has_override
            for pt, (assigned, _, has_override) in (
                (pt, per_teacher.get(pt.id, (0.0, 0, False))) for pt, _ in teacher_rows
            )
        ) or any(
            assigned > requirement.required_hours + _EPSILON and not has_override
            for requirement, (assigned, _, has_override) in (
                (requirement, per_requirement.get(requirement.id, (0.0, 0, False)))
                for requirement, _, _ in requirement_rows
            )
        )
        if has_unresolved_overage:
            state = GlobalBalanceState.EXCEEDED
        elif pending > _EPSILON:
            state = GlobalBalanceState.PENDING
        elif pending < -_EPSILON and has_any_override:
            # Over-assigned but every overage is department-head-overridden.
            state = GlobalBalanceState.WARNING
        else:
            state = GlobalBalanceState.BALANCED

        return GlobalBalance(
            total_required_hours=total_required,
            total_available_hours=total_available,
            total_assigned_hours=total_assigned,
            pending_required_hours=pending,
            availability_difference=availability_diff,
            uncovered_requirements=uncovered,
            overloaded_teachers=overloaded,
            state=state,
        )

    @staticmethod
    def compute_validations(
        session: Session, process_id: uuid.UUID
    ) -> list[ValidationMessage]:
        teacher_balances = SummaryService.compute_teacher_balances(session, process_id)
        requirement_balances = SummaryService.compute_requirement_balances(
            session, process_id
        )
        global_balance = SummaryService.compute_global_balance(session, process_id)
        messages: list[ValidationMessage] = []

        for req_balance in requirement_balances:
            if req_balance.state == RequirementBalanceState.OVER_ASSIGNED:
                if not req_balance.has_override:
                    messages.append(
                        ValidationMessage(
                            severity=ValidationSeverity.BLOCKING,
                            code=CODE_REQ_OVER_ASSIGNED,
                            message=(
                                f"Requirement {req_balance.subject_name} for "
                                f"{req_balance.teaching_group_label} is over-assigned "
                                f"({req_balance.assigned_hours:.2f} h assigned for "
                                f"{req_balance.required_hours:.2f} h required)."
                            ),
                            entity_type="requirement",
                            entity_id=req_balance.hour_requirement_id,
                        )
                    )
                else:
                    messages.append(
                        ValidationMessage(
                            severity=ValidationSeverity.WARNING,
                            code=CODE_REQ_OVER_ASSIGNED_OVERRIDDEN,
                            message=(
                                f"Requirement {req_balance.subject_name} for "
                                f"{req_balance.teaching_group_label} is over-assigned "
                                "but a department head override has been recorded."
                            ),
                            entity_type="requirement",
                            entity_id=req_balance.hour_requirement_id,
                        )
                    )
            elif req_balance.state == RequirementBalanceState.UNCOVERED:
                messages.append(
                    ValidationMessage(
                        severity=ValidationSeverity.BLOCKING,
                        code=CODE_REQ_NOT_FULLY_ASSIGNED,
                        message=(
                            f"Requirement {req_balance.subject_name} for "
                            f"{req_balance.teaching_group_label} has no assignment yet."
                        ),
                        entity_type="requirement",
                        entity_id=req_balance.hour_requirement_id,
                    )
                )
            elif req_balance.state == RequirementBalanceState.PARTIAL:
                messages.append(
                    ValidationMessage(
                        severity=ValidationSeverity.WARNING,
                        code=CODE_REQ_NOT_FULLY_ASSIGNED,
                        message=(
                            f"Requirement {req_balance.subject_name} for "
                            f"{req_balance.teaching_group_label} is partially covered "
                            f"({req_balance.pending_hours:.2f} h still pending)."
                        ),
                        entity_type="requirement",
                        entity_id=req_balance.hour_requirement_id,
                    )
                )
            elif req_balance.state in {
                RequirementBalanceState.COVERED,
                RequirementBalanceState.EXPLICITLY_SHARED,
            }:
                messages.append(
                    ValidationMessage(
                        severity=ValidationSeverity.INFO,
                        code=CODE_REQ_FULLY_ASSIGNED,
                        message=(
                            f"Requirement {req_balance.subject_name} for "
                            f"{req_balance.teaching_group_label} is fully covered."
                        ),
                        entity_type="requirement",
                        entity_id=req_balance.hour_requirement_id,
                    )
                )

        for balance in teacher_balances:
            if balance.state == TeacherBalanceState.OVERLOADED:
                if not balance.has_override:
                    messages.append(
                        ValidationMessage(
                            severity=ValidationSeverity.BLOCKING,
                            code=CODE_TEACHER_OVERLOADED,
                            message=(
                                f"{balance.display_name} is overloaded "
                                f"({balance.assigned_hours:.2f} h assigned for "
                                f"{balance.available_hours:.2f} h available)."
                            ),
                            entity_type="teacher",
                            entity_id=balance.process_teacher_id,
                        )
                    )
                else:
                    messages.append(
                        ValidationMessage(
                            severity=ValidationSeverity.WARNING,
                            code=CODE_TEACHER_OVERLOADED_OVERRIDDEN,
                            message=(
                                f"{balance.display_name} is overloaded but a "
                                "department head override has been recorded."
                            ),
                            entity_type="teacher",
                            entity_id=balance.process_teacher_id,
                        )
                    )
            elif balance.state == TeacherBalanceState.BALANCED:
                messages.append(
                    ValidationMessage(
                        severity=ValidationSeverity.INFO,
                        code=CODE_TEACHER_BALANCED,
                        message=f"{balance.display_name} is at balance.",
                        entity_type="teacher",
                        entity_id=balance.process_teacher_id,
                    )
                )

        if global_balance.state == GlobalBalanceState.BALANCED:
            messages.append(
                ValidationMessage(
                    severity=ValidationSeverity.INFO,
                    code=CODE_PROCESS_BALANCED,
                    message="Process hours are balanced.",
                    entity_type="process",
                    entity_id=process_id,
                )
            )
        elif global_balance.state in {
            GlobalBalanceState.PENDING,
            GlobalBalanceState.EXCEEDED,
            GlobalBalanceState.WARNING,
        }:
            if global_balance.uncovered_requirements > 0:
                messages.append(
                    ValidationMessage(
                        severity=ValidationSeverity.BLOCKING,
                        code=CODE_PROCESS_HAS_PENDING,
                        message=(
                            f"{global_balance.uncovered_requirements} requirement(s) "
                            "still need hours."
                        ),
                        entity_type="process",
                        entity_id=process_id,
                    )
                )
            if global_balance.state == GlobalBalanceState.EXCEEDED:
                messages.append(
                    ValidationMessage(
                        severity=ValidationSeverity.BLOCKING,
                        code=CODE_PROCESS_HAS_OVERAGE,
                        message=(
                            "Process has unresolved over-assignments "
                            "(see blocking requirement / teacher validations)."
                        ),
                        entity_type="process",
                        entity_id=process_id,
                    )
                )

        return messages

    @staticmethod
    def compute_summary(session: Session, process_id: uuid.UUID) -> ProcessSummary:
        global_balance = SummaryService.compute_global_balance(session, process_id)
        validations = SummaryService.compute_validations(session, process_id)
        blocking = sum(
            1 for v in validations if v.severity == ValidationSeverity.BLOCKING
        )
        return ProcessSummary(
            process_id=process_id,
            global_balance=global_balance,
            validations=validations,
            blocking_validation_count=blocking,
        )

    @staticmethod
    def compute_dashboard(session: Session, process_id: uuid.UUID) -> ProcessDashboard:
        teacher_balances = SummaryService.compute_teacher_balances(session, process_id)
        requirement_balances = SummaryService.compute_requirement_balances(
            session, process_id
        )
        global_balance = SummaryService.compute_global_balance(session, process_id)
        validations = SummaryService.compute_validations(session, process_id)
        blocking = sum(
            1 for v in validations if v.severity == ValidationSeverity.BLOCKING
        )
        return ProcessDashboard(
            process_id=process_id,
            generated_at=datetime.now(tz=timezone.utc),
            global_balance=global_balance,
            teacher_balances=teacher_balances,
            requirement_balances=requirement_balances,
            validations=validations,
            blocking_validation_count=blocking,
        )
