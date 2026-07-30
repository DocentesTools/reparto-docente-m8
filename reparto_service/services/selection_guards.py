"""Cheap in-transaction feasibility guards for one proposed selection.

This module deliberately contains no call to the bounded full solver.  It builds
the current remaining assignment state, applies one proposed complete-slot
selection, and runs only polynomial checks: residual totals, slot fit,
oversized-slot detection and per-activity bipartite matching (plan §20.5).

Witness validation and repair are also local: repair starts from an already
complete deterministic witness and permits only a bounded number of
balance-improving slot moves.  Witness persistence is a separate orchestration
concern (plan §20.20).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from sqlmodel import Session, col, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.enums import (
    AssignmentStatus,
    HourRequirementStatus,
    ProcessTeacherStatus,
)
from reparto_service.services.feasibility import (
    FeasibilityParticipant,
    FeasibilitySlot,
    FeasibilityState,
    FeasibilityWitnessEntry,
    hours_to_units,
)


class FastGuardCode(str, Enum):
    """Stable reasons why a proposed selection is not cheaply safe."""

    RESIDUAL_TOTALS_MISMATCH = "residual_totals_mismatch"
    SELECTED_SLOT_DOES_NOT_FIT = "selected_slot_does_not_fit"
    SELECTED_ACTIVITY_ALREADY_OCCUPIED = "selected_activity_already_occupied"
    SLOT_EXCEEDS_EVERY_TARGET = "slot_exceeds_every_target"
    DISTINCT_TEACHER_SHORTFALL = "distinct_teacher_shortfall"


class WitnessRepairCode(str, Enum):
    """Stable bounded-witness repair outcomes."""

    REPAIRED = "repaired"
    INVALID_WITNESS = "invalid_witness"
    FAST_GUARD_FAILED = "fast_guard_failed"
    REPAIR_LIMIT_REACHED = "repair_limit_reached"
    LOCAL_REPAIR_NOT_FOUND = "local_repair_not_found"


@dataclass(frozen=True, slots=True)
class FastGuardFinding:
    """One deterministic cheap-guard refusal."""

    code: FastGuardCode
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FastGuardResult:
    """Prospective remaining state and every cheap finding."""

    prospective_state: FeasibilityState
    findings: tuple[FastGuardFinding, ...]

    @property
    def is_safe(self) -> bool:
        """Return whether every cheap guard accepted the proposal."""

        return not self.findings


@dataclass(frozen=True, slots=True)
class WitnessRepairLimits:
    """Hard bounds for local witness repair."""

    max_steps: int = 256
    max_moves: int = 8

    def __post_init__(self) -> None:
        """Reject negative local-search bounds."""

        if self.max_steps < 0 or self.max_moves < 0:
            raise ValueError("witness repair limits must be non-negative")


@dataclass(frozen=True, slots=True)
class WitnessRepairResult:
    """Result of validating and locally repairing a deterministic witness."""

    code: WitnessRepairCode
    witness: tuple[FeasibilityWitnessEntry, ...] | None
    steps: int

    @property
    def repaired(self) -> bool:
        """Return whether a valid prospective witness was produced."""

        return self.code == WitnessRepairCode.REPAIRED


DEFAULT_WITNESS_REPAIR_LIMITS = WitnessRepairLimits()


def build_remaining_assignment_state(
    session: Session, process_id: uuid.UUID
) -> FeasibilityState:
    """Build the current remaining state using only cheap indexed reads."""

    participants = list(
        session.exec(
            select(ProcessTeacher)
            .where(ProcessTeacher.assignment_process_id == process_id)
            .where(ProcessTeacher.status == ProcessTeacherStatus.ACTIVE)
        ).all()
    )
    assigned_units: dict[uuid.UUID, int] = defaultdict(int)
    occupied: dict[uuid.UUID, set[str]] = defaultdict(set)
    assigned_requirement_ids: set[uuid.UUID] = set()
    rows = session.exec(
        select(Assignment, HourRequirement)
        .where(Assignment.assignment_process_id == process_id)
        .where(Assignment.status == AssignmentStatus.ACTIVE)
        .where(Assignment.hour_requirement_id == HourRequirement.id)
    ).all()
    for assignment, requirement in rows:
        assigned_requirement_ids.add(assignment.hour_requirement_id)
        assigned_units[assignment.process_teacher_id] += hours_to_units(
            str(requirement.required_teacher_hours)
        )
        occupied[assignment.process_teacher_id].add(
            str(assignment.teaching_activity_id)
        )
    return FeasibilityState(
        _build_participants(participants, assigned_units, occupied),
        _build_available_slots(session, process_id, assigned_requirement_ids),
    )


def _build_participants(
    rows: list[ProcessTeacher],
    assigned_units: dict[uuid.UUID, int],
    occupied: dict[uuid.UUID, set[str]],
) -> tuple[FeasibilityParticipant, ...]:
    participants = []
    for row in rows:
        target = hours_to_units(str(row.target_weekly_hours))
        participants.append(
            FeasibilityParticipant(
                str(row.id),
                target - assigned_units[row.id],
                frozenset(occupied[row.id]),
            )
        )
    return tuple(sorted(participants, key=lambda item: item.participant_id))


def _build_available_slots(
    session: Session,
    process_id: uuid.UUID,
    assigned_requirement_ids: set[uuid.UUID],
) -> tuple[FeasibilitySlot, ...]:
    rows = session.exec(
        select(HourRequirement)
        .where(HourRequirement.assignment_process_id == process_id)
        .where(col(HourRequirement.retired_generation).is_(None))
        .where(HourRequirement.status == HourRequirementStatus.AVAILABLE)
    ).all()
    slots = (
        FeasibilitySlot(
            str(row.id),
            str(row.teaching_activity_id),
            row.position_index,
            hours_to_units(str(row.required_teacher_hours)),
        )
        for row in rows
        if row.id not in assigned_requirement_ids
    )
    return tuple(sorted(slots, key=_slot_key))


def compute_fast_feasibility_checks(
    state: FeasibilityState,
    *,
    proposed_slot_id: str,
    proposed_participant_id: str,
) -> FastGuardResult:
    """Apply one proposal and run only the deterministic §20.5 cheap guards."""

    participants = _participant_map(state)
    slots = _slot_map(state)
    participant = participants.get(proposed_participant_id)
    slot = slots.get(proposed_slot_id)
    if participant is None:
        raise ValueError(f"unknown proposed participant: {proposed_participant_id}")
    if slot is None:
        raise ValueError(f"unknown proposed slot: {proposed_slot_id}")
    findings = _proposal_findings(participant, slot)
    prospective = _apply_proposal(state, participant, slot)
    findings.extend(_prospective_findings(prospective))
    return FastGuardResult(prospective, tuple(findings))


def _proposal_findings(
    participant: FeasibilityParticipant, slot: FeasibilitySlot
) -> list[FastGuardFinding]:
    findings = []
    if slot.hours_units > participant.remaining_target_units:
        findings.append(
            FastGuardFinding(FastGuardCode.SELECTED_SLOT_DOES_NOT_FIT, (slot.slot_id,))
        )
    if slot.activity_id in participant.occupied_activity_ids:
        findings.append(
            FastGuardFinding(
                FastGuardCode.SELECTED_ACTIVITY_ALREADY_OCCUPIED,
                (slot.activity_id, participant.participant_id),
            )
        )
    return findings


def _apply_proposal(
    state: FeasibilityState,
    participant: FeasibilityParticipant,
    slot: FeasibilitySlot,
) -> FeasibilityState:
    updated = FeasibilityParticipant(
        participant.participant_id,
        max(0, participant.remaining_target_units - slot.hours_units),
        participant.occupied_activity_ids | {slot.activity_id},
    )
    participants = tuple(
        updated if item.participant_id == participant.participant_id else item
        for item in state.participants
    )
    slots = tuple(item for item in state.slots if item.slot_id != slot.slot_id)
    return FeasibilityState(participants, slots)


def _prospective_findings(state: FeasibilityState) -> list[FastGuardFinding]:
    findings = []
    if sum(item.remaining_target_units for item in state.participants) != sum(
        item.hours_units for item in state.slots
    ):
        findings.append(FastGuardFinding(FastGuardCode.RESIDUAL_TOTALS_MISMATCH))
    if state.slots and (
        not state.participants
        or max(item.hours_units for item in state.slots)
        > max(item.remaining_target_units for item in state.participants)
    ):
        largest = max(state.slots, key=lambda item: (item.hours_units, item.slot_id))
        findings.append(
            FastGuardFinding(
                FastGuardCode.SLOT_EXCEEDS_EVERY_TARGET, (largest.slot_id,)
            )
        )
    for activity_id, slots in _slots_by_activity(state.slots).items():
        if not _activity_has_matching(slots, state.participants):
            findings.append(
                FastGuardFinding(
                    FastGuardCode.DISTINCT_TEACHER_SHORTFALL, (activity_id,)
                )
            )
    return findings


def validate_proposed_assignment_against_witness(
    state: FeasibilityState,
    witness: tuple[FeasibilityWitnessEntry, ...],
    *,
    proposed_slot_id: str,
    proposed_participant_id: str,
    limits: WitnessRepairLimits = DEFAULT_WITNESS_REPAIR_LIMITS,
) -> WitnessRepairResult:
    """Validate and boundedly repair a witness around one proposed selection."""

    fast = compute_fast_feasibility_checks(
        state,
        proposed_slot_id=proposed_slot_id,
        proposed_participant_id=proposed_participant_id,
    )
    if not fast.is_safe:
        return WitnessRepairResult(WitnessRepairCode.FAST_GUARD_FAILED, None, 0)
    mapping = _validated_witness_mapping(state, witness)
    if mapping is None:
        return WitnessRepairResult(WitnessRepairCode.INVALID_WITNESS, None, 0)
    mapping.pop(proposed_slot_id)
    repaired, steps, limit_reached = _repair_mapping(
        fast.prospective_state,
        mapping,
        limits,
    )
    if repaired is None:
        code = (
            WitnessRepairCode.REPAIR_LIMIT_REACHED
            if limit_reached
            else WitnessRepairCode.LOCAL_REPAIR_NOT_FOUND
        )
        return WitnessRepairResult(code, None, steps)
    return WitnessRepairResult(
        WitnessRepairCode.REPAIRED,
        _mapping_to_witness(repaired),
        steps,
    )


def _validated_witness_mapping(
    state: FeasibilityState,
    witness: tuple[FeasibilityWitnessEntry, ...],
) -> dict[str, str] | None:
    slots = _slot_map(state)
    participants = _participant_map(state)
    if len(witness) != len(slots):
        return None
    mapping = {entry.slot_id: entry.participant_id for entry in witness}
    if len(mapping) != len(witness) or set(mapping) != set(slots):
        return None
    if any(participant_id not in participants for participant_id in mapping.values()):
        return None
    return mapping if _mapping_is_exact(state, mapping) else None


def _mapping_is_exact(state: FeasibilityState, mapping: dict[str, str]) -> bool:
    assigned: dict[str, int] = defaultdict(int)
    activities: dict[str, set[str]] = {
        item.participant_id: set(item.occupied_activity_ids)
        for item in state.participants
    }
    for slot in state.slots:
        participant_id = mapping[slot.slot_id]
        if slot.activity_id in activities[participant_id]:
            return False
        activities[participant_id].add(slot.activity_id)
        assigned[participant_id] += slot.hours_units
    return all(
        assigned[item.participant_id] == item.remaining_target_units
        for item in state.participants
    )


def _repair_mapping(
    state: FeasibilityState,
    mapping: dict[str, str],
    limits: WitnessRepairLimits,
) -> tuple[dict[str, str] | None, int, bool]:
    targets = {
        item.participant_id: item.remaining_target_units for item in state.participants
    }
    slots = _slot_map(state)
    steps = [0]
    repaired = _repair_search(state, slots, targets, mapping, limits, steps, 0)
    return repaired, steps[0], repaired is None and steps[0] >= limits.max_steps


def _repair_search(
    state: FeasibilityState,
    slots: dict[str, FeasibilitySlot],
    targets: dict[str, int],
    mapping: dict[str, str],
    limits: WitnessRepairLimits,
    steps: list[int],
    moves: int,
) -> dict[str, str] | None:
    deltas = _assignment_deltas(slots, targets, mapping)
    if all(value == 0 for value in deltas.values()):
        return mapping if _mapping_is_exact(state, mapping) else None
    if steps[0] >= limits.max_steps or moves >= limits.max_moves:
        return None
    donor = min((key for key, value in deltas.items() if value > 0), default=None)
    assert donor is not None
    remaining_steps = max(0, limits.max_steps - steps[0])
    moves_to_try = _repair_moves(state, slots, mapping, deltas, donor)[:remaining_steps]
    for slot, recipient in moves_to_try:
        steps[0] += 1
        candidate = dict(mapping)
        candidate[slot.slot_id] = recipient
        repaired = _repair_search(
            state, slots, targets, candidate, limits, steps, moves + 1
        )
        if repaired is not None:
            return repaired
    return None


def _assignment_deltas(
    slots: dict[str, FeasibilitySlot],
    targets: dict[str, int],
    mapping: dict[str, str],
) -> dict[str, int]:
    assigned = dict.fromkeys(targets, 0)
    for slot_id, participant_id in mapping.items():
        assigned[participant_id] += slots[slot_id].hours_units
    return {key: assigned[key] - target for key, target in targets.items()}


def _repair_moves(
    state: FeasibilityState,
    slots: dict[str, FeasibilitySlot],
    mapping: dict[str, str],
    deltas: dict[str, int],
    donor: str,
) -> tuple[tuple[FeasibilitySlot, str], ...]:
    moves = []
    donor_slots = sorted(
        (slots[key] for key, owner in mapping.items() if owner == donor),
        key=_slot_key,
    )
    for slot in donor_slots:
        if slot.hours_units > deltas[donor]:
            continue
        for recipient in sorted(key for key, value in deltas.items() if value < 0):
            if _recipient_can_take(state, slots, mapping, recipient, slot):
                moves.append((slot, recipient))
    return tuple(moves)


def _recipient_can_take(
    state: FeasibilityState,
    slots: dict[str, FeasibilitySlot],
    mapping: dict[str, str],
    recipient: str,
    slot: FeasibilitySlot,
) -> bool:
    participant = _participant_map(state)[recipient]
    activities = set(participant.occupied_activity_ids)
    activities.update(
        slots[key].activity_id
        for key, owner in mapping.items()
        if owner == recipient and key != slot.slot_id
    )
    return slot.activity_id not in activities


def _slots_by_activity(
    slots: tuple[FeasibilitySlot, ...],
) -> dict[str, tuple[FeasibilitySlot, ...]]:
    grouped: dict[str, list[FeasibilitySlot]] = defaultdict(list)
    for slot in slots:
        grouped[slot.activity_id].append(slot)
    return {
        key: tuple(sorted(value, key=_slot_key))
        for key, value in sorted(grouped.items())
    }


def _activity_has_matching(
    slots: tuple[FeasibilitySlot, ...],
    participants: tuple[FeasibilityParticipant, ...],
) -> bool:
    matches: dict[str, int] = {}
    for index, slot in enumerate(slots):
        if not _augment_match(index, slot, slots, participants, matches, set()):
            return False
    return True


def _augment_match(
    slot_index: int,
    slot: FeasibilitySlot,
    slots: tuple[FeasibilitySlot, ...],
    participants: tuple[FeasibilityParticipant, ...],
    matches: dict[str, int],
    visited: set[str],
) -> bool:
    for participant in participants:
        participant_id = participant.participant_id
        if participant_id in visited or not _is_candidate(participant, slot):
            continue
        visited.add(participant_id)
        previous = matches.get(participant_id)
        if previous is None or _augment_match(
            previous, slots[previous], slots, participants, matches, visited
        ):
            matches[participant_id] = slot_index
            return True
    return False


def _is_candidate(participant: FeasibilityParticipant, slot: FeasibilitySlot) -> bool:
    return (
        participant.remaining_target_units >= slot.hours_units
        and slot.activity_id not in participant.occupied_activity_ids
    )


def _participant_map(
    state: FeasibilityState,
) -> dict[str, FeasibilityParticipant]:
    result = {item.participant_id: item for item in state.participants}
    if len(result) != len(state.participants):
        raise ValueError("duplicate participant_id")
    return result


def _slot_map(state: FeasibilityState) -> dict[str, FeasibilitySlot]:
    result = {item.slot_id: item for item in state.slots}
    if len(result) != len(state.slots):
        raise ValueError("duplicate slot_id")
    return result


def _mapping_to_witness(
    mapping: dict[str, str],
) -> tuple[FeasibilityWitnessEntry, ...]:
    return tuple(
        FeasibilityWitnessEntry(slot_id, participant_id)
        for slot_id, participant_id in sorted(mapping.items())
    )


def _slot_key(slot: FeasibilitySlot) -> tuple[int, str, int, str]:
    return (-slot.hours_units, slot.activity_id, slot.position_index, slot.slot_id)


__all__ = [
    "DEFAULT_WITNESS_REPAIR_LIMITS",
    "FastGuardCode",
    "FastGuardFinding",
    "FastGuardResult",
    "WitnessRepairCode",
    "WitnessRepairLimits",
    "WitnessRepairResult",
    "build_remaining_assignment_state",
    "compute_fast_feasibility_checks",
    "validate_proposed_assignment_against_witness",
]
