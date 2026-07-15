"""reparto_service API schemas.

Domain request/response schemas that are not bound to a database table
(summary, balance, validation, dashboard) live in dedicated submodules
and are re-exported from here.
"""

from .planning import (
    AssignmentSummary,
    GroupBalance,
    ParticipantBalance,
    PlanBalance,
    PlanValidationMessage,
    PlanValidationReport,
    TeacherLoadBalance,
)
from .summary import (
    GlobalBalance,
    ProcessDashboard,
    ProcessSummary,
    RequirementBalance,
    TeacherBalance,
    ValidationMessage,
)

__all__ = [
    "AssignmentSummary",
    "GlobalBalance",
    "GroupBalance",
    "ParticipantBalance",
    "PlanBalance",
    "PlanValidationMessage",
    "PlanValidationReport",
    "ProcessDashboard",
    "ProcessSummary",
    "RequirementBalance",
    "TeacherBalance",
    "TeacherLoadBalance",
    "ValidationMessage",
]
