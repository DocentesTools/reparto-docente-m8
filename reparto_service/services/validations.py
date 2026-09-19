"""Planning-stage blocking and warning validations (plan §6.3, §6.4).

:class:`PlanValidationService` turns the numeric balances of
:mod:`reparto_service.services.calculations` and a handful of cheap structural
queries into the human/​machine validation findings the department head sees
before locking a plan or starting the assignment stage. It is the message layer
that :mod:`reparto_service.services.calculations` deliberately leaves out — that
module owns the numbers, this one owns the findings.

Scope and cost (plan §20.19/§20.23): every check here is O(rows) — a balance
comparison or an existence query. The **exponential feasibility solver is never
run** from this service (that is its own §20.20 task). The stored
``feasibility_status`` is *read* and surfaced as a blocking finding when it is
not ``FEASIBLE``, but no solve is triggered, so ``compute_plan_validations`` is
safe on any request path.

Findings carry a stable ``code`` (the ``CODE_*`` constants below; the frontend
keys off these, never off the prose ``message``) and are returned blocking-first
in a deterministic order so snapshots and comparisons stay stable.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlmodel import Session, col, select

from reparto_service.core.decimals import quantize_hours
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    FeasibilityStatus,
    HourRequirementStatus,
    ProcessTeacherStatus,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingActivitySyncState,
    TeachingPlanStatus,
    ValidationSeverity,
)
from reparto_service.schemas.planning import (
    AssignmentValidationReport,
    PlanValidationMessage,
    PlanValidationReport,
)
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.schemas.validation_findings import (
    CODE_ACTIVITY_LINKED_SUBJECT_MISMATCH,
    CODE_ACTIVITY_MISSING_GROUPS,
    CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED,
    CODE_ACTIVITY_OUT_OF_SYNC,
    CODE_FEASIBILITY_NOT_CONFIRMED,
    CODE_GROUP_HOURS_IMBALANCED,
    CODE_MAIN_SUBJECT_NOT_MATERIALIZED,
    CODE_MISSING_ALLOCATION,
    CODE_PARTICIPANT_BELOW_TARGET,
    CODE_PARTICIPANT_OVERLOADED,
    CODE_PARTICIPANT_OVER_TARGET,
    CODE_PLAN_STALE,
    CODE_REQUIREMENTS_NOT_GENERATED,
    CODE_REQUIREMENTS_STALE,
    CODE_REQUIREMENTS_UNASSIGNED,
    CODE_SECONDARY_ACTIVITIES_AVAILABLE,
    CODE_TEACHER_LOAD_IMBALANCED,
)
from reparto_service.services.calculations import (
    AssignmentCalculationService,
    PlanningCalculationService,
)

_ZERO = Decimal("0.00")

_STALE_REQUIREMENT_STATES = {
    HourRequirementStatus.STALE,
    HourRequirementStatus.RECONCILIATION_REQUIRED,
}

_STALE_PLAN_STATES = {
    TeachingPlanStatus.STALE,
    TeachingPlanStatus.RECONCILIATION_REQUIRED,
}


class PlanValidationService:
    """Cheap planning blocking/warning findings (plan §6.3, §6.4)."""

    @staticmethod
    def compute_plan_validations(
        session: Session, plan: TeachingPlan
    ) -> PlanValidationReport:
        """Return every planning finding for ``plan``, blocking-first.

        The plan is assignment-ready (plan §3.10) only when the returned report
        has no blocking finding. No feasibility solve is triggered (plan §20.23).
        """
        process_id = plan.assignment_process_id
        blocking: list[PlanValidationMessage] = []
        warnings: list[PlanValidationMessage] = []

        PlanValidationService._check_balances(session, plan, blocking)
        PlanValidationService._check_main_materialization(session, plan, blocking)
        PlanValidationService._check_activity_links(session, plan, blocking)
        PlanValidationService._check_generation(session, plan, blocking)
        PlanValidationService._check_feasibility(plan, blocking)

        PlanValidationService._warn_overloaded_participants(
            session, process_id, warnings
        )
        PlanValidationService._warn_secondary_available(session, plan, warnings)

        messages = blocking + warnings
        return PlanValidationReport(
            teaching_plan_id=plan.id,
            assignment_process_id=process_id,
            is_assignment_ready=not blocking,
            blocking_count=len(blocking),
            warning_count=len(warnings),
            messages=messages,
        )

    # ── Balances (plan §6.3) ─────────────────────────────────────────────────

    @staticmethod
    def _check_balances(
        session: Session,
        plan: TeachingPlan,
        out: list[PlanValidationMessage],
    ) -> None:
        balance = PlanningCalculationService.compute_plan_balance(session, plan)
        allocation_hours = balance.group.allocated_group_weekly_hours
        if allocation_hours is None:
            out.append(
                _plan_msg(
                    plan,
                    ValidationSeverity.BLOCKING,
                    CODE_MISSING_ALLOCATION,
                    "No current school-leadership allocation revision exists.",
                    {},
                )
            )
        elif not balance.group.is_balanced:
            difference = balance.group.allocation_difference
            assert difference is not None
            out.append(
                _plan_msg(
                    plan,
                    ValidationSeverity.BLOCKING,
                    CODE_GROUP_HOURS_IMBALANCED,
                    (
                        "Group-hour total "
                        f"({balance.group.total_group_load}) differs from the "
                        f"current allocation "
                        f"({balance.group.allocated_group_weekly_hours}); "
                        f"difference {difference}."
                    ),
                    {
                        "group_hours": _hours_text(balance.group.total_group_load),
                        "allocation_hours": _hours_text(allocation_hours),
                        "difference_hours": _signed_hours_text(difference),
                    },
                )
            )
        if not balance.teacher.is_balanced:
            out.append(
                _plan_msg(
                    plan,
                    ValidationSeverity.BLOCKING,
                    CODE_TEACHER_LOAD_IMBALANCED,
                    (
                        "Teacher load "
                        f"({balance.teacher.total_teacher_load}) differs from the "
                        f"participant target total "
                        f"({balance.teacher.participant_target_total}); "
                        f"difference {balance.teacher.teacher_load_difference}."
                    ),
                    {
                        "teacher_hours": _hours_text(
                            balance.teacher.total_teacher_load
                        ),
                        "target_hours": _hours_text(
                            balance.teacher.participant_target_total
                        ),
                        "difference_hours": _signed_hours_text(
                            balance.teacher.teacher_load_difference
                        ),
                    },
                )
            )

    # ── Main materialization (plan §6.3) ─────────────────────────────────────

    @staticmethod
    def _check_main_materialization(
        session: Session,
        plan: TeachingPlan,
        out: list[PlanValidationMessage],
    ) -> None:
        materialized = PlanValidationService._materialized_main_source_ids(
            session, plan
        )
        for cell in PlanValidationService._active_cells_of_category(
            session, plan.assignment_process_id, SubjectAllocationCategory.MAIN
        ):
            if cell.id not in materialized:
                group = session.get(TeachingGroup, cell.teaching_group_id)
                subject = session.get(Subject, cell.subject_id)
                group_label = group.label if group is not None else "Unnamed group"
                subject_label = (
                    subject.name if subject is not None else "Unnamed subject"
                )
                out.append(
                    PlanValidationMessage(
                        severity=ValidationSeverity.BLOCKING,
                        code=CODE_MAIN_SUBJECT_NOT_MATERIALIZED,
                        message=(
                            f"Group {group_label} · {subject_label} has no "
                            "materialized teaching activity."
                        ),
                        params={
                            "group_label": group_label,
                            "subject_label": subject_label,
                        },
                        entity_type="group_subject",
                        entity_id=cell.id,
                    )
                )

    # ── Activity/group links (plan §5.7, §6.3) ───────────────────────────────

    @staticmethod
    def _check_activity_links(
        session: Session,
        plan: TeachingPlan,
        out: list[PlanValidationMessage],
    ) -> None:
        activities = PlanValidationService._live_activities(session, plan)
        links = PlanValidationService._links_by_activity(
            session, [a.id for a in activities]
        )
        for activity in activities:
            cells = links.get(activity.id, [])
            subject = session.get(Subject, activity.subject_id)
            activity_label = subject.name if subject is not None else "Unnamed activity"
            allows_zero = subject is not None and subject.allows_zero_groups
            allows_multiple = subject is not None and subject.allows_multiple_groups

            if activity.sync_state == TeachingActivitySyncState.OUT_OF_SYNC:
                out.append(
                    _activity_msg(
                        activity,
                        CODE_ACTIVITY_OUT_OF_SYNC,
                        (
                            "Main activity differs from its source GroupSubject; "
                            "run sync-preview and explicitly apply the change."
                        ),
                        {"activity_label": activity_label},
                    )
                )

            if not cells and not allows_zero:
                out.append(
                    _activity_msg(
                        activity,
                        CODE_ACTIVITY_MISSING_GROUPS,
                        "Activity links no group but its subject forbids zero groups.",
                        {"activity_label": activity_label},
                    )
                )
            if len(cells) > 1 and not allows_multiple:
                out.append(
                    _activity_msg(
                        activity,
                        CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED,
                        (
                            f"Activity links {len(cells)} groups but its subject "
                            "forbids multiple groups."
                        ),
                        {
                            "activity_label": activity_label,
                            "group_count": len(cells),
                        },
                    )
                )
            if any(cell.subject_id != activity.subject_id for cell in cells):
                out.append(
                    _activity_msg(
                        activity,
                        CODE_ACTIVITY_LINKED_SUBJECT_MISMATCH,
                        "Activity links a group-subject cell of a different subject.",
                        {"activity_label": activity_label},
                    )
                )

    # ── Requirement generation / staleness (plan §6.3, §3.11) ────────────────

    @staticmethod
    def _check_generation(
        session: Session,
        plan: TeachingPlan,
        out: list[PlanValidationMessage],
    ) -> None:
        live_states = session.exec(
            select(HourRequirement.status)
            .where(HourRequirement.assignment_process_id == plan.assignment_process_id)
            .where(col(HourRequirement.retired_generation).is_(None))
        ).all()
        stale_count = 0
        if not live_states:
            out.append(
                _plan_msg(
                    plan,
                    ValidationSeverity.BLOCKING,
                    CODE_REQUIREMENTS_NOT_GENERATED,
                    "No teacher-requirement slots have been generated for the plan.",
                    {},
                )
            )
        else:
            stale_count = sum(
                state in _STALE_REQUIREMENT_STATES for state in live_states
            )
        if live_states and stale_count > 0:
            out.append(
                _plan_msg(
                    plan,
                    ValidationSeverity.BLOCKING,
                    CODE_REQUIREMENTS_STALE,
                    f"{stale_count} generated requirement slot(s) are stale.",
                    {"count": stale_count},
                )
            )
        if plan.status in _STALE_PLAN_STATES:
            out.append(
                _plan_msg(
                    plan,
                    ValidationSeverity.BLOCKING,
                    CODE_PLAN_STALE,
                    f"The plan is {plan.status.value} and must be reconciled.",
                    {"status": plan.status.value},
                )
            )

    # ── Feasibility (plan §20.19 — read only, never solved) ──────────────────

    @staticmethod
    def _check_feasibility(
        plan: TeachingPlan,
        out: list[PlanValidationMessage],
    ) -> None:
        if plan.feasibility_status != FeasibilityStatus.FEASIBLE:
            out.append(
                _plan_msg(
                    plan,
                    ValidationSeverity.BLOCKING,
                    CODE_FEASIBILITY_NOT_CONFIRMED,
                    (
                        "Assignment feasibility is "
                        f"{plan.feasibility_status.value}; a FEASIBLE evaluation is "
                        "required before assignment."
                    ),
                    {"status": plan.feasibility_status.value},
                )
            )

    # ── Warnings (plan §6.4) ─────────────────────────────────────────────────

    @staticmethod
    def _warn_overloaded_participants(
        session: Session,
        process_id: uuid.UUID,
        out: list[PlanValidationMessage],
    ) -> None:
        teachers = session.exec(
            select(ProcessTeacher, TeacherProfile)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.status == ProcessTeacherStatus.ACTIVE)
            .where(ProcessTeacher.teacher_profile_id == TeacherProfile.id)
        ).all()
        overloaded = [
            (teacher, profile)
            for teacher, profile in teachers
            if quantize_hours(teacher.extra_weekly_hours) > _ZERO
        ]
        for teacher, profile in sorted(overloaded, key=lambda row: str(row[0].id)):
            extra_hours = quantize_hours(teacher.extra_weekly_hours)
            out.append(
                PlanValidationMessage(
                    severity=ValidationSeverity.WARNING,
                    code=CODE_PARTICIPANT_OVERLOADED,
                    message=(
                        f"Participant {profile.display_name} has authorized extra "
                        f"hours ({extra_hours})."
                    ),
                    params={
                        "teacher_label": profile.display_name,
                        "extra_hours": _hours_text(extra_hours),
                    },
                    entity_type="teacher",
                    entity_id=teacher.id,
                )
            )

    @staticmethod
    def _warn_secondary_available(
        session: Session,
        plan: TeachingPlan,
        out: list[PlanValidationMessage],
    ) -> None:
        linked = PlanValidationService._linked_cell_ids(session, plan)
        unmaterialized = [
            cell
            for cell in PlanValidationService._active_cells_of_category(
                session, plan.assignment_process_id, SubjectAllocationCategory.SECONDARY
            )
            if cell.id not in linked
        ]
        if unmaterialized:
            out.append(
                _plan_msg(
                    plan,
                    ValidationSeverity.WARNING,
                    CODE_SECONDARY_ACTIVITIES_AVAILABLE,
                    (
                        f"{len(unmaterialized)} optional secondary group-subject "
                        "cell(s) are not yet part of any activity."
                    ),
                    {"count": len(unmaterialized)},
                )
            )

    # ── Shared queries ───────────────────────────────────────────────────────

    @staticmethod
    def _live_activities(
        session: Session, plan: TeachingPlan
    ) -> list[TeachingActivity]:
        return list(
            session.exec(
                select(TeachingActivity)
                .where(TeachingActivity.teaching_plan_id == plan.id)
                .where(col(TeachingActivity.retired_at).is_(None))
                .order_by(col(TeachingActivity.id))
            ).all()
        )

    @staticmethod
    def _links_by_activity(
        session: Session, activity_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[GroupSubject]]:
        if not activity_ids:
            return {}
        rows = session.exec(
            select(TeachingActivityGroup.teaching_activity_id, GroupSubject)
            .where(TeachingActivityGroup.group_subject_id == GroupSubject.id)
            .where(col(TeachingActivityGroup.teaching_activity_id).in_(activity_ids))
        ).all()
        grouped: dict[uuid.UUID, list[GroupSubject]] = {}
        for activity_id, cell in rows:
            grouped.setdefault(activity_id, []).append(cell)
        return grouped

    @staticmethod
    def _linked_cell_ids(session: Session, plan: TeachingPlan) -> set[uuid.UUID]:
        activity_ids = [
            a.id for a in PlanValidationService._live_activities(session, plan)
        ]
        links = PlanValidationService._links_by_activity(session, activity_ids)
        return {cell.id for cells in links.values() for cell in cells}

    @staticmethod
    def _materialized_main_source_ids(
        session: Session, plan: TeachingPlan
    ) -> set[uuid.UUID]:
        rows = session.exec(
            select(TeachingActivity.source_group_subject_id)
            .where(TeachingActivity.teaching_plan_id == plan.id)
            .where(col(TeachingActivity.retired_at).is_(None))
            .where(TeachingActivity.source == TeachingActivitySource.MAIN_GENERATED)
            .where(col(TeachingActivity.source_group_subject_id).is_not(None))
        ).all()
        return {source_id for source_id in rows if source_id is not None}

    @staticmethod
    def _active_cells_of_category(
        session: Session,
        process_id: uuid.UUID,
        category: SubjectAllocationCategory,
    ) -> list[GroupSubject]:
        return list(
            session.exec(
                select(GroupSubject)
                .where(GroupSubject.assignment_process_id == process_id)
                .where(col(GroupSubject.active).is_(True))
                .where(GroupSubject.subject_id == Subject.id)
                .where(Subject.allocation_category == category)
                .order_by(col(GroupSubject.id))
            ).all()
        )


class AssignmentValidationService:
    """Cheap assignment-stage blocking/warning findings (plan §6.3, §6.4).

    The assignment-stage twin of :class:`PlanValidationService`. It reads the
    numeric assignment view of
    :class:`~reparto_service.services.calculations.AssignmentCalculationService`
    (per-participant assigned/remaining hours and the live-slot counts) and turns
    it into stable-``code`` findings. Every check is O(rows) and **solver-free**
    (plan §20.23): the exponential feasibility solver is never run here.

    Findings enforce the plan §3.6/§3.8 assignment invariants at report level:

    * **indivisibility / coverage** — a live requirement slot is either fully
      assigned or unassigned (there is no partial state), so any unoccupied live
      slot is a blocking ``requirement.unassigned`` finding that stops final
      closure;
    * **exact participant targets & no overload bypass** — an active participant
      assigned *above* their target (``participant.over_target``) reveals an
      overload that did not go through the extra-hours flow (plan §3.8), and an
      active, participating teacher still *below* target
      (``participant.below_target``) blocks final closure;
    * **authorized overload** — an active participant with authorized extra hours
      is surfaced as the §6.4 ``teacher.overloaded_authorized`` warning.

    The distinct-teacher rule (plan §3.7) is enforced structurally at write time
    (``AssignmentController._occupy_slot`` plus the DB active partial-unique
    index), so it can never surface as a stored violation and needs no finding.
    """

    @staticmethod
    def compute_assignment_validations(
        session: Session, process: AssignmentProcess
    ) -> AssignmentValidationReport:
        """Return every assignment-stage finding for ``process``, blocking-first.

        The process is ready for final closure (plan §3.10) only when the report
        has no blocking finding. No feasibility solve is triggered (plan §20.23).
        """
        summary = AssignmentCalculationService.compute_assignment_summary(
            session, process
        )
        teachers = {
            teacher.id: teacher
            for teacher in session.exec(
                select(ProcessTeacher).where(
                    ProcessTeacher.assignment_process_id == process.id
                )
            ).all()
        }

        over_target: list[PlanValidationMessage] = []
        below_target: list[PlanValidationMessage] = []
        overloaded: list[PlanValidationMessage] = []
        for participant in summary.participants:
            teacher = teachers[participant.process_teacher_id]
            if teacher.status != ProcessTeacherStatus.ACTIVE:
                continue
            remaining = quantize_hours(participant.remaining_weekly_hours)
            if remaining < _ZERO:
                over_target.append(
                    _teacher_msg(
                        teacher.id,
                        ValidationSeverity.BLOCKING,
                        CODE_PARTICIPANT_OVER_TARGET,
                        (
                            f"Participant {participant.display_name} is assigned "
                            f"{participant.assigned_weekly_hours} hours, above the "
                            f"target of {participant.target_weekly_hours}; increase "
                            "authorized extra hours to allow the overload."
                        ),
                        {
                            "teacher_label": participant.display_name,
                            "assigned_hours": _hours_text(
                                participant.assigned_weekly_hours
                            ),
                            "target_hours": _hours_text(
                                participant.target_weekly_hours
                            ),
                            "difference_hours": _signed_hours_text(-remaining),
                        },
                    )
                )
            elif remaining > _ZERO and teacher.participates_in_selection:
                below_target.append(
                    _teacher_msg(
                        teacher.id,
                        ValidationSeverity.BLOCKING,
                        CODE_PARTICIPANT_BELOW_TARGET,
                        (
                            f"Participant {participant.display_name} is {remaining} "
                            f"hours below the target of "
                            f"{participant.target_weekly_hours}."
                        ),
                        {
                            "teacher_label": participant.display_name,
                            "assigned_hours": _hours_text(
                                participant.assigned_weekly_hours
                            ),
                            "target_hours": _hours_text(
                                participant.target_weekly_hours
                            ),
                            "difference_hours": _signed_hours_text(-remaining),
                        },
                    )
                )
            if participant.is_overloaded:
                overloaded.append(
                    _teacher_msg(
                        teacher.id,
                        ValidationSeverity.WARNING,
                        CODE_PARTICIPANT_OVERLOADED,
                        (
                            f"Participant {participant.display_name} has authorized "
                            f"extra hours ({participant.extra_weekly_hours})."
                        ),
                        {
                            "teacher_label": participant.display_name,
                            "extra_hours": _hours_text(participant.extra_weekly_hours),
                        },
                    )
                )

        blocking: list[PlanValidationMessage] = []
        if summary.available_slots > 0:
            blocking.append(
                PlanValidationMessage(
                    severity=ValidationSeverity.BLOCKING,
                    code=CODE_REQUIREMENTS_UNASSIGNED,
                    message=(
                        f"{summary.available_slots} live requirement slot(s) have no "
                        "active assignment; every slot must be assigned in full."
                    ),
                    params={"count": summary.available_slots},
                    entity_type="assignment_process",
                    entity_id=process.id,
                )
            )
        blocking.extend(sorted(over_target, key=lambda m: str(m.entity_id)))
        blocking.extend(sorted(below_target, key=lambda m: str(m.entity_id)))
        warnings = sorted(overloaded, key=lambda m: str(m.entity_id))

        messages = blocking + warnings
        return AssignmentValidationReport(
            assignment_process_id=process.id,
            is_final_ready=not blocking,
            blocking_count=len(blocking),
            warning_count=len(warnings),
            messages=messages,
        )


def _plan_msg(
    plan: TeachingPlan,
    severity: ValidationSeverity,
    code: str,
    message: str,
    params: dict[str, str | int],
) -> PlanValidationMessage:
    """Build a plan-wide finding pointing at the plan itself."""
    return PlanValidationMessage(
        severity=severity,
        code=code,
        message=message,
        params=params,
        entity_type="plan",
        entity_id=plan.id,
    )


def _activity_msg(
    activity: TeachingActivity,
    code: str,
    message: str,
    params: dict[str, str | int],
) -> PlanValidationMessage:
    """Build a blocking finding pointing at a teaching activity."""
    return PlanValidationMessage(
        severity=ValidationSeverity.BLOCKING,
        code=code,
        message=message,
        params=params,
        entity_type="teaching_activity",
        entity_id=activity.id,
    )


def _teacher_msg(
    teacher_id: uuid.UUID,
    severity: ValidationSeverity,
    code: str,
    message: str,
    params: dict[str, str | int],
) -> PlanValidationMessage:
    """Build a finding pointing at one process teacher."""
    return PlanValidationMessage(
        severity=severity,
        code=code,
        message=message,
        params=params,
        entity_type="teacher",
        entity_id=teacher_id,
    )


def _hours_text(value: Decimal) -> str:
    """Return canonical two-place decimal hours without an explicit plus sign."""
    return str(quantize_hours(value))


def _signed_hours_text(value: Decimal) -> str:
    """Return canonical signed two-place decimal hours for a difference."""
    canonical = quantize_hours(value)
    return f"+{canonical}" if canonical > _ZERO else str(canonical)


__all__ = [
    "CODE_ACTIVITY_LINKED_SUBJECT_MISMATCH",
    "CODE_ACTIVITY_MISSING_GROUPS",
    "CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED",
    "CODE_ACTIVITY_OUT_OF_SYNC",
    "CODE_FEASIBILITY_NOT_CONFIRMED",
    "CODE_GROUP_HOURS_IMBALANCED",
    "CODE_MAIN_SUBJECT_NOT_MATERIALIZED",
    "CODE_MISSING_ALLOCATION",
    "CODE_PARTICIPANT_BELOW_TARGET",
    "CODE_PARTICIPANT_OVERLOADED",
    "CODE_PARTICIPANT_OVER_TARGET",
    "CODE_PLAN_STALE",
    "CODE_REQUIREMENTS_NOT_GENERATED",
    "CODE_REQUIREMENTS_STALE",
    "CODE_REQUIREMENTS_UNASSIGNED",
    "CODE_SECONDARY_ACTIVITIES_AVAILABLE",
    "CODE_TEACHER_LOAD_IMBALANCED",
    "AssignmentValidationService",
    "PlanValidationService",
]
