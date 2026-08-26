"""Tests for cheap transactional selection guards and witness repair (§20.5)."""

from __future__ import annotations

from collections.abc import Iterable

import pytest
from sqlmodel import Session

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
from reparto_service.services.selection_guards import (
    FastGuardCode,
    WitnessRepairCode,
    WitnessRepairLimits,
    build_remaining_assignment_state,
    compute_fast_feasibility_checks,
    validate_proposed_assignment_against_witness,
)
from tests import factories


def _participant(
    participant_id: str,
    hours: str,
    *occupied: str,
) -> FeasibilityParticipant:
    return FeasibilityParticipant(
        participant_id,
        hours_to_units(hours),
        frozenset(occupied),
    )


def _slot(
    slot_id: str,
    activity_id: str,
    hours: str,
    position: int = 0,
) -> FeasibilitySlot:
    return FeasibilitySlot(
        slot_id,
        activity_id,
        position,
        hours_to_units(hours),
    )


def _state(
    participants: Iterable[FeasibilityParticipant],
    slots: Iterable[FeasibilitySlot],
) -> FeasibilityState:
    return FeasibilityState(tuple(participants), tuple(slots))


def _witness(*entries: tuple[str, str]) -> tuple[FeasibilityWitnessEntry, ...]:
    return tuple(FeasibilityWitnessEntry(*entry) for entry in entries)


def _codes(
    state: FeasibilityState, slot_id: str = "chosen", participant_id: str = "a"
) -> tuple[FastGuardCode, ...]:
    result = compute_fast_feasibility_checks(
        state,
        proposed_slot_id=slot_id,
        proposed_participant_id=participant_id,
    )
    return tuple(item.code for item in result.findings)


def test_fast_checks_accept_a_safe_proposal_and_build_prospective_state() -> None:
    state = _state(
        (_participant("a", "2"), _participant("b", "2")),
        (_slot("chosen", "x", "2"), _slot("remaining", "x", "2", 1)),
    )
    result = compute_fast_feasibility_checks(
        state,
        proposed_slot_id="chosen",
        proposed_participant_id="a",
    )

    assert result.is_safe
    assert result.findings == ()
    assert {item.slot_id for item in result.prospective_state.slots} == {"remaining"}
    participant = next(
        item
        for item in result.prospective_state.participants
        if item.participant_id == "a"
    )
    assert participant.remaining_target_units == 0
    assert participant.occupied_activity_ids == {"x"}


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (
            _state(
                (_participant("a", "3"),),
                (_slot("chosen", "x", "2"),),
            ),
            FastGuardCode.RESIDUAL_TOTALS_MISMATCH,
        ),
        (
            _state(
                (_participant("a", "1"),),
                (_slot("chosen", "x", "2"),),
            ),
            FastGuardCode.SELECTED_SLOT_DOES_NOT_FIT,
        ),
        (
            _state(
                (_participant("a", "2", "x"),),
                (_slot("chosen", "x", "2"),),
            ),
            FastGuardCode.SELECTED_ACTIVITY_ALREADY_OCCUPIED,
        ),
        (
            _state(
                (_participant("a", "5"), _participant("b", "5")),
                (
                    _slot("chosen", "chosen-activity", "4"),
                    _slot("large", "large-activity", "6"),
                ),
            ),
            FastGuardCode.SLOT_EXCEEDS_EVERY_TARGET,
        ),
        (
            _state(
                (_participant("a", "4"), _participant("b", "4")),
                (
                    _slot("chosen", "other", "2"),
                    _slot("shared-0", "shared", "3"),
                    _slot("shared-1", "shared", "3", 1),
                ),
            ),
            FastGuardCode.DISTINCT_TEACHER_SHORTFALL,
        ),
    ],
)
def test_fast_checks_report_each_guard(
    state: FeasibilityState, expected: FastGuardCode
) -> None:
    assert expected in _codes(state)


def test_oversized_guard_handles_no_remaining_participants() -> None:
    state = _state(
        (_participant("a", "1"),),
        (_slot("chosen", "x", "1"), _slot("remaining", "y", "1")),
    )
    result = compute_fast_feasibility_checks(
        state,
        proposed_slot_id="chosen",
        proposed_participant_id="a",
    )
    # The participant remains in the prospective state with a zero target; this
    # exercises the same "no capacity exists" branch as an empty pool.
    assert FastGuardCode.SLOT_EXCEEDS_EVERY_TARGET in {
        item.code for item in result.findings
    }


