"""reparto_service domain services.

Domain services contain the pure business logic that the controllers
and routes call into. They are stateless, take a ``Session`` and return
Pydantic schemas (or raise domain-specific exceptions) so they can be
unit-tested without an HTTP client.
"""

from .calculations import (
    AssignmentCalculationService,
    PlanningCalculationService,
)
from .planning_lifecycle import (
    FEASIBILITY_LIFECYCLE,
    HOUR_REQUIREMENT_LIFECYCLE,
    TEACHING_PLAN_LIFECYCLE,
    IllegalStateTransitionError,
    TransitionTable,
)
from .process_lifecycle import (
    ALLOWED_TRANSITIONS,
    IllegalTransitionError,
    assert_allowed_transition,
    is_allowed_transition,
    is_closing_transition,
    is_reopen_edge,
    is_reopen_to_close_edge,
    is_terminal,
)
from .snapshots import SnapshotService
from .validations import (
    AssignmentValidationService,
    PlanValidationService,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "AssignmentCalculationService",
    "AssignmentValidationService",
    "FEASIBILITY_LIFECYCLE",
    "HOUR_REQUIREMENT_LIFECYCLE",
    "IllegalStateTransitionError",
    "IllegalTransitionError",
    "PlanValidationService",
    "PlanningCalculationService",
    "SnapshotService",
    "TEACHING_PLAN_LIFECYCLE",
    "TransitionTable",
    "assert_allowed_transition",
    "is_allowed_transition",
    "is_closing_transition",
    "is_reopen_edge",
    "is_reopen_to_close_edge",
    "is_terminal",
]
