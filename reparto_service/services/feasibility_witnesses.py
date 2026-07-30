"""Persist, validate, invalidate and repair feasibility witnesses.

Full evaluation is an explicit administrator operation.  Assignment hot paths
only validate the current fingerprint and perform the bounded local repair from
``selection_guards``; they never invoke the NP-hard solver (plan 20.24).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.feasibility_witnesses import (
    FeasibilityEvaluationPublic,
    FeasibilityWitness,
    FeasibilityWitnessEntryPublic,
    FeasibilityWitnessPublic,
)
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import AssignmentStatus, FeasibilityStatus
from reparto_service.services.feasibility import (
    SOLVER_VERSION,
    FeasibilityResult,
    FeasibilityState,
    FeasibilityWitnessEntry,
    evaluate_assignment_feasibility,
    hours_to_units,
)
from reparto_service.services.selection_guards import (
    WitnessRepairResult,
    build_remaining_assignment_state,
    validate_feasibility_witness,
    validate_proposed_assignment_against_witness,
)


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

    state = build_remaining_assignment_state(session, process_id)
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
    fixed = tuple(
        FeasibilityWitnessEntry(
            str(item.hour_requirement_id), str(item.process_teacher_id)
        )
        for item in assignments
    )
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
                "id": str(item.id),
                "activity_id": str(item.teaching_activity_id),
                "position_index": item.position_index,
                "hours_units": hours_to_units(str(item.required_teacher_hours)),
            }
            for item in requirements
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
    fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return FeasibilitySnapshot(state, fingerprint, fixed)


class FeasibilityWitnessService:
    """Database orchestration around the pure solver and local repair."""

    @staticmethod
    def evaluate(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityEvaluationPublic:
        """Evaluate or reuse the exact current fingerprint and persist its witness."""

        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        snapshot = build_feasibility_snapshot(session, process_id)
        cached = FeasibilityWitnessService._cached_result(session, plan, snapshot)
        if cached is not None:
            return cached
        result = evaluate_assignment_feasibility(snapshot.state)
        checked_at = datetime.now(tz=timezone.utc)
        FeasibilityWitnessService._persist_result(
            session, plan, snapshot, result, checked_at
        )
        session.commit()
        session.refresh(plan)
        return FeasibilityWitnessService._evaluation_public(
            plan,
            result=result,
            cache_reused=False,
            witness_available=result.witness is not None,
        )

    @staticmethod
    def get_witness(
        session: Session, process_id: uuid.UUID
    ) -> FeasibilityWitnessPublic:
        """Return the current complete witness or fail closed when it is stale."""

        plan = FeasibilityWitnessService._plan_or_404(session, process_id)
        snapshot = build_feasibility_snapshot(session, process_id)
        row = FeasibilityWitnessService._current_row(session, plan, snapshot)
        if row is None or plan.feasibility_status != FeasibilityStatus.FEASIBLE:
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
        if row is None:
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
    def invalidate(session: Session, process_id: uuid.UUID) -> None:
        """Immediately remove cached provenance and witness after an input mutation."""

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
            return
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

    @staticmethod
    def _cached_result(
        session: Session,
        plan: TeachingPlan,
        snapshot: FeasibilitySnapshot,
    ) -> FeasibilityEvaluationPublic | None:
        if (
            plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED
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
    ) -> None:
        plan.feasibility_status = result.status
        plan.feasibility_generation = plan.current_generation_number
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
]
