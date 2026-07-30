"""Tests for the bounded deterministic feasibility solver (plan §20.3)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from reparto_service.enums import FeasibilityStatus
from reparto_service.services.feasibility import (
    SOLVER_VERSION,
    FeasibilityDiagnosticCode,
    FeasibilityParticipant,
    FeasibilitySlot,
    FeasibilityState,
    SolverLimits,
    evaluate_assignment_feasibility,
    hours_to_units,
)


def _participant(
    participant_id: str,
    hours: str,
    *,
    occupied: frozenset[str] = frozenset(),
) -> FeasibilityParticipant:
    return FeasibilityParticipant(
        participant_id,
        hours_to_units(hours),
        occupied,
    )


def _slot(
    slot_id: str,
    activity_id: str,
    position: int,
    hours: str,
) -> FeasibilitySlot:
    return FeasibilitySlot(
        slot_id,
        activity_id,
        position,
        hours_to_units(hours),
    )


def _state(
    participants: tuple[FeasibilityParticipant, ...],
    slots: tuple[FeasibilitySlot, ...],
) -> FeasibilityState:
    return FeasibilityState(participants, slots)


def _diagnostic_code(state: FeasibilityState) -> FeasibilityDiagnosticCode:
    result = evaluate_assignment_feasibility(state)
    assert len(result.diagnostics) == 1
    return result.diagnostics[0].code


def test_hours_to_units_uses_exact_integer_hundredths() -> None:
    assert hours_to_units(Decimal("10.25")) == 1025
    assert hours_to_units("0") == 0
    assert hours_to_units(7) == 700


@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        (1.5, TypeError, "binary float"),
        ("1.001", ValueError, "two decimal"),
        ("-1", ValueError, "finite and non-negative"),
        ("NaN", ValueError, "finite and non-negative"),
        ("not-hours", ValueError, "finite decimal"),
    ],
)
def test_hours_to_units_rejects_inexact_or_invalid_values(
    value: object,
    error: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error, match=message):
        hours_to_units(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("participant_id", "units", "message"),
    [
        ("", 100, "participant_id"),
        ("teacher", -1, "non-negative"),
    ],
)
def test_participant_rejects_invalid_values(
    participant_id: str, units: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        FeasibilityParticipant(participant_id, units)


@pytest.mark.parametrize(
    ("slot_id", "activity_id", "position", "units", "message"),
    [
        ("", "activity", 0, 100, "must not be empty"),
        ("slot", "", 0, 100, "must not be empty"),
        ("slot", "activity", -1, 100, "non-negative"),
        ("slot", "activity", 0, 0, "positive"),
    ],
)
def test_slot_rejects_invalid_values(
    slot_id: str,
    activity_id: str,
    position: int,
    units: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        FeasibilitySlot(slot_id, activity_id, position, units)


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        ({"max_participants": -1}, "instance-size"),
        ({"max_slots": -1}, "instance-size"),
        ({"max_steps": -1}, "max_steps"),
        ({"max_seconds": -0.1}, "max_seconds"),
    ],
)
def test_solver_limits_reject_negative_bounds(
    limits: dict[str, int | float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        SolverLimits(**limits)  # type: ignore[arg-type]


def test_feasible_witness_is_complete_stable_and_largest_slot_first() -> None:
    state = _state(
        (_participant("teacher-b", "5"), _participant("teacher-a", "5")),
        (
            _slot("slot-d", "activity-d", 0, "2"),
            _slot("slot-b", "activity-b", 0, "3"),
            _slot("slot-c", "activity-c", 0, "2"),
            _slot("slot-a", "activity-a", 0, "3"),
        ),
    )

    first = evaluate_assignment_feasibility(state)
    second = evaluate_assignment_feasibility(
        _state(tuple(reversed(state.participants)), tuple(reversed(state.slots))),
        limits=SolverLimits(max_seconds=None),
    )

    assert first == second
    assert first.status == FeasibilityStatus.FEASIBLE
    assert first.solver_version == SOLVER_VERSION
    assert first.diagnostics == ()
    assert first.witness is not None
    assert [(item.slot_id, item.participant_id) for item in first.witness] == [
        ("slot-a", "teacher-a"),
        ("slot-b", "teacher-b"),
        ("slot-c", "teacher-a"),
        ("slot-d", "teacher-b"),
    ]
    assert first.states_explored > 0


def test_empty_exact_state_is_feasible_with_empty_witness() -> None:
    result = evaluate_assignment_feasibility(_state((), ()))
    assert result.status == FeasibilityStatus.FEASIBLE
    assert result.witness == ()


def test_balanced_totals_can_still_be_exact_partition_infeasible() -> None:
    state = _state(
        (_participant("a", "5"), _participant("b", "5")),
        (
            _slot("s1", "a1", 0, "3"),
            _slot("s2", "a2", 0, "3"),
            _slot("s3", "a3", 0, "3"),
            _slot("s4", "a4", 0, "1"),
        ),
    )
    result = evaluate_assignment_feasibility(state)
    assert result.status == FeasibilityStatus.INFEASIBLE
    assert result.witness is None
    assert result.diagnostics[0].code == FeasibilityDiagnosticCode.UNSATISFIABLE_TARGETS


def test_incompatible_residual_totals_are_diagnosed_before_search() -> None:
    state = _state((_participant("a", "3"),), (_slot("s", "x", 0, "2"),))
    result = evaluate_assignment_feasibility(state)
    assert result.status == FeasibilityStatus.INFEASIBLE
    assert result.states_explored == 0
    assert (
        result.diagnostics[0].code
        == FeasibilityDiagnosticCode.INCOMPATIBLE_RESIDUAL_TOTALS
    )


def test_slot_larger_than_every_remaining_target_is_diagnosed() -> None:
    state = _state(
        (_participant("a", "5"), _participant("b", "5")),
        (
            _slot("large", "x", 0, "6"),
            _slot("small", "y", 0, "4"),
        ),
    )
    result = evaluate_assignment_feasibility(state)
    assert (
        result.diagnostics[0].code
        == FeasibilityDiagnosticCode.SLOT_EXCEEDS_EVERY_TARGET
    )
    assert result.diagnostics[0].related_ids == ("large",)


def test_distinct_teacher_constraint_accepts_different_participants() -> None:
    state = _state(
        (_participant("a", "2"), _participant("b", "2")),
        (
            _slot("s1", "shared", 0, "2"),
            _slot("s2", "shared", 1, "2"),
        ),
    )
    result = evaluate_assignment_feasibility(state)
    assert result.status == FeasibilityStatus.FEASIBLE
    assert result.witness is not None
    assert {item.participant_id for item in result.witness} == {"a", "b"}


@pytest.mark.parametrize(
    "participants",
    [
        (_participant("a", "4"), _participant("b", "0")),
        (
            _participant("a", "2", occupied=frozenset({"shared"})),
            _participant("b", "2"),
        ),
    ],
)
def test_distinct_teacher_shortfall_is_diagnosed(
    participants: tuple[FeasibilityParticipant, ...],
) -> None:
    state = _state(
        participants,
        (
            _slot("s1", "shared", 0, "2"),
            _slot("s2", "shared", 1, "2"),
        ),
    )
    assert (
        _diagnostic_code(state) == FeasibilityDiagnosticCode.DISTINCT_TEACHER_SHORTFALL
    )


def test_existing_activity_occupancy_is_respected_by_search() -> None:
    state = _state(
        (
            _participant("a", "2", occupied=frozenset({"shared"})),
            _participant("b", "2"),
        ),
        (
            _slot("s1", "shared", 0, "2"),
            _slot("s2", "other", 0, "2"),
        ),
    )
    result = evaluate_assignment_feasibility(state)
    assert result.status == FeasibilityStatus.FEASIBLE
    assert result.witness is not None
    assert {(item.slot_id, item.participant_id) for item in result.witness} == {
        ("s1", "b"),
        ("s2", "a"),
    }


def test_residual_target_gcd_pruning_rejects_unreachable_target() -> None:
    state = _state(
        (_participant("a", "5"), _participant("b", "5")),
        (
            _slot("s1", "x", 0, "4"),
            _slot("s2", "y", 0, "4"),
            _slot("s3", "z", 0, "2"),
        ),
    )
    result = evaluate_assignment_feasibility(state)
    assert result.status == FeasibilityStatus.INFEASIBLE
    assert result.states_explored == 1


def test_residual_oversized_slot_prunes_a_fragmented_branch() -> None:
    state = _state(
        (
            _participant("a", "6"),
            _participant("b", "2"),
            _participant("c", "2"),
        ),
        (
            _slot("s1", "x", 0, "4"),
            _slot("s2", "y", 0, "3"),
            _slot("s3", "z1", 0, "1"),
            _slot("s4", "z2", 0, "1"),
            _slot("s5", "z3", 0, "1"),
        ),
    )
    result = evaluate_assignment_feasibility(state)
    assert result.status == FeasibilityStatus.INFEASIBLE
    assert result.states_explored == 2


def test_equivalent_slot_permutations_reuse_a_memoized_state() -> None:
    state = _state(
        (
            _participant("a", "6", occupied=frozenset({"old-a"})),
            _participant("b", "6", occupied=frozenset({"old-b"})),
            _participant("c", "6", occupied=frozenset({"old-c"})),
        ),
        (
            _slot("shared-1", "a-shared", 0, "4"),
            _slot("shared-2", "a-shared", 1, "4"),
            _slot("four", "z-other-4", 0, "4"),
            _slot("three", "z-other-3", 0, "3"),
            _slot("two", "z-other-2", 0, "2"),
            _slot("one", "z-other-1", 0, "1"),
        ),
    )
    result = evaluate_assignment_feasibility(
        state,
        limits=SolverLimits(max_seconds=None),
    )
    assert result.status == FeasibilityStatus.INFEASIBLE
    assert result.memoization_hits > 0


@pytest.mark.parametrize(
    "limits",
    [
        SolverLimits(max_participants=0),
        SolverLimits(max_slots=0),
    ],
)
def test_instance_size_limit_returns_unknown(limits: SolverLimits) -> None:
    state = _state((_participant("a", "1"),), (_slot("s", "x", 0, "1"),))
    result = evaluate_assignment_feasibility(state, limits=limits)
    assert result.status == FeasibilityStatus.UNKNOWN
    assert result.witness is None
    assert result.diagnostics[0].code == FeasibilityDiagnosticCode.INSTANCE_SIZE_LIMIT
    assert result.states_explored == 0


def test_step_budget_returns_unknown_fail_closed() -> None:
    state = _state((_participant("a", "1"),), (_slot("s", "x", 0, "1"),))
    result = evaluate_assignment_feasibility(
        state,
        limits=SolverLimits(max_steps=0, max_seconds=None),
    )
    assert result.status == FeasibilityStatus.UNKNOWN
    assert result.diagnostics[0].code == FeasibilityDiagnosticCode.STEP_LIMIT


def test_time_budget_returns_unknown_fail_closed() -> None:
    state = _state((_participant("a", "1"),), (_slot("s", "x", 0, "1"),))
    result = evaluate_assignment_feasibility(
        state,
        limits=SolverLimits(max_seconds=0),
    )
    assert result.status == FeasibilityStatus.UNKNOWN
    assert result.diagnostics[0].code == FeasibilityDiagnosticCode.TIME_LIMIT


@pytest.mark.parametrize(
    "state",
    [
        _state(
            (_participant("same", "1"), _participant("same", "1")),
            (_slot("s1", "x", 0, "1"), _slot("s2", "y", 0, "1")),
        ),
        _state(
            (_participant("a", "2"),),
            (_slot("same", "x", 0, "1"), _slot("same", "y", 0, "1")),
        ),
        _state(
            (_participant("a", "2"),),
            (_slot("s1", "x", 0, "1"), _slot("s2", "x", 0, "1")),
        ),
    ],
)
def test_duplicate_solver_identities_are_rejected(state: FeasibilityState) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_assignment_feasibility(state)
