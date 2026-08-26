"""Unit tests for ``reparto_service.services.planning_lifecycle``.

These pin down the three planning state machines (teaching plan, hour-requirement
slot and the orthogonal feasibility axis) and exercise the generic
:class:`TransitionTable` helper, including its terminal-state behaviour.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import pytest

from reparto_service.enums import (
    FeasibilityStatus,
    HourRequirementStatus,
    TeachingPlanStatus,
)
from reparto_service.services.planning_lifecycle import (
    FEASIBILITY_LIFECYCLE,
    HOUR_REQUIREMENT_LIFECYCLE,
    TEACHING_PLAN_LIFECYCLE,
    IllegalStateTransitionError,
    TransitionTable,
)

TP = TeachingPlanStatus
HR = HourRequirementStatus
FS = FeasibilityStatus


# ── Exact contract of each table (pins the plan §5.2/§5.9/§20 edges) ──────────


EXPECTED_TEACHING_PLAN: dict[TeachingPlanStatus, set[TeachingPlanStatus]] = {
    TP.DRAFT: {TP.UNBALANCED, TP.BALANCED},
    TP.UNBALANCED: {TP.BALANCED},
    TP.BALANCED: {TP.UNBALANCED, TP.LOCKED},
    TP.LOCKED: {TP.REQUIREMENTS_GENERATED, TP.BALANCED, TP.STALE},
    TP.REQUIREMENTS_GENERATED: {TP.STALE, TP.RECONCILIATION_REQUIRED},
    TP.STALE: {TP.UNBALANCED, TP.BALANCED, TP.REQUIREMENTS_GENERATED},
    TP.RECONCILIATION_REQUIRED: {TP.REQUIREMENTS_GENERATED},
}

EXPECTED_HOUR_REQUIREMENT: dict[HourRequirementStatus, set[HourRequirementStatus]] = {
    HR.AVAILABLE: {HR.ASSIGNED, HR.STALE},
    HR.ASSIGNED: {HR.AVAILABLE, HR.RECONCILIATION_REQUIRED},
    HR.STALE: {HR.AVAILABLE},
    HR.RECONCILIATION_REQUIRED: {HR.ASSIGNED, HR.STALE},
}

EXPECTED_FEASIBILITY: dict[FeasibilityStatus, set[FeasibilityStatus]] = {
    FS.NOT_EVALUATED: {FS.FEASIBLE, FS.INFEASIBLE, FS.UNKNOWN},
    FS.FEASIBLE: {FS.NOT_EVALUATED},
    FS.INFEASIBLE: {FS.NOT_EVALUATED},
    FS.UNKNOWN: {FS.NOT_EVALUATED},
}

TABLES: list[tuple[TransitionTable[Any], dict[Any, set[Any]], type[Enum]]] = [
    (TEACHING_PLAN_LIFECYCLE, EXPECTED_TEACHING_PLAN, TeachingPlanStatus),
    (HOUR_REQUIREMENT_LIFECYCLE, EXPECTED_HOUR_REQUIREMENT, HourRequirementStatus),
    (FEASIBILITY_LIFECYCLE, EXPECTED_FEASIBILITY, FeasibilityStatus),
]


def test_teaching_plan_table_matches_contract() -> None:
    actual = {k: set(v) for k, v in TEACHING_PLAN_LIFECYCLE.table.items()}
    assert actual == EXPECTED_TEACHING_PLAN


def test_hour_requirement_table_matches_contract() -> None:
    actual = {k: set(v) for k, v in HOUR_REQUIREMENT_LIFECYCLE.table.items()}
    assert actual == EXPECTED_HOUR_REQUIREMENT


def test_feasibility_table_matches_contract() -> None:
    actual = {k: set(v) for k, v in FEASIBILITY_LIFECYCLE.table.items()}
    assert actual == EXPECTED_FEASIBILITY


# ── Generic invariants across every real table ───────────────────────────────


@pytest.mark.parametrize(("table", "expected", "enum"), TABLES)
def test_every_enum_member_is_a_table_key(
    table: TransitionTable[Enum],
    expected: dict[Enum, set[Enum]],
    enum: type[Enum],
) -> None:
    """No member may be silently unhandled by its own lifecycle."""
    assert set(table.table) == set(enum)


@pytest.mark.parametrize(("table", "expected", "enum"), TABLES)
def test_no_self_loops(
    table: TransitionTable[Enum],
    expected: dict[Enum, set[Enum]],
    enum: type[Enum],
) -> None:
    for source, targets in table.table.items():
        assert source not in targets, f"self-loop on {source.value}"


@pytest.mark.parametrize(("table", "expected", "enum"), TABLES)
def test_targets_are_valid_members(
    table: TransitionTable[Enum],
    expected: dict[Enum, set[Enum]],
    enum: type[Enum],
) -> None:
    for source, targets in table.table.items():
        for target in targets:
            assert target in enum, f"{source.value} -> {target!r} invalid"


@pytest.mark.parametrize(("table", "expected", "enum"), TABLES)
def test_happy_paths_allowed(
    table: TransitionTable[Enum],
    expected: dict[Enum, set[Enum]],
    enum: type[Enum],
) -> None:
    for source, targets in expected.items():
        for target in targets:
            assert table.is_allowed(source, target)
            table.assert_allowed(source, target)  # must not raise


@pytest.mark.parametrize(("table", "expected", "enum"), TABLES)
def test_forbidden_edges_rejected(
    table: TransitionTable[Enum],
    expected: dict[Enum, set[Enum]],
    enum: type[Enum],
) -> None:
    for source in enum:
        for target in enum:
            if target in expected[source] or target == source:
                continue
            assert not table.is_allowed(source, target)
            with pytest.raises(IllegalStateTransitionError):
                table.assert_allowed(source, target)


@pytest.mark.parametrize(("table", "expected", "enum"), TABLES)
def test_self_edge_never_allowed(
    table: TransitionTable[Enum],
    expected: dict[Enum, set[Enum]],
    enum: type[Enum],
) -> None:
    for status in enum:
        assert not table.is_allowed(status, status)


@pytest.mark.parametrize(("table", "expected", "enum"), TABLES)
def test_no_real_state_is_terminal(
    table: TransitionTable[Enum],
    expected: dict[Enum, set[Enum]],
    enum: type[Enum],
) -> None:
    """Every planning state can still progress somewhere."""
    for status in enum:
        assert not table.is_terminal(status)
        assert table.targets(status)


# ── Error object detail ──────────────────────────────────────────────────────


def test_error_message_lists_allowed_targets() -> None:
    with pytest.raises(IllegalStateTransitionError) as exc:
        TEACHING_PLAN_LIFECYCLE.assert_allowed(TP.DRAFT, TP.LOCKED)
    err = exc.value
    assert err.machine == "TeachingPlanStatus"
    assert err.current is TP.DRAFT
    assert err.target is TP.LOCKED
    assert err.allowed == frozenset({TP.UNBALANCED, TP.BALANCED})
    assert "TeachingPlanStatus" in str(err)
    assert "none (terminal)" not in str(err)


# ── Generic TransitionTable helper: terminal-state and default branches ──────


TERMINAL_TABLE: dict[FeasibilityStatus, frozenset[FeasibilityStatus]] = {
    FS.FEASIBLE: frozenset(),
}


def test_terminal_state_and_missing_key_behaviour() -> None:
    toy: TransitionTable[FeasibilityStatus] = TransitionTable("Toy", TERMINAL_TABLE)
    # State present with no outgoing edges is terminal.
    assert toy.is_terminal(FS.FEASIBLE)
    assert toy.targets(FS.FEASIBLE) == frozenset()
    # A state absent from the table defaults to empty targets (also terminal).
    assert toy.is_terminal(FS.UNKNOWN)
    assert toy.targets(FS.UNKNOWN) == frozenset()
    assert not toy.is_allowed(FS.FEASIBLE, FS.NOT_EVALUATED)


def test_terminal_state_error_message_uses_none_terminal() -> None:
    toy: TransitionTable[FeasibilityStatus] = TransitionTable("Toy", TERMINAL_TABLE)
    with pytest.raises(IllegalStateTransitionError) as exc:
        toy.assert_allowed(FS.FEASIBLE, FS.NOT_EVALUATED)
    assert "none (terminal)" in str(exc.value)
    assert exc.value.allowed == frozenset()
