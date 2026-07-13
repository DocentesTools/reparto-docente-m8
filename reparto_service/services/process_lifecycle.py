"""AssignmentProcess lifecycle service.

The state machine in plan §8.4 is the single source of truth for which
``AssignmentProcessStatus`` transitions are legal. The controller and the
route layer delegate here so the table is enforced exactly once and can
be unit-tested without an HTTP stack.

Status values (plan 8.4):

* ``draft`` — created but not ready.
* ``ready_for_meeting`` — setup is complete; the head can open a meeting.
* ``meeting_open`` — a meeting session is open in LAN read mode (Phase 2).
* ``assigning`` — the meeting is actively producing assignments.
* ``department_proposal`` — the department has a draft proposal.
* ``sent_to_school_leadership`` — the proposal was sent to leadership.
* ``returned_by_school_leadership`` — leadership sent the proposal back.
* ``internal_revision`` — the department is revising after leadership.
* ``final`` — the process is closed and immutable.
* ``reopened`` — a final process has been reopened for further work.
* ``archived`` — kept for history; immutable.

Transition table (everything not listed is illegal):

* ``draft`` → ``ready_for_meeting``, ``archived``.
* ``ready_for_meeting`` → ``meeting_open``, ``draft``, ``archived``.
* ``meeting_open`` → ``assigning``, ``ready_for_meeting``, ``archived``.
* ``assigning`` → ``department_proposal``, ``meeting_open``, ``archived``.
* ``department_proposal`` → ``sent_to_school_leadership``,
  ``assigning``, ``archived``.
* ``sent_to_school_leadership`` → ``returned_by_school_leadership``,
  ``archived``.
* ``returned_by_school_leadership`` → ``internal_revision``, ``archived``.
* ``internal_revision`` → ``department_proposal``, ``final``, ``archived``.
* ``final`` → ``reopened`` (reopen endpoint only), ``archived``.
* ``reopened`` → ``internal_revision``, ``final``, ``archived``.
* ``archived`` → (terminal).

The reopen endpoint accepts only the ``final`` → ``reopened`` edge.
"""

from __future__ import annotations

from collections.abc import Iterable

from reparto_service.enums import AssignmentProcessStatus

# Edges allowed through the standard ``POST /transition`` endpoint.
ALLOWED_TRANSITIONS: dict[
    AssignmentProcessStatus, frozenset[AssignmentProcessStatus]
] = {
    AssignmentProcessStatus.DRAFT: frozenset(
        {
            AssignmentProcessStatus.READY_FOR_MEETING,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.READY_FOR_MEETING: frozenset(
        {
            AssignmentProcessStatus.MEETING_OPEN,
            AssignmentProcessStatus.DRAFT,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.MEETING_OPEN: frozenset(
        {
            AssignmentProcessStatus.ASSIGNING,
            AssignmentProcessStatus.READY_FOR_MEETING,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.ASSIGNING: frozenset(
        {
            AssignmentProcessStatus.DEPARTMENT_PROPOSAL,
            AssignmentProcessStatus.MEETING_OPEN,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.DEPARTMENT_PROPOSAL: frozenset(
        {
            AssignmentProcessStatus.SENT_TO_SCHOOL_LEADERSHIP,
            AssignmentProcessStatus.ASSIGNING,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.SENT_TO_SCHOOL_LEADERSHIP: frozenset(
        {
            AssignmentProcessStatus.RETURNED_BY_SCHOOL_LEADERSHIP,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.RETURNED_BY_SCHOOL_LEADERSHIP: frozenset(
        {
            AssignmentProcessStatus.INTERNAL_REVISION,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.INTERNAL_REVISION: frozenset(
        {
            AssignmentProcessStatus.DEPARTMENT_PROPOSAL,
            AssignmentProcessStatus.FINAL,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.FINAL: frozenset(
        {
            # ``reopened`` is only reachable through the dedicated
            # ``POST /reopen`` endpoint, but we still list it here so
            # ``is_allowed_transition`` and the audit log agree.
            AssignmentProcessStatus.REOPENED,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.REOPENED: frozenset(
        {
            AssignmentProcessStatus.INTERNAL_REVISION,
            AssignmentProcessStatus.FINAL,
            AssignmentProcessStatus.ARCHIVED,
        }
    ),
    AssignmentProcessStatus.ARCHIVED: frozenset(),
}


class IllegalTransitionError(ValueError):
    """Raised when a caller requests a transition that is not in the table."""

    def __init__(
        self,
        current: AssignmentProcessStatus,
        target: AssignmentProcessStatus,
        allowed: Iterable[AssignmentProcessStatus],
    ) -> None:
        self.current = current
        self.target = target
        self.allowed = frozenset(allowed)
        super().__init__(
            f"Illegal transition: {current.value} → {target.value}. "
            f"Allowed targets from {current.value}: "
            f"{sorted(s.value for s in self.allowed) or 'none (terminal)'}."
        )


def is_allowed_transition(
    current: AssignmentProcessStatus,
    target: AssignmentProcessStatus,
) -> bool:
    """Return ``True`` when the table allows the transition."""
    if current == target:
        return False
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_allowed_transition(
    current: AssignmentProcessStatus,
    target: AssignmentProcessStatus,
) -> None:
    """Raise ``IllegalTransitionError`` if the edge is not allowed."""
    if not is_allowed_transition(current, target):
        raise IllegalTransitionError(
            current, target, ALLOWED_TRANSITIONS.get(current, frozenset())
        )


def is_reopen_edge(
    current: AssignmentProcessStatus,
    target: AssignmentProcessStatus,
) -> bool:
    """Return ``True`` when the edge is the reopen edge (``final`` → ``reopened``)."""
    return (
        current == AssignmentProcessStatus.FINAL
        and target == AssignmentProcessStatus.REOPENED
    )


def is_closing_transition(
    current: AssignmentProcessStatus,
    target: AssignmentProcessStatus,
) -> bool:
    """Return ``True`` when the edge transitions *into* ``final``.

    Independent of whether the edge is in the table: the controller
    uses this to record ``closed_at`` / ``closed_by_user_id`` on any
    transition that lands the process in ``final`` (including the
    ``reopened`` → ``final`` re-close). The function does not, by
    itself, validate that the transition is legal — combine it with
    :func:`is_allowed_transition` for the full guard.
    """
    return (
        current != AssignmentProcessStatus.FINAL
        and target == AssignmentProcessStatus.FINAL
    )


def is_reopen_to_close_edge(
    current: AssignmentProcessStatus,
    target: AssignmentProcessStatus,
) -> bool:
    """Return ``True`` when the edge re-closes a reopened process."""
    return (
        current == AssignmentProcessStatus.REOPENED
        and target == AssignmentProcessStatus.FINAL
    )


# Backwards-compatible alias used by the previous controller implementation.
is_close_edge = is_closing_transition


def is_terminal(status: AssignmentProcessStatus) -> bool:
    """Return ``True`` when the status does not allow any further transition."""
    return not ALLOWED_TRANSITIONS.get(status, frozenset())


__all__ = [
    "ALLOWED_TRANSITIONS",
    "IllegalTransitionError",
    "assert_allowed_transition",
    "is_allowed_transition",
    "is_close_edge",
    "is_reopen_edge",
    "is_reopen_to_close_edge",
    "is_terminal",
]
