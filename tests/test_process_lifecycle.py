"""Unit tests for ``reparto_service.services.process_lifecycle``.

The state machine is the only authority on which ``AssignmentProcess``
status transitions are legal. These tests pin the table down and check
the helper predicates the controller relies on.
"""

from __future__ import annotations

import pytest

from reparto_service.enums import AssignmentProcessStatus as S
from reparto_service.services.process_lifecycle import (
    ALLOWED_TRANSITIONS,
    IllegalTransitionError,
    assert_allowed_transition,
    is_allowed_transition,
    is_closing_transition,
    is_reopen_edge,
    is_reopen_to_close_edge,
    is_terminal,
)

ALL_STATUSES: list[S] = list(S)


# ── Table sanity ──────────────────────────────────────────────────────────────


def test_archived_is_terminal() -> None:
    assert is_terminal(S.ARCHIVED)
    assert ALLOWED_TRANSITIONS[S.ARCHIVED] == frozenset()


def test_every_non_terminal_status_has_at_least_one_edge() -> None:
    for status in ALL_STATUSES:
        if status == S.ARCHIVED:
            continue
        assert ALLOWED_TRANSITIONS[status], (
            f"Status {status.value} is effectively terminal but is not ARCHIVED"
        )


def test_reopen_edge_is_listed_under_final() -> None:
    """``final`` → ``reopened`` must be in the table even though it goes
    through the dedicated reopen endpoint, so audit / inspection agree.
    """
    assert S.REOPENED in ALLOWED_TRANSITIONS[S.FINAL]


def test_no_status_self_loop() -> None:
    for source, targets in ALLOWED_TRANSITIONS.items():
        assert source not in targets, f"self-loop on {source.value}"


def test_no_status_references_a_nonexistent_target() -> None:
    for source, targets in ALLOWED_TRANSITIONS.items():
        for target in targets:
            assert target in S, (
                f"{source.value} -> {target.value} is not a valid status"
            )


# ── Per-edge happy paths (lock the table down) ───────────────────────────────


HAPPY_PATHS: list[tuple[S, S]] = [
    (S.DRAFT, S.READY_FOR_MEETING),
    (S.DRAFT, S.ARCHIVED),
    (S.READY_FOR_MEETING, S.MEETING_OPEN),
    (S.READY_FOR_MEETING, S.DRAFT),
    (S.READY_FOR_MEETING, S.ARCHIVED),
    (S.MEETING_OPEN, S.ASSIGNING),
    (S.MEETING_OPEN, S.READY_FOR_MEETING),
    (S.MEETING_OPEN, S.ARCHIVED),
    (S.ASSIGNING, S.DEPARTMENT_PROPOSAL),
    (S.ASSIGNING, S.MEETING_OPEN),
    (S.ASSIGNING, S.ARCHIVED),
    (S.DEPARTMENT_PROPOSAL, S.SENT_TO_SCHOOL_LEADERSHIP),
    (S.DEPARTMENT_PROPOSAL, S.ASSIGNING),
    (S.DEPARTMENT_PROPOSAL, S.ARCHIVED),
    (S.SENT_TO_SCHOOL_LEADERSHIP, S.RETURNED_BY_SCHOOL_LEADERSHIP),
    (S.SENT_TO_SCHOOL_LEADERSHIP, S.ARCHIVED),
    (S.RETURNED_BY_SCHOOL_LEADERSHIP, S.INTERNAL_REVISION),
    (S.RETURNED_BY_SCHOOL_LEADERSHIP, S.ARCHIVED),
    (S.INTERNAL_REVISION, S.DEPARTMENT_PROPOSAL),
    (S.INTERNAL_REVISION, S.FINAL),
    (S.INTERNAL_REVISION, S.ARCHIVED),
    (S.FINAL, S.REOPENED),
    (S.FINAL, S.ARCHIVED),
    (S.REOPENED, S.INTERNAL_REVISION),
    (S.REOPENED, S.FINAL),
    (S.REOPENED, S.ARCHIVED),
]


@pytest.mark.parametrize(("current", "target"), HAPPY_PATHS)
def test_happy_path_allowed(current: S, target: S) -> None:
    assert is_allowed_transition(current, target)
    assert_allowed_transition(current, target)  # should not raise


# ── Forbidden edges (a few representative ones) ──────────────────────────────


def test_draft_cannot_jump_to_final() -> None:
    assert not is_allowed_transition(S.DRAFT, S.FINAL)
    with pytest.raises(IllegalTransitionError):
        assert_allowed_transition(S.DRAFT, S.FINAL)


def test_final_cannot_go_back_to_assigning() -> None:
    assert not is_allowed_transition(S.FINAL, S.ASSIGNING)
    with pytest.raises(IllegalTransitionError):
        assert_allowed_transition(S.FINAL, S.ASSIGNING)


def test_archived_cannot_transition_anywhere() -> None:
    for target in ALL_STATUSES:
        assert not is_allowed_transition(S.ARCHIVED, target)
        with pytest.raises(IllegalTransitionError):
            assert_allowed_transition(S.ARCHIVED, target)


def test_self_transition_is_not_allowed() -> None:
    for status in ALL_STATUSES:
        assert not is_allowed_transition(status, status)


# ── Edge predicates ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (S.FINAL, S.REOPENED, True),
        (S.ASSIGNING, S.REOPENED, False),
        (S.REOPENED, S.REOPENED, False),
    ],
)
def test_is_reopen_edge(current: S, target: S, expected: bool) -> None:
    assert is_reopen_edge(current, target) is expected


@pytest.mark.parametrize(
    ("current", "target", "expected"),
    [
        (S.INTERNAL_REVISION, S.FINAL, True),
        (
            S.REOPENED,
            S.FINAL,
            True,
        ),  # re-close after reopen also records close metadata
        (S.FINAL, S.FINAL, False),  # self loop
        (
            S.ASSIGNING,
            S.FINAL,
            True,
        ),  # "into final" is True even when the edge is illegal
    ],
)
def test_is_closing_transition(current: S, target: S, expected: bool) -> None:
    assert is_closing_transition(current, target) is expected


def test_is_reopen_to_close_edge_only_reopened_to_final() -> None:
    assert is_reopen_to_close_edge(S.REOPENED, S.FINAL) is True
    assert is_reopen_to_close_edge(S.ASSIGNING, S.FINAL) is False
    assert is_reopen_to_close_edge(S.REOPENED, S.REOPENED) is False
