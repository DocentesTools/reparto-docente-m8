"""Lifecycle transition contracts for the three-stage planning domain.

Central, HTTP- and database-independent source of truth for the legal state
transitions of the new planning enums. Mirrors
:mod:`reparto_service.services.process_lifecycle` so every lifecycle table is
declared once, enforced once, and unit-testable without an HTTP or DB stack.

Three independent state machines are defined:

* :data:`TEACHING_PLAN_LIFECYCLE` — :class:`~reparto_service.enums.TeachingPlanStatus`,
  the operational stage of a teaching plan (plan §5.2, §9, §20.14).
* :data:`HOUR_REQUIREMENT_LIFECYCLE` —
  :class:`~reparto_service.enums.HourRequirementStatus`, the state of one
  generated, indivisible teacher-position slot (plan §5.9, §20.8).
* :data:`FEASIBILITY_LIFECYCLE` —
  :class:`~reparto_service.enums.FeasibilityStatus`, the *orthogonal*
  assignment-feasibility axis (plan §20.1, §20.14, §20.23).

Feasibility is ORTHOGONAL to the teaching-plan status (plan §20.1): the two are
never folded together. ``feasibility_status`` resets to ``NOT_EVALUATED`` on any
relevant change, which the feasibility table models as an edge from every result
state back to ``NOT_EVALUATED``.

Each table lists only the legal edges; everything not listed is illegal. The
tables are contracts consumed by the later model, service and route tasks; they
do not themselves mutate any row.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from typing import Generic, TypeVar

from reparto_service.enums import (
    FeasibilityStatus,
    HourRequirementStatus,
    TeachingPlanStatus,
)

StatusT = TypeVar("StatusT", bound=Enum)


class IllegalStateTransitionError(ValueError):
    """Raised when a caller requests an edge that is not in a lifecycle table.

    Mirrors :class:`reparto_service.services.process_lifecycle.IllegalTransitionError`
    but carries the machine name so a single error type can serve every planning
    lifecycle.
    """

    def __init__(
        self,
        machine: str,
        current: Enum,
        target: Enum,
        allowed: Iterable[Enum],
    ) -> None:
        self.machine = machine
        self.current = current
        self.target = target
        self.allowed = frozenset(allowed)
        super().__init__(
            f"Illegal {machine} transition: {current.value} → {target.value}. "
            f"Allowed targets from {current.value}: "
            f"{sorted(s.value for s in self.allowed) or 'none (terminal)'}."
        )


class TransitionTable(Generic[StatusT]):
    """An immutable, self-describing state-transition table.

    Wraps a ``{state: frozenset(targets)}`` mapping and exposes the same
    predicate surface as :mod:`reparto_service.services.process_lifecycle`
    (:meth:`is_allowed`, :meth:`assert_allowed`, :meth:`targets`,
    :meth:`is_terminal`) so controllers can enforce any planning lifecycle the
    same way the process lifecycle is enforced.
    """

    def __init__(
        self,
        name: str,
        table: dict[StatusT, frozenset[StatusT]],
    ) -> None:
        self.name = name
        self.table = table

    def targets(self, current: StatusT) -> frozenset[StatusT]:
        """Return the legal target states from ``current`` (empty if terminal)."""
        return self.table.get(current, frozenset())

    def is_allowed(self, current: StatusT, target: StatusT) -> bool:
        """Return ``True`` when the table allows ``current`` → ``target``.

        A self-edge (``current == target``) is never allowed, matching the
        process lifecycle.
        """
        if current == target:
            return False
        return target in self.targets(current)

    def assert_allowed(self, current: StatusT, target: StatusT) -> None:
        """Raise :class:`IllegalStateTransitionError` if the edge is illegal."""
        if not self.is_allowed(current, target):
            raise IllegalStateTransitionError(
                self.name, current, target, self.targets(current)
            )

    def is_terminal(self, status: StatusT) -> bool:
        """Return ``True`` when no further transition is possible from ``status``."""
        return not self.targets(status)


# ── TeachingPlan operational lifecycle (plan §5.2, §9, §20.14) ────────────────
#
# Unlocked plans ({DRAFT, UNBALANCED, BALANCED}) recompute their balance
# immediately (plan §20.14 "Draft/unlocked: recalculate immediately"), so those
# states interchange freely. Locking requires all three invariants (plan §20.1),
# and only invalidation of a LOCKED or REQUIREMENTS_GENERATED plan produces the
# STALE / RECONCILIATION_REQUIRED states.
_TP = TeachingPlanStatus
TEACHING_PLAN_LIFECYCLE: TransitionTable[TeachingPlanStatus] = TransitionTable(
    "TeachingPlanStatus",
    {
        # Initial state; the first balance computation lands on UNBALANCED or
        # BALANCED and never returns to "never computed".
        _TP.DRAFT: frozenset({_TP.UNBALANCED, _TP.BALANCED}),
        # Edits that reach both exact targets balance the plan.
        _TP.UNBALANCED: frozenset({_TP.BALANCED}),
        # Edits may break the balance again; a fully balanced+feasible plan may
        # be locked (plan §20.1).
        _TP.BALANCED: frozenset({_TP.UNBALANCED, _TP.LOCKED}),
        # Locked, no requirements yet: generate slots, unlock back to editing,
        # or go STALE on invalidation and require unlock (plan §20.14).
        _TP.LOCKED: frozenset({_TP.REQUIREMENTS_GENERATED, _TP.BALANCED, _TP.STALE}),
        # Generated: invalidation with no assignments needs regeneration
        # (→ STALE); invalidation with assignments needs manual reconciliation
        # (plan §20.14) — assignments are never silently dropped.
        _TP.REQUIREMENTS_GENERATED: frozenset({_TP.STALE, _TP.RECONCILIATION_REQUIRED}),
        # Stale: unlock + recompute (→ UNBALANCED/BALANCED) or regenerate the
        # unchanged slots (→ REQUIREMENTS_GENERATED) (plan §9, §20.14).
        _TP.STALE: frozenset(
            {_TP.UNBALANCED, _TP.BALANCED, _TP.REQUIREMENTS_GENERATED}
        ),
        # Reconciliation: after manual resolution, regeneration creates a new
        # generation and returns to REQUIREMENTS_GENERATED (plan §9).
        _TP.RECONCILIATION_REQUIRED: frozenset({_TP.REQUIREMENTS_GENERATED}),
    },
)


# ── HourRequirement slot lifecycle (plan §5.9, §20.8) ─────────────────────────
#
# A generated slot is indivisible: it is either AVAILABLE or fully ASSIGNED.
# A change to an assigned slot always routes through RECONCILIATION_REQUIRED and
# is never silently overwritten (plan §20.8).
_HR = HourRequirementStatus
HOUR_REQUIREMENT_LIFECYCLE: TransitionTable[HourRequirementStatus] = TransitionTable(
    "HourRequirementStatus",
    {
        # Fresh slot: assign it, or retire it (→ STALE) if regeneration
        # removes an unassigned position.
        _HR.AVAILABLE: frozenset({_HR.ASSIGNED, _HR.STALE}),
        # Undo returns it to AVAILABLE; a value change to an assigned slot
        # enters reconciliation (plan §20.8).
        _HR.ASSIGNED: frozenset({_HR.AVAILABLE, _HR.RECONCILIATION_REQUIRED}),
        # An unchanged slot revived by regeneration keeps its id and returns
        # to AVAILABLE (plan §20.8).
        _HR.STALE: frozenset({_HR.AVAILABLE}),
        # Reconciliation either preserves the assignment (→ ASSIGNED) or
        # supersedes/retires the slot (→ STALE) (plan §5.9, §20.8).
        _HR.RECONCILIATION_REQUIRED: frozenset({_HR.ASSIGNED, _HR.STALE}),
    },
)


# ── Feasibility axis lifecycle (plan §20.1, §20.14, §20.23) ───────────────────
#
# Orthogonal to the plan status. Evaluation turns NOT_EVALUATED into a result;
# any relevant change invalidates a result back to NOT_EVALUATED before a re-run
# (plan §20.14, §20.23). Results never mutate into one another directly — they
# always pass back through NOT_EVALUATED — so a stale witness can never be reused.
_FS = FeasibilityStatus
FEASIBILITY_LIFECYCLE: TransitionTable[FeasibilityStatus] = TransitionTable(
    "FeasibilityStatus",
    {
        _FS.NOT_EVALUATED: frozenset({_FS.FEASIBLE, _FS.INFEASIBLE, _FS.UNKNOWN}),
        _FS.FEASIBLE: frozenset({_FS.NOT_EVALUATED}),
        _FS.INFEASIBLE: frozenset({_FS.NOT_EVALUATED}),
        _FS.UNKNOWN: frozenset({_FS.NOT_EVALUATED}),
    },
)


__all__ = [
    "FEASIBILITY_LIFECYCLE",
    "HOUR_REQUIREMENT_LIFECYCLE",
    "TEACHING_PLAN_LIFECYCLE",
    "IllegalStateTransitionError",
    "TransitionTable",
]
