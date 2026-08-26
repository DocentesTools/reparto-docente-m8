"""Persist, validate, invalidate and repair feasibility witnesses.

Full evaluation is an explicit administrator operation.  Assignment hot paths
only validate the current fingerprint and perform the bounded local repair from
``selection_guards``; they never invoke the NP-hard solver (plan 20.24).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.feasibility_witnesses import (
    FeasibilityDiagnosticPublic,
    FeasibilityDiagnosticsPublic,
    FeasibilityEvaluationPublic,
    FeasibilityWitness,
    FeasibilityWitnessEntryPublic,
    FeasibilityWitnessPublic,
)
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.teaching_activities import TeachingActivity
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentStatus,
    FeasibilityStatus,
    ProcessTeacherStatus,
    SseEventType,
    TeachingPlanStatus,
)
from reparto_service.services.feasibility import (
    DEFAULT_SOLVER_LIMITS,
    SOLVER_VERSION,
    FeasibilityParticipant,
    FeasibilityResult,
    FeasibilitySlot,
    FeasibilityState,
    FeasibilityWitnessEntry,
    evaluate_assignment_feasibility,
    hours_to_units,
)
from reparto_service.services.feasibility_controls import serialize_feasibility_solve
from reparto_service.services.selection_guards import (
    WitnessRepairResult,
    validate_feasibility_witness,
    validate_proposed_assignment_against_witness,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FeasibilitySnapshot:
    """Current solver state plus its complete deterministic input fingerprint."""

    state: FeasibilityState
    fingerprint: str
    fixed_assignments: tuple[FeasibilityWitnessEntry, ...]


def build_feasibility_snapshot(
    session: Session, process_id: uuid.UUID
) -> FeasibilitySnapshot:
    """Build the remaining state and hash every input that affects a solution."""

    requirements = list(
        session.exec(
            select(HourRequirement)
            .where(HourRequirement.assignment_process_id == process_id)
            .where(col(HourRequirement.retired_generation).is_(None))
            .order_by(
                col(HourRequirement.teaching_activity_id),
                col(HourRequirement.position_index),
                col(HourRequirement.id),
            )
        ).all()
    )
    assignments = list(
        session.exec(
            select(Assignment)
            .where(Assignment.assignment_process_id == process_id)
            .where(Assignment.status == AssignmentStatus.ACTIVE)
            .order_by(col(Assignment.hour_requirement_id))
        ).all()
    )
    slots = tuple(
        FeasibilitySlot(
            str(item.id),
            str(item.teaching_activity_id),
            item.position_index,
            hours_to_units(str(item.required_teacher_hours)),
        )
        for item in requirements
    )
    fixed = {
        str(item.hour_requirement_id): str(item.process_teacher_id)
        for item in assignments
    }
    return _snapshot_from_slots(session, process_id, slots, fixed)


def build_intended_feasibility_snapshot(
    session: Session, process_id: uuid.UUID
) -> FeasibilitySnapshot:
    """Build the exact post-generation/reconciliation solver state."""

    plan = FeasibilityWitnessService._plan_or_404(session, process_id)
    requirements = {
        (item.teaching_activity_id, item.position_index): item
        for item in session.exec(
            select(HourRequirement)
            .where(HourRequirement.assignment_process_id == process_id)
            .where(col(HourRequirement.retired_generation).is_(None))
        ).all()
    }
    assignments = {
        item.hour_requirement_id: item
        for item in session.exec(
            select(Assignment)
            .where(Assignment.assignment_process_id == process_id)
            .where(Assignment.status == AssignmentStatus.ACTIVE)
        ).all()
    }
    slots, fixed = _intended_slots(session, plan, requirements, assignments)
    return _snapshot_from_slots(session, process_id, slots, fixed)


def prospective_requirement_id(
    plan_id: uuid.UUID,
    generation: int,
    activity_id: uuid.UUID,
    position_index: int,
) -> uuid.UUID:
    """Return the stable row id used by preview, solver and generation apply."""

    return uuid.uuid5(
        plan_id,
        f"requirement:{generation}:{activity_id}:{position_index}",
    )


def _intended_slots(
    session: Session,
    plan: TeachingPlan,
    requirements: dict[tuple[uuid.UUID, int], HourRequirement],
    assignments: dict[uuid.UUID, Assignment],
) -> tuple[tuple[FeasibilitySlot, ...], dict[str, str]]:
    slots: list[FeasibilitySlot] = []
    fixed: dict[str, str] = {}
    generation = plan.current_generation_number + 1
    activities = session.exec(
        select(TeachingActivity)
        .where(TeachingActivity.teaching_plan_id == plan.id)
        .where(col(TeachingActivity.retired_at).is_(None))
        .order_by(col(TeachingActivity.id))
    ).all()
    for activity in activities:
        units = hours_to_units(str(activity.teacher_weekly_hours_per_position))
        for position in range(activity.required_teacher_count):
            current = requirements.get((activity.id, position))
            current_matches = (
                current is not None
                and hours_to_units(str(current.required_teacher_hours)) == units
            )
            if current_matches and current is not None:
                slot_id = current.id
            else:
                slot_id = prospective_requirement_id(
                    plan.id, generation, activity.id, position
                )
            slots.append(
                FeasibilitySlot(str(slot_id), str(activity.id), position, units)
            )
            if current_matches and current is not None and current.id in assignments:
                fixed[str(slot_id)] = str(assignments[current.id].process_teacher_id)
    return tuple(slots), fixed


def _snapshot_from_slots(
    session: Session,
    process_id: uuid.UUID,
    slots: tuple[FeasibilitySlot, ...],
    fixed_by_slot: dict[str, str],
) -> FeasibilitySnapshot:
    participants = list(
        session.exec(
            select(ProcessTeacher)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.status == ProcessTeacherStatus.ACTIVE)
        ).all()
    )
    slot_by_id = {item.slot_id: item for item in slots}
    assigned_units: dict[str, int] = defaultdict(int)
    occupied: dict[str, set[str]] = defaultdict(set)
    for slot_id, participant_id in fixed_by_slot.items():
        slot = slot_by_id[slot_id]
        assigned_units[participant_id] += slot.hours_units
        occupied[participant_id].add(slot.activity_id)
    state = FeasibilityState(
        participants=tuple(
            FeasibilityParticipant(
                str(item.id),
                hours_to_units(str(item.target_weekly_hours))
                - assigned_units[str(item.id)],
                frozenset(occupied[str(item.id)]),
            )
            for item in participants
        ),
        slots=tuple(item for item in slots if item.slot_id not in fixed_by_slot),
    )
    fixed = tuple(
        FeasibilityWitnessEntry(slot_id, participant_id)
        for slot_id, participant_id in sorted(fixed_by_slot.items())
    )
    return FeasibilitySnapshot(state, _fingerprint(state, slots, fixed), fixed)


def _fingerprint(
    state: FeasibilityState,
    slots: tuple[FeasibilitySlot, ...],
    fixed: tuple[FeasibilityWitnessEntry, ...],
) -> str:
    payload = {
        "solver_version": SOLVER_VERSION,
        "participants": [
            {
                "id": item.participant_id,
                "remaining_target_units": item.remaining_target_units,
                "occupied_activity_ids": sorted(item.occupied_activity_ids),
            }
            for item in state.participants
        ],
        "slots": [
            {
                "id": item.slot_id,
                "activity_id": item.activity_id,
                "position_index": item.position_index,
                "hours_units": item.hours_units,
            }
            for item in slots
        ],
        "assignments": [
            {
                "slot_id": item.slot_id,
                "participant_id": item.participant_id,
            }
            for item in fixed
        ],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


#: Plan statuses that hold no requirement rows because generation has not run
#: (plan §20.14). In these the only feasibility question with an answer is about
#: the state generation *would* produce — see :meth:`FeasibilityWitnessService.evaluate`.
_PRE_LOCK_PLAN_STATUSES: frozenset[TeachingPlanStatus] = frozenset(
    {
        TeachingPlanStatus.DRAFT,
        TeachingPlanStatus.UNBALANCED,
        TeachingPlanStatus.BALANCED,
    }
)


def _has_live_requirements(session: Session, process_id: uuid.UUID) -> bool:
    """Whether the process holds a requirement slot generation has not retired."""

    return (
        session.exec(
            select(HourRequirement.id)
            .where(HourRequirement.assignment_process_id == process_id)
            .where(col(HourRequirement.retired_generation).is_(None))
            .limit(1)
        ).first()
        is not None
    )


class FeasibilityWitnessService:
    """Database orchestration around the pure solver and local repair."""

    @staticmethod
    def evaluate(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityEvaluationPublic:
        """Evaluate or reuse the exact current fingerprint and persist its witness.

        An unlocked plan has no requirement rows yet, so the current-state
        snapshot holds no slots at all: it would weigh every participant target
        against zero slot hours and answer ``INFEASIBLE`` for a structural
        reason that says nothing about the plan. It would also be a permanent
        answer — the only route out is generation, and §20.1 will not lock, let
        alone generate, until feasibility is confirmed.

        The state an unlocked plan is actually being asked about is the one
        generation would produce, which is exactly what the lock and generation
        gates already enforce through :meth:`require_intended_feasible`. So
        evaluate that, and let the status this stores be the status those gates
        will act on rather than a second, contradictory one (§13.6 walk-through).

        Both halves of the condition below are load-bearing. No live requirement
        row is what makes the current-state snapshot unable to answer at all; an
        unlocked status is what makes ``current_generation_number`` the
        generation whose *successor* the intended snapshot describes, so the
        stored generation stays the one the witness readers expect. From
        ``LOCKED`` onwards the live rows are the state, and the stored generation
        has to keep matching ``current_generation_number`` so the witness reads
        that gate assignment stay current.
        """

        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        if plan.status in _PRE_LOCK_PLAN_STATUSES and not _has_live_requirements(
            session, process_id
        ):
            return FeasibilityWitnessService.evaluate_intended(session, process_id)
        snapshot = build_feasibility_snapshot(session, process_id)
        return FeasibilityWitnessService._evaluate_snapshot(
            session,
            plan,
            snapshot,
            generation=plan.current_generation_number,
        )

    @staticmethod
    def evaluate_intended(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityEvaluationPublic:
        """Evaluate the exact state produced by the next generation/reconciliation."""

        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        snapshot = build_intended_feasibility_snapshot(session, process_id)
        return FeasibilityWitnessService._evaluate_snapshot(
            session,
            plan,
            snapshot,
            generation=plan.current_generation_number + 1,
        )

    @staticmethod
    def require_intended_feasible(
        session: Session, process_id: uuid.UUID, *, operation: str
    ) -> FeasibilityEvaluationPublic:
        """Run/reuse the intended-state solve and fail closed unless FEASIBLE."""

        evaluation = FeasibilityWitnessService.evaluate_intended(session, process_id)
        if (
            evaluation.status != FeasibilityStatus.FEASIBLE
            or not evaluation.witness_available
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot {operation}: assignment feasibility is "
                    f"{evaluation.status.value}; a current FEASIBLE result is required."
                ),
            )
        return evaluation

    @staticmethod
    def _evaluate_snapshot(
        session: Session,
        plan: TeachingPlan,
        snapshot: FeasibilitySnapshot,
        *,
        generation: int,
    ) -> FeasibilityEvaluationPublic:
        started_at = time.monotonic()
        with serialize_feasibility_solve(session, plan.assignment_process_id):
            cached = FeasibilityWitnessService._cached_result(
                session, plan, snapshot, generation=generation
            )
            if cached is not None:
                FeasibilityWitnessService._log_evaluation(
                    snapshot,
                    status_value=cached.status.value,
                    cache_reused=True,
                    states_explored=0,
                    memoization_hits=0,
                    budget_outcome="not_run",
                    started_at=started_at,
                )
                return cached
            result = evaluate_assignment_feasibility(snapshot.state)
            checked_at = datetime.now(tz=timezone.utc)
            FeasibilityWitnessService._persist_result(
                session, plan, snapshot, result, checked_at, generation=generation
            )
            session.commit()
            session.refresh(plan)
            FeasibilityWitnessService._log_evaluation(
                snapshot,
                status_value=result.status.value,
                cache_reused=False,
                states_explored=result.states_explored,
                memoization_hits=result.memoization_hits,
                budget_outcome=(
                    result.diagnostics[0].code.value
                    if result.status == FeasibilityStatus.UNKNOWN and result.diagnostics
                    else "completed"
                ),
                started_at=started_at,
            )
            evaluation = FeasibilityWitnessService._evaluation_public(
                plan,
                result=result,
                cache_reused=False,
                witness_available=result.witness is not None,
            )
            FeasibilityWitnessService._publish_evaluation(
                session, plan, result, evaluation, started_at=started_at
            )
            return evaluation

    @staticmethod
    def _publish_evaluation(
        session: Session,
        plan: TeachingPlan,
        result: FeasibilityResult,
        evaluation: FeasibilityEvaluationPublic,
        *,
        started_at: float,
    ) -> None:
        """Announce a newly persisted feasibility result (plan §11, §20.25).

        The single emit site for ``teaching_plan.feasibility_updated``: every
        administrative entry point — the explicit evaluate endpoint, plan lock,
        requirement generation and reconciliation — reaches a persisted result
        through :meth:`_evaluate_snapshot`, and a reused cache publishes nothing
        because no status transitioned.

        The payload is the department-head tier's by construction: the projection
        drops it entirely for the teacher and shared-screen tiers, which see only
        the derived readiness (§20.25). It summarises the diagnostics by stable
        code and names the activities/slots they refer to, so a head can react
        without a second request — but it never carries the witness, which stays
        in its restricted store (§20.24).
        """
        # Imported here, not at module scope: the readiness projection in
        # ``services.sse`` reads the lifecycle gates, which read this module.
        from reparto_service.services.sse import publish_domain_event

        related_ids: list[str] = []
        for diagnostic in result.diagnostics:
            for related_id in diagnostic.related_ids:
                text = str(related_id)
                if text not in related_ids:
                    related_ids.append(text)
        publish_domain_event(
            session,
            process_id=plan.assignment_process_id,
            event_type=SseEventType.TEACHING_PLAN_FEASIBILITY_UPDATED,
            payload={
                "teaching_plan_id": str(plan.id),
                "feasibility_status": evaluation.status.value,
                "feasibility_checked_at": (
                    evaluation.checked_at.isoformat()
                    if evaluation.checked_at is not None
                    else None
                ),
                "solver_version": evaluation.solver_version,
                "witness_available": evaluation.witness_available,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
                "diagnostic_codes": [
                    diagnostic.code.value for diagnostic in result.diagnostics
                ],
                "affected_ids": related_ids,
            },
        )

    @staticmethod
    def _log_evaluation(
        snapshot: FeasibilitySnapshot,
        *,
        status_value: str,
        cache_reused: bool,
        states_explored: int,
        memoization_hits: int,
        budget_outcome: str,
        started_at: float,
    ) -> None:
        """Emit bounded solver telemetry without IDs, names or fingerprints."""

        elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))
        logger.info(
            "feasibility_evaluation status=%s cache_reused=%s "
            "participant_count=%d slot_count=%d states_explored=%d "
            "memoization_hits=%d budget_outcome=%s max_participants=%d "
            "max_slots=%d max_steps=%d max_seconds=%s elapsed_ms=%d",
            status_value,
            cache_reused,
            len(snapshot.state.participants),
            len(snapshot.state.slots),
            states_explored,
            memoization_hits,
            budget_outcome,
            DEFAULT_SOLVER_LIMITS.max_participants,
            DEFAULT_SOLVER_LIMITS.max_slots,
            DEFAULT_SOLVER_LIMITS.max_steps,
            DEFAULT_SOLVER_LIMITS.max_seconds,
            elapsed_ms,
        )

    @staticmethod
    def get_witness(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityWitnessPublic:
        """Return the current complete witness or fail closed when it is stale."""

        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        snapshot = build_feasibility_snapshot(session, process_id)
        row = FeasibilityWitnessService._current_row(session, plan, snapshot)
        if (
            row is None
            or plan.feasibility_status != FeasibilityStatus.FEASIBLE
            or plan.feasibility_generation != plan.current_generation_number
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A current deterministic witness is unavailable; an "
                    "administrative feasibility evaluation is required."
                ),
            )
        return FeasibilityWitnessPublic(
            teaching_plan_id=plan.id,
            assignment_process_id=process_id,
            input_fingerprint=row.input_fingerprint,
            solver_version=row.solver_version,
            checked_at=plan.feasibility_checked_at or row.updated_at,
            witness=[
                FeasibilityWitnessEntryPublic(
                    slot_id=uuid.UUID(item["slot_id"]),
                    process_teacher_id=uuid.UUID(item["participant_id"]),
                )
                for item in row.witness_json
            ],
        )

    @staticmethod
    def get_diagnostics(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityDiagnosticsPublic:
        """Return the latest evaluation's findings or fail closed when stale.

        The findings are administration-only (plan §7.3, §20.24): they name the
        concrete slots/activities a remediation must touch, so they live behind
        the same gate as the witness without ever carrying the witness itself.
        Any input mutation has already invalidated the cached row, so a missing
        or mismatched fingerprint/generation means a fresh evaluation is due.
        """

        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        snapshot = build_feasibility_snapshot(session, process_id)
        row = FeasibilityWitnessService._current_row(session, plan, snapshot)
        if (
            row is None
            or plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED
            or plan.feasibility_generation != plan.current_generation_number
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Current feasibility diagnostics are unavailable; an "
                    "administrative feasibility evaluation is required."
                ),
            )
        return FeasibilityDiagnosticsPublic(
            teaching_plan_id=plan.id,
            assignment_process_id=process_id,
            status=plan.feasibility_status,
            checked_at=plan.feasibility_checked_at or row.updated_at,
            diagnostics=[
                FeasibilityDiagnosticPublic(
                    code=item["code"],
                    message=item["message"],
                    related_ids=[
                        uuid.UUID(str(value)) for value in item["related_ids"]
                    ],
                )
                for item in row.diagnostics_json
            ],
        )

    @staticmethod
    def repair_for_selection(
        session: Session,
        *,
        process_id: uuid.UUID,
        proposed_slot_id: uuid.UUID,
        proposed_participant_id: uuid.UUID,
    ) -> WitnessRepairResult:
        """Load and boundedly repair the current witness for one proposal."""

        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        snapshot = build_feasibility_snapshot(session, process_id)
        row = FeasibilityWitnessService._current_row(session, plan, snapshot)
        if (
            row is None
            or plan.feasibility_status != FeasibilityStatus.FEASIBLE
            or plan.feasibility_generation != plan.current_generation_number
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Selection is blocked because the deterministic witness is "
                    "missing or stale; administrative feasibility evaluation is "
                    "required."
                ),
            )
        remaining_ids = {item.slot_id for item in snapshot.state.slots}
        remaining = tuple(
            FeasibilityWitnessEntry(item["slot_id"], item["participant_id"])
            for item in row.witness_json
            if item["slot_id"] in remaining_ids
        )
        return validate_proposed_assignment_against_witness(
            snapshot.state,
            remaining,
            proposed_slot_id=str(proposed_slot_id),
            proposed_participant_id=str(proposed_participant_id),
        )

    @staticmethod
    def repair_for_reassignment(
        session: Session,
        *,
        process_id: uuid.UUID,
        assignment: Assignment,
        requirement: HourRequirement,
        proposed_participant_id: uuid.UUID,
    ) -> WitnessRepairResult | None:
        """Repair a current witness around an atomic undo plus replacement.

        A pure undo cannot destroy feasibility, so the existing complete witness
        remains a valid starting point after releasing the old fixed pair. The
        replacement is checked with the same cheap guards and bounded local
        repair as a normal selection, without invoking the full solver.
        """

        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        if plan.feasibility_status != FeasibilityStatus.FEASIBLE:
            return None
        snapshot = build_feasibility_snapshot(session, process_id)
        row = FeasibilityWitnessService._current_row(session, plan, snapshot)
        if row is None or plan.feasibility_generation != plan.current_generation_number:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Reassignment is blocked because the deterministic witness is "
                    "missing or stale; administrative feasibility evaluation is "
                    "required."
                ),
            )
        released_state = FeasibilityWitnessService._released_assignment_state(
            snapshot.state, assignment, requirement
        )
        remaining_ids = {item.slot_id for item in released_state.slots}
        remaining = tuple(
            FeasibilityWitnessEntry(item["slot_id"], item["participant_id"])
            for item in row.witness_json
            if item["slot_id"] in remaining_ids
        )
        return validate_proposed_assignment_against_witness(
            released_state,
            remaining,
            proposed_slot_id=str(requirement.id),
            proposed_participant_id=str(proposed_participant_id),
        )

    @staticmethod
    def _released_assignment_state(
        state: FeasibilityState,
        assignment: Assignment,
        requirement: HourRequirement,
    ) -> FeasibilityState:
        """Return the hypothetical remaining state after releasing one pair."""

        participant_id = str(assignment.process_teacher_id)
        activity_id = str(requirement.teaching_activity_id)
        slot = FeasibilitySlot(
            str(requirement.id),
            activity_id,
            requirement.position_index,
            hours_to_units(str(requirement.required_teacher_hours)),
        )
        participants = tuple(
            FeasibilityParticipant(
                item.participant_id,
                item.remaining_target_units + slot.hours_units,
                item.occupied_activity_ids - {activity_id},
            )
            if item.participant_id == participant_id
            else item
            for item in state.participants
        )
        slots = tuple(
            sorted(
                (*state.slots, slot),
                key=lambda item: (
                    -item.hours_units,
                    item.activity_id,
                    item.position_index,
                    item.slot_id,
                ),
            )
        )
        return FeasibilityState(participants=participants, slots=slots)

    @staticmethod
    def persist_repair(
        session: Session,
        *,
        process_id: uuid.UUID,
        repaired_remaining: tuple[FeasibilityWitnessEntry, ...],
    ) -> None:
        """Persist a repaired witness against the post-selection fingerprint."""

        session.flush()
        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        snapshot = build_feasibility_snapshot(session, process_id)
        if not validate_feasibility_witness(snapshot.state, repaired_remaining):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The repaired deterministic witness is inconsistent.",
            )
        complete = snapshot.fixed_assignments + repaired_remaining
        FeasibilityWitnessService._upsert_row(
            session,
            plan,
            snapshot,
            complete,
            diagnostics=[],
        )
        plan.feasibility_status = FeasibilityStatus.FEASIBLE
        plan.feasibility_generation = plan.current_generation_number
        plan.feasibility_checked_at = datetime.now(tz=timezone.utc)
        plan.feasibility_input_fingerprint = snapshot.fingerprint
        plan.feasibility_solver_version = SOLVER_VERSION
        session.add(plan)

    @staticmethod
    def invalidate(session: Session, process_id: uuid.UUID) -> bool:
        """Immediately remove cached provenance and witness after an input mutation.

        Returns whether a stored evaluation was actually discarded. Invalidation
        is called unconditionally on every mutating path, so most calls find a
        plan that is already ``NOT_EVALUATED`` (or no plan at all); only a real
        transition is worth announcing on the stream, and the caller publishes
        ``teaching_plan.feasibility_invalidated`` after its commit when this
        returns ``True``.
        """
        # Mutation controllers may already hold a newly added row whose eventual
        # commit is expected to raise a handled uniqueness error. Looking up the
        # cache must not autoflush that row before the controller's try/rollback.
        with session.no_autoflush:
            plan = session.exec(
                select(TeachingPlan).where(
                    TeachingPlan.assignment_process_id == process_id
                )
            ).first()
        if plan is None:
            return False
        invalidated = plan.feasibility_status != FeasibilityStatus.NOT_EVALUATED
        plan.feasibility_status = FeasibilityStatus.NOT_EVALUATED
        plan.feasibility_generation = None
        plan.feasibility_checked_at = None
        plan.feasibility_input_fingerprint = None
        plan.feasibility_solver_version = None
        plan.feasibility_diagnostics_ref = None
        session.add(plan)
        with session.no_autoflush:
            row = FeasibilityWitnessService._row(session, plan.id)
        if row is not None:
            session.delete(row)
        return invalidated

    @staticmethod
    def _cached_result(
        session: Session,
        plan: TeachingPlan,
        snapshot: FeasibilitySnapshot,
        *,
        generation: int,
    ) -> FeasibilityEvaluationPublic | None:
        if (
            plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED
            or plan.feasibility_generation != generation
            or plan.feasibility_input_fingerprint != snapshot.fingerprint
            or plan.feasibility_solver_version != SOLVER_VERSION
            or plan.feasibility_checked_at is None
        ):
            return None
        witness_available = (
            plan.feasibility_status == FeasibilityStatus.FEASIBLE
            and FeasibilityWitnessService._current_row(session, plan, snapshot)
            is not None
        )
        if (
            plan.feasibility_status == FeasibilityStatus.FEASIBLE
            and not witness_available
        ):
            return None
        return FeasibilityWitnessService._evaluation_public(
            plan,
            result=None,
            cache_reused=True,
            witness_available=witness_available,
        )

    @staticmethod
    def _persist_result(
        session: Session,
        plan: TeachingPlan,
        snapshot: FeasibilitySnapshot,
        result: FeasibilityResult,
        checked_at: datetime,
        *,
        generation: int,
    ) -> None:
        plan.feasibility_status = result.status
        plan.feasibility_generation = generation
        plan.feasibility_checked_at = checked_at
        plan.feasibility_input_fingerprint = snapshot.fingerprint
        plan.feasibility_solver_version = result.solver_version
        plan.feasibility_diagnostics_ref = (
            f"feasibility-witness:{plan.id}" if result.diagnostics else None
        )
        session.add(plan)
        FeasibilityWitnessService._upsert_row(
            session,
            plan,
            snapshot,
            snapshot.fixed_assignments + (result.witness or ()),
            diagnostics=[
                {
                    "code": item.code.value,
                    "message": item.message,
                    "related_ids": list(item.related_ids),
                }
                for item in result.diagnostics
            ],
        )

    @staticmethod
    def _upsert_row(
        session: Session,
        plan: TeachingPlan,
        snapshot: FeasibilitySnapshot,
        witness: tuple[FeasibilityWitnessEntry, ...],
        *,
        diagnostics: list[dict[str, Any]],
    ) -> None:
        row = FeasibilityWitnessService._row(session, plan.id)
        if row is None:
            row = FeasibilityWitness(
                teaching_plan_id=plan.id,
                assignment_process_id=plan.assignment_process_id,
                input_fingerprint=snapshot.fingerprint,
                solver_version=SOLVER_VERSION,
                witness_json=[],
            )
        row.input_fingerprint = snapshot.fingerprint
        row.solver_version = SOLVER_VERSION
        row.witness_json = [
            {"slot_id": item.slot_id, "participant_id": item.participant_id}
            for item in sorted(witness, key=lambda item: item.slot_id)
        ]
        row.diagnostics_json = diagnostics
        session.add(row)

    @staticmethod
    def _current_row(
        session: Session, plan: TeachingPlan, snapshot: FeasibilitySnapshot
    ) -> FeasibilityWitness | None:
        if (
            plan.feasibility_input_fingerprint != snapshot.fingerprint
            or plan.feasibility_solver_version != SOLVER_VERSION
        ):
            return None
        row = FeasibilityWitnessService._row(session, plan.id)
        if (
            row is None
            or row.input_fingerprint != snapshot.fingerprint
            or row.solver_version != SOLVER_VERSION
        ):
            return None
        return row

    @staticmethod
    def _row(session: Session, plan_id: uuid.UUID) -> FeasibilityWitness | None:
        return session.exec(
            select(FeasibilityWitness).where(
                FeasibilityWitness.teaching_plan_id == plan_id
            )
        ).first()

    @staticmethod
    def _plan_or_404(session: Session, process_id: uuid.UUID) -> TeachingPlan:
        plan = session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No teaching plan for process {process_id}.",
            )
        return plan

    @staticmethod
    def _evaluation_public(
        plan: TeachingPlan,
        *,
        result: FeasibilityResult | None,
        cache_reused: bool,
        witness_available: bool,
    ) -> FeasibilityEvaluationPublic:
        return FeasibilityEvaluationPublic(
            teaching_plan_id=plan.id,
            assignment_process_id=plan.assignment_process_id,
            status=plan.feasibility_status,
            input_fingerprint=plan.feasibility_input_fingerprint or "",
            solver_version=plan.feasibility_solver_version or SOLVER_VERSION,
            checked_at=plan.feasibility_checked_at or datetime.now(tz=timezone.utc),
            cache_reused=cache_reused,
            witness_available=witness_available,
            states_explored=0 if result is None else result.states_explored,
            memoization_hits=0 if result is None else result.memoization_hits,
        )


__all__ = [
    "FeasibilitySnapshot",
    "FeasibilityWitnessService",
    "build_feasibility_snapshot",
    "build_intended_feasibility_snapshot",
    "prospective_requirement_id",
]