@pytest.mark.parametrize(
    ("participant_id", "slot_id", "message"),
    [
        ("missing", "chosen", "unknown proposed participant"),
        ("a", "missing", "unknown proposed slot"),
    ],
)
def test_fast_checks_reject_unknown_proposal_identity(
    participant_id: str, slot_id: str, message: str
) -> None:
    state = _state((_participant("a", "1"),), (_slot("chosen", "x", "1"),))
    with pytest.raises(ValueError, match=message):
        compute_fast_feasibility_checks(
            state,
            proposed_slot_id=slot_id,
            proposed_participant_id=participant_id,
        )


@pytest.mark.parametrize(
    "state",
    [
        _state(
            (_participant("a", "1"), _participant("a", "1")),
            (_slot("chosen", "x", "1"),),
        ),
        _state(
            (_participant("a", "1"),),
            (_slot("chosen", "x", "1"), _slot("chosen", "y", "1")),
        ),
    ],
)
def test_fast_checks_reject_duplicate_identities(state: FeasibilityState) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        compute_fast_feasibility_checks(
            state,
            proposed_slot_id="chosen",
            proposed_participant_id="a",
        )


def test_witness_repair_removes_an_already_matching_selection() -> None:
    state = _state(
        (_participant("a", "2"), _participant("b", "2")),
        (_slot("chosen", "x", "2"), _slot("remaining", "x", "2", 1)),
    )
    result = validate_proposed_assignment_against_witness(
        state,
        _witness(("chosen", "a"), ("remaining", "b")),
        proposed_slot_id="chosen",
        proposed_participant_id="a",
    )

    assert result.repaired
    assert result.code == WitnessRepairCode.REPAIRED
    assert result.witness == _witness(("remaining", "b"))
    assert result.steps == 0


def test_witness_repair_moves_only_the_needed_deterministic_slot() -> None:
    state = _state(
        (_participant("a", "4"), _participant("b", "4")),
        (
            _slot("chosen", "x", "2"),
            _slot("a-first", "a1", "2"),
            _slot("a-second", "a2", "2"),
            _slot("b-slot", "b1", "2"),
        ),
    )
    result = validate_proposed_assignment_against_witness(
        state,
        _witness(
            ("chosen", "b"),
            ("a-first", "a"),
            ("a-second", "a"),
            ("b-slot", "b"),
        ),
        proposed_slot_id="chosen",
        proposed_participant_id="a",
    )

    assert result.repaired
    assert result.steps == 1
    assert result.witness == _witness(
        ("a-first", "b"),
        ("a-second", "a"),
        ("b-slot", "b"),
    )


def test_witness_repair_skips_a_recipient_activity_collision() -> None:
    state = _state(
        (_participant("a", "4"), _participant("b", "2", "blocked")),
        (
            _slot("chosen", "x", "2"),
            _slot("blocked-slot", "blocked", "2"),
            _slot("allowed-slot", "allowed", "2"),
        ),
    )
    result = validate_proposed_assignment_against_witness(
        state,
        _witness(
            ("chosen", "b"),
            ("blocked-slot", "a"),
            ("allowed-slot", "a"),
        ),
        proposed_slot_id="chosen",
        proposed_participant_id="a",
    )
    assert result.repaired
    assert result.witness == _witness(
        ("allowed-slot", "b"),
        ("blocked-slot", "a"),
    )


@pytest.mark.parametrize(
    "witness",
    [
        _witness(),
        _witness(("chosen", "a"), ("chosen", "b")),
        _witness(("chosen", "missing"), ("remaining", "b")),
        _witness(("chosen", "a"), ("remaining", "a")),
        _witness(("chosen", "a"), ("remaining", "b"), ("extra", "b")),
    ],
)
def test_witness_validation_rejects_incomplete_or_inexact_mapping(
    witness: tuple[FeasibilityWitnessEntry, ...],
) -> None:
    state = _state(
        (_participant("a", "2"), _participant("b", "2")),
        (_slot("chosen", "x", "2"), _slot("remaining", "x", "2", 1)),
    )
    result = validate_proposed_assignment_against_witness(
        state,
        witness,
        proposed_slot_id="chosen",
        proposed_participant_id="a",
    )
    assert result.code == WitnessRepairCode.INVALID_WITNESS
    assert result.witness is None
    assert not result.repaired


