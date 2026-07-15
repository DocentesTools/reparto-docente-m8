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
from .validations import (
    PlanValidationService,
)
from .summary import (
    CODE_PROCESS_BALANCED,
    CODE_PROCESS_HAS_OVERAGE,
    CODE_PROCESS_HAS_PENDING,
    CODE_REQ_FULLY_ASSIGNED,
    CODE_REQ_NOT_FULLY_ASSIGNED,
    CODE_REQ_OVER_ASSIGNED,
    CODE_REQ_OVER_ASSIGNED_OVERRIDDEN,
    CODE_TEACHER_BALANCED,
    CODE_TEACHER_OVERLOADED,
    CODE_TEACHER_OVERLOADED_OVERRIDDEN,
    SummaryService,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "AssignmentCalculationService",
    "CODE_PROCESS_BALANCED",
    "CODE_PROCESS_HAS_OVERAGE",
    "CODE_PROCESS_HAS_PENDING",
    "CODE_REQ_FULLY_ASSIGNED",
    "CODE_REQ_NOT_FULLY_ASSIGNED",
    "CODE_REQ_OVER_ASSIGNED",
    "CODE_REQ_OVER_ASSIGNED_OVERRIDDEN",
    "CODE_TEACHER_BALANCED",
    "CODE_TEACHER_OVERLOADED",
    "CODE_TEACHER_OVERLOADED_OVERRIDDEN",
    "FEASIBILITY_LIFECYCLE",
    "HOUR_REQUIREMENT_LIFECYCLE",
    "IllegalStateTransitionError",
    "IllegalTransitionError",
    "PlanValidationService",
    "PlanningCalculationService",
    "SummaryService",
    "TEACHING_PLAN_LIFECYCLE",
    "TransitionTable",
    "assert_allowed_transition",
    "is_allowed_transition",
    "is_closing_transition",
    "is_reopen_edge",
    "is_reopen_to_close_edge",
    "is_terminal",
]
