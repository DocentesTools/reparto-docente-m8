"""Bounded deterministic assignment-feasibility solver (plan §20.3).

The solver is deliberately pure and transport-agnostic.  Callers build a
remaining-state snapshot in integer hundredths, then receive one of
``FEASIBLE``, ``INFEASIBLE`` or ``UNKNOWN``.  It never reads or writes the
database and therefore cannot accidentally run while assignment row locks are
held.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum

from reparto_service.enums import FeasibilityStatus

SOLVER_VERSION = "bounded-dfs-v1"
HUNDREDTH = Decimal("0.01")


class FeasibilityDiagnosticCode(str, Enum):
    """Stable internal diagnostic vocabulary for feasibility evaluations."""

    INCOMPATIBLE_RESIDUAL_TOTALS = "incompatible_residual_totals"
    SLOT_EXCEEDS_EVERY_TARGET = "slot_exceeds_every_target"
    DISTINCT_TEACHER_SHORTFALL = "distinct_teacher_shortfall"
    UNSATISFIABLE_TARGETS = "unsatisfiable_targets"
    INSTANCE_SIZE_LIMIT = "instance_size_limit"
    STEP_LIMIT = "step_limit"
    TIME_LIMIT = "time_limit"


@dataclass(frozen=True, slots=True)
class FeasibilityParticipant:
    """One active participant and their exact remaining target, in hundredths."""

    participant_id: str
    remaining_target_units: int
    occupied_activity_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Reject malformed domain snapshots before search begins."""
        if not self.participant_id:
            raise ValueError("participant_id must not be empty")
        if self.remaining_target_units < 0:
            raise ValueError("remaining_target_units must be non-negative")


@dataclass(frozen=True, slots=True)
class FeasibilitySlot:
    """One indivisible requirement slot, expressed in integer hundredths."""

    slot_id: str
    activity_id: str
    position_index: int
    hours_units: int

    def __post_init__(self) -> None:
        """Reject malformed logical slot identities and hour values."""
        if not self.slot_id or not self.activity_id:
            raise ValueError("slot_id and activity_id must not be empty")
        if self.position_index < 0:
            raise ValueError("position_index must be non-negative")
        if self.hours_units <= 0:
            raise ValueError("hours_units must be positive")


@dataclass(frozen=True, slots=True)
class FeasibilityState:
    """Complete remaining assignment state consumed by the pure solver."""

    participants: tuple[FeasibilityParticipant, ...]
    slots: tuple[FeasibilitySlot, ...]


@dataclass(frozen=True, slots=True)
class SolverLimits:
    """Configurable hard bounds for one feasibility evaluation."""

    max_participants: int = 30
    max_slots: int = 100
    max_steps: int = 1_000_000
    max_seconds: float | None = 2.0

    def __post_init__(self) -> None:
        """Ensure every configured bound is meaningful."""
        if self.max_participants < 0 or self.max_slots < 0:
            raise ValueError("instance-size limits must be non-negative")
        if self.max_steps < 0:
            raise ValueError("max_steps must be non-negative")
        if self.max_seconds is not None and self.max_seconds < 0:
            raise ValueError("max_seconds must be non-negative or None")


DEFAULT_SOLVER_LIMITS = SolverLimits()


@dataclass(frozen=True, slots=True)
class FeasibilityWitnessEntry:
    """One deterministic slot-to-participant witness assignment."""

    slot_id: str
    participant_id: str


@dataclass(frozen=True, slots=True)
class FeasibilityDiagnostic:
    """Internal, administration-only explanation of a solver outcome."""

    code: FeasibilityDiagnosticCode
    message: str
    related_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FeasibilityResult:
    """Bounded solver result, deterministic witness and search telemetry."""

    status: FeasibilityStatus
    witness: tuple[FeasibilityWitnessEntry, ...] | None
    diagnostics: tuple[FeasibilityDiagnostic, ...]
    states_explored: int
    memoization_hits: int
    solver_version: str = SOLVER_VERSION


class _BudgetExceeded(RuntimeError):
    """Internal control flow for a hard search-budget stop."""

    def __init__(self, code: FeasibilityDiagnosticCode) -> None:
        super().__init__(code.value)
        self.code = code


def hours_to_units(value: Decimal | str | int) -> int:
    """Convert an exact hour value to integer hundredths.

    Binary floats are intentionally rejected.  Values with more than two
    decimal places are invalid rather than silently rounded.
    """

    if isinstance(value, float):
        raise TypeError("binary float hours are not accepted")
    try:
        decimal_value = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("hours must be a finite decimal value") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError("hours must be finite and non-negative")
    if decimal_value.quantize(HUNDREDTH) != decimal_value:
        raise ValueError("hours must have at most two decimal places")
    return int(decimal_value * 100)