def test_witness_validation_rejects_a_fast_guard_failure_first() -> None:
    state = _state(
        (_participant("a", "1"),),
        (_slot("chosen", "x", "2"),),
    )
    result = validate_proposed_assignment_against_witness(
        state,
        _witness(("chosen", "a")),
        proposed_slot_id="chosen",
        proposed_participant_id="a",
    )
    assert result.code == WitnessRepairCode.FAST_GUARD_FAILED


def test_witness_repair_stops_at_the_explicit_step_limit() -> None:
    state = _state(
        (_participant("a", "4"), _participant("b", "4")),
        (
            _slot("chosen", "x", "2"),
            _slot("a-first", "a1", "2"),
            _slot("a-second", "a2", "2"),
            _slot("b-slot", "b1", "2"),
        ),
    )
    result = validate_proposed_assignment_against_witness(
        state,
        _witness(
            ("chosen", "b"),
            ("a-first", "a"),
            ("a-second", "a"),
            ("b-slot", "b"),
        ),
        proposed_slot_id="chosen",
        proposed_participant_id="a",
        limits=WitnessRepairLimits(max_steps=0),
    )
    assert result.code == WitnessRepairCode.REPAIR_LIMIT_REACHED
    assert result.steps == 0


def test_witness_repair_stops_after_a_bounded_failed_move() -> None:
    state = _state(
        (_participant("a", "4"), _participant("b", "4")),
        (
            _slot("chosen", "x", "4"),
            _slot("a-first", "a1", "2"),
            _slot("a-second", "a2", "2"),
        ),
    )
    result = validate_proposed_assignment_against_witness(
        state,
        _witness(
            ("chosen", "b"),
            ("a-first", "a"),
            ("a-second", "a"),
        ),
        proposed_slot_id="chosen",
        proposed_participant_id="a",
        limits=WitnessRepairLimits(max_steps=1, max_moves=1),
    )
    assert result.code == WitnessRepairCode.REPAIR_LIMIT_REACHED
    assert result.steps == 1


def test_witness_repair_reports_when_no_balance_improving_move_exists() -> None:
    state = _state(
        (_participant("a", "4"), _participant("b", "3")),
        (
            _slot("chosen", "x", "1"),
            _slot("a-first", "a1", "2"),
            _slot("a-second", "a2", "2"),
            _slot("b-slot", "b1", "2"),
        ),
    )
    result = validate_proposed_assignment_against_witness(
        state,
        _witness(
            ("chosen", "b"),
            ("a-first", "a"),
            ("a-second", "a"),
            ("b-slot", "b"),
        ),
        proposed_slot_id="chosen",
        proposed_participant_id="a",
    )
    assert result.code == WitnessRepairCode.LOCAL_REPAIR_NOT_FOUND
    assert result.steps == 0


def test_witness_repair_limits_reject_negative_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        WitnessRepairLimits(max_moves=-1)


def test_remaining_state_builder_uses_live_active_rows_only(session: Session) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(
        session, plan, subject, required_teacher_count=3
    )
    assigned_slot = factories.make_hour_requirement(
        session, process, activity, position_index=0, required_teacher_hours=2
    )
    available_slot = factories.make_hour_requirement(
        session, process, activity, position_index=1, required_teacher_hours=2
    )
    retired_slot = factories.make_hour_requirement(
        session,
        process,
        activity,
        position_index=2,
        required_teacher_hours=2,
        retired_generation=2,
    )
    profile = factories.make_teacher_profile(session)
    active = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4
    )
    inactive_profile = factories.make_teacher_profile(session)
    factories.make_process_teacher(
        session,
        process,
        inactive_profile,
        base_weekly_hours=20,
        status=ProcessTeacherStatus.INACTIVE,
    )
    factories.make_assignment(session, process, assigned_slot, active)
    retired_slot.status = HourRequirementStatus.AVAILABLE
    session.add(retired_slot)
    session.commit()
    cancelled = factories.make_assignment(
        session,
        process,
        available_slot,
        active,
        status=AssignmentStatus.CANCELLED,
    )
    assert cancelled.status == AssignmentStatus.CANCELLED

    state = build_remaining_assignment_state(session, process.id)

    assert len(state.participants) == 1
    assert state.participants[0].remaining_target_units == hours_to_units("2")
    assert state.participants[0].occupied_activity_ids == {str(activity.id)}
    assert [item.slot_id for item in state.slots] == [str(available_slot.id)]