def evaluate_assignment_feasibility(
    state: FeasibilityState,
    *,
    limits: SolverLimits = DEFAULT_SOLVER_LIMITS,
) -> FeasibilityResult:
    """Evaluate exact assignment feasibility within explicit hard bounds."""

    participants, slots = _normalise_and_validate(state)
    size_diagnostic = _size_limit_diagnostic(participants, slots, limits)
    if size_diagnostic is not None:
        return _result(FeasibilityStatus.UNKNOWN, None, size_diagnostic)
    root_diagnostic = _root_infeasibility_diagnostic(participants, slots)
    if root_diagnostic is not None:
        return _result(FeasibilityStatus.INFEASIBLE, None, root_diagnostic)
    search = _FeasibilitySearch(participants, slots, limits)
    return search.solve()


def _normalise_and_validate(
    state: FeasibilityState,
) -> tuple[tuple[FeasibilityParticipant, ...], tuple[FeasibilitySlot, ...]]:
    participants = tuple(
        sorted(state.participants, key=lambda item: item.participant_id)
    )
    slots = tuple(
        sorted(
            state.slots,
            key=lambda item: (
                -item.hours_units,
                item.activity_id,
                item.position_index,
                item.slot_id,
            ),
        )
    )
    _require_unique((item.participant_id for item in participants), "participant_id")
    _require_unique((item.slot_id for item in slots), "slot_id")
    _require_unique(
        (f"{item.activity_id}\0{item.position_index}" for item in slots),
        "logical slot identity",
    )
    return participants, slots


def _require_unique(values: Iterable[object], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text in seen:
            raise ValueError(f"duplicate {label}: {text}")
        seen.add(text)


def _size_limit_diagnostic(
    participants: tuple[FeasibilityParticipant, ...],
    slots: tuple[FeasibilitySlot, ...],
    limits: SolverLimits,
) -> FeasibilityDiagnostic | None:
    if len(participants) <= limits.max_participants and len(slots) <= limits.max_slots:
        return None
    return FeasibilityDiagnostic(
        FeasibilityDiagnosticCode.INSTANCE_SIZE_LIMIT,
        "The feasibility instance exceeds the configured size limit.",
    )


def _root_infeasibility_diagnostic(
    participants: tuple[FeasibilityParticipant, ...],
    slots: tuple[FeasibilitySlot, ...],
) -> FeasibilityDiagnostic | None:
    target_total = sum(item.remaining_target_units for item in participants)
    slot_total = sum(item.hours_units for item in slots)
    if target_total != slot_total:
        return FeasibilityDiagnostic(
            FeasibilityDiagnosticCode.INCOMPATIBLE_RESIDUAL_TOTALS,
            "Remaining participant targets and slot hours have different totals.",
        )
    largest_target = max(
        (item.remaining_target_units for item in participants), default=0
    )
    oversized = next(
        (item for item in slots if item.hours_units > largest_target), None
    )
    if oversized is not None:
        return FeasibilityDiagnostic(
            FeasibilityDiagnosticCode.SLOT_EXCEEDS_EVERY_TARGET,
            "A remaining slot exceeds every participant's remaining target.",
            (oversized.slot_id,),
        )
    return _root_distinct_shortfall(participants, slots)


def _root_distinct_shortfall(
    participants: tuple[FeasibilityParticipant, ...],
    slots: tuple[FeasibilitySlot, ...],
) -> FeasibilityDiagnostic | None:
    remaining = [item.remaining_target_units for item in participants]
    occupied = [set(item.occupied_activity_ids) for item in participants]
    for activity_id, activity_slots in _slots_by_activity(slots).items():
        if not _activity_has_matching(
            activity_slots, participants, remaining, occupied
        ):
            return FeasibilityDiagnostic(
                FeasibilityDiagnosticCode.DISTINCT_TEACHER_SHORTFALL,
                "An activity has too few distinct participants for its positions.",
                (activity_id,),
            )
    return None


def _result(
    status: FeasibilityStatus,
    witness: tuple[FeasibilityWitnessEntry, ...] | None,
    diagnostic: FeasibilityDiagnostic | None,
    *,
    states_explored: int = 0,
    memoization_hits: int = 0,
) -> FeasibilityResult:
    diagnostics = () if diagnostic is None else (diagnostic,)
    return FeasibilityResult(
        status=status,
        witness=witness,
        diagnostics=diagnostics,
        states_explored=states_explored,
        memoization_hits=memoization_hits,
    )


class _FeasibilitySearch:
    """Mutable search workspace kept private behind the pure public function."""

    def __init__(
        self,
        participants: tuple[FeasibilityParticipant, ...],
        slots: tuple[FeasibilitySlot, ...],
        limits: SolverLimits,
    ) -> None:
        self.participants = participants
        self.slots = slots
        self.limits = limits
        self.remaining = [item.remaining_target_units for item in participants]
        self.occupied = [set(item.occupied_activity_ids) for item in participants]
        self.assignment = [-1] * len(slots)
        self.memo: set[tuple[int, tuple[int, ...], tuple[tuple[str, ...], ...]]] = set()
        self.states_explored = 0
        self.memoization_hits = 0
        self.started_at = time.monotonic()

    def solve(self) -> FeasibilityResult:
        """Run the bounded DFS and translate its internal outcome."""
        try:
            feasible = self._search(0)
        except _BudgetExceeded as exc:
            return self._unknown_result(exc.code)
        if not feasible:
            diagnostic = FeasibilityDiagnostic(
                FeasibilityDiagnosticCode.UNSATISFIABLE_TARGETS,
                "No exact assignment satisfies every participant target.",
            )
            return self._make_result(FeasibilityStatus.INFEASIBLE, None, diagnostic)
        witness = tuple(
            FeasibilityWitnessEntry(
                slot_id=slot.slot_id,
                participant_id=self.participants[self.assignment[index]].participant_id,
            )
            for index, slot in enumerate(self.slots)
        )
        return self._make_result(FeasibilityStatus.FEASIBLE, witness, None)

    def _search(self, slot_index: int) -> bool:
        self._consume_budget()
        if slot_index == len(self.slots):
            return all(value == 0 for value in self.remaining)
        state_key = self._state_key(slot_index)
        if state_key in self.memo:
            self.memoization_hits += 1
            return False
        if not self._residual_state_possible(slot_index):
            self.memo.add(state_key)
            return False
        slot = self.slots[slot_index]
        seen_signatures: set[tuple[int, tuple[str, ...]]] = set()
        for participant_index in self._candidate_indices(slot):
            signature = (
                self.remaining[participant_index],
                tuple(sorted(self.occupied[participant_index])),
            )
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            self._assign(slot_index, participant_index)
            if self._search(slot_index + 1):
                return True
            self._unassign(slot_index, participant_index)
        self.memo.add(state_key)
        return False

    def _consume_budget(self) -> None:
        if self.states_explored >= self.limits.max_steps:
            raise _BudgetExceeded(FeasibilityDiagnosticCode.STEP_LIMIT)
        if self._time_limit_reached():
            raise _BudgetExceeded(FeasibilityDiagnosticCode.TIME_LIMIT)
        self.states_explored += 1

    def _time_limit_reached(self) -> bool:
        if self.limits.max_seconds is None:
            return False
        return time.monotonic() - self.started_at >= self.limits.max_seconds

    def _state_key(
        self, slot_index: int
    ) -> tuple[int, tuple[int, ...], tuple[tuple[str, ...], ...]]:
        return (
            slot_index,
            tuple(self.remaining),
            tuple(tuple(sorted(values)) for values in self.occupied),
        )

    def _candidate_indices(self, slot: FeasibilitySlot) -> tuple[int, ...]:
        return tuple(
            index
            for index in range(len(self.participants))
            if self.remaining[index] >= slot.hours_units
            and slot.activity_id not in self.occupied[index]
        )

    def _assign(self, slot_index: int, participant_index: int) -> None:
        slot = self.slots[slot_index]
        self.assignment[slot_index] = participant_index
        self.remaining[participant_index] -= slot.hours_units
        self.occupied[participant_index].add(slot.activity_id)

    def _unassign(self, slot_index: int, participant_index: int) -> None:
        slot = self.slots[slot_index]
        self.assignment[slot_index] = -1
        self.remaining[participant_index] += slot.hours_units
        self.occupied[participant_index].remove(slot.activity_id)

    def _residual_state_possible(self, slot_index: int) -> bool:
        remaining_slots = self.slots[slot_index:]
        if remaining_slots and remaining_slots[0].hours_units > max(self.remaining):
            return False
        if not self._targets_reachable(remaining_slots):
            return False
        return self._distinct_pools_sufficient(remaining_slots)

    def _targets_reachable(self, remaining_slots: tuple[FeasibilitySlot, ...]) -> bool:
        for index, target in enumerate(self.remaining):
            if target == 0:
                continue
            eligible_hours = self._eligible_activity_hours(index, remaining_slots)
            if (
                not eligible_hours
                or sum(max(values) for values in eligible_hours.values()) < target
            ):
                return False
            divisor = math.gcd(
                *(value for values in eligible_hours.values() for value in values)
            )
            if target % divisor != 0:
                return False
        return True

    def _eligible_activity_hours(
        self,
        participant_index: int,
        remaining_slots: tuple[FeasibilitySlot, ...],
    ) -> dict[str, list[int]]:
        eligible: dict[str, list[int]] = defaultdict(list)
        for slot in remaining_slots:
            if (
                slot.activity_id not in self.occupied[participant_index]
                and slot.hours_units <= self.remaining[participant_index]
            ):
                eligible[slot.activity_id].append(slot.hours_units)
        return eligible

    def _distinct_pools_sufficient(
        self, remaining_slots: tuple[FeasibilitySlot, ...]
    ) -> bool:
        return all(
            _activity_has_matching(
                activity_slots,
                self.participants,
                self.remaining,
                self.occupied,
            )
            for activity_slots in _slots_by_activity(remaining_slots).values()
        )

    def _unknown_result(self, code: FeasibilityDiagnosticCode) -> FeasibilityResult:
        message = (
            "The deterministic search reached its step budget."
            if code == FeasibilityDiagnosticCode.STEP_LIMIT
            else "The deterministic search reached its time budget."
        )
        return self._make_result(
            FeasibilityStatus.UNKNOWN,
            None,
            FeasibilityDiagnostic(code, message),
        )

    def _make_result(
        self,
        status: FeasibilityStatus,
        witness: tuple[FeasibilityWitnessEntry, ...] | None,
        diagnostic: FeasibilityDiagnostic | None,
    ) -> FeasibilityResult:
        return _result(
            status,
            witness,
            diagnostic,
            states_explored=self.states_explored,
            memoization_hits=self.memoization_hits,
        )


def _slots_by_activity(
    slots: tuple[FeasibilitySlot, ...],
) -> dict[str, tuple[FeasibilitySlot, ...]]:
    grouped: dict[str, list[FeasibilitySlot]] = defaultdict(list)
    for slot in slots:
        grouped[slot.activity_id].append(slot)
    return {activity_id: tuple(items) for activity_id, items in grouped.items()}


def _activity_has_matching(
    activity_slots: tuple[FeasibilitySlot, ...],
    participants: tuple[FeasibilityParticipant, ...],
    remaining: list[int],
    occupied: list[set[str]],
) -> bool:
    matched_participant_to_slot: dict[int, int] = {}
    for activity_slot_index, slot in enumerate(activity_slots):
        if not _augment_activity_match(
            activity_slot_index,
            slot,
            activity_slots,
            participants,
            remaining,
            occupied,
            matched_participant_to_slot,
            set(),
        ):
            return False
    return True


def _augment_activity_match(
    activity_slot_index: int,
    slot: FeasibilitySlot,
    activity_slots: tuple[FeasibilitySlot, ...],
    participants: tuple[FeasibilityParticipant, ...],
    remaining: list[int],
    occupied: list[set[str]],
    matches: dict[int, int],
    visited: set[int],
) -> bool:
    for participant_index in range(len(participants)):
        if not _is_activity_candidate(slot, participant_index, remaining, occupied):
            continue
        if participant_index in visited:
            continue
        visited.add(participant_index)
        previous_slot_index = matches.get(participant_index)
        if previous_slot_index is None or _augment_activity_match(
            previous_slot_index,
            activity_slots[previous_slot_index],
            activity_slots,
            participants,
            remaining,
            occupied,
            matches,
            visited,
        ):
            matches[participant_index] = activity_slot_index
            return True
    return False


def _is_activity_candidate(
    slot: FeasibilitySlot,
    participant_index: int,
    remaining: list[int],
    occupied: list[set[str]],
) -> bool:
    return (
        remaining[participant_index] >= slot.hours_units
        and slot.activity_id not in occupied[participant_index]
    )


__all__ = [
    "DEFAULT_SOLVER_LIMITS",
    "HUNDREDTH",
    "SOLVER_VERSION",
    "FeasibilityDiagnostic",
    "FeasibilityDiagnosticCode",
    "FeasibilityParticipant",
    "FeasibilityResult",
    "FeasibilitySlot",
    "FeasibilityState",
    "FeasibilityWitnessEntry",
    "SolverLimits",
    "evaluate_assignment_feasibility",
    "hours_to_units",
]
