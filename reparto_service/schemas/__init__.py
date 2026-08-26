"""reparto_service API schemas.

Domain request/response schemas that are not bound to a database table
(balance, validation, dashboard, exchange, event) live in dedicated submodules
and are re-exported from here.
"""

from .dashboard import (
    AssignmentSection,
    CurrentTurnSummary,
    PlanningSection,
    ProcessDashboard,
    ProcessSummary,
    TeacherLanSummary,
)
from .exchange import (
    PlanningExportActivity,
    PlanningExportArtifact,
    PlanningImportActivity,
    PlanningImportRequest,
    PlanningImportResult,
)
from .planning import (
    AssignmentSummary,
    AssignmentValidationReport,
    GroupBalance,
    ParticipantBalance,
    PlanBalance,
    PlanValidationMessage,
    PlanValidationReport,
    TeacherLoadBalance,
)

__all__ = [
    "AssignmentSection",
    "AssignmentSummary",
    "AssignmentValidationReport",
    "CurrentTurnSummary",
    "GroupBalance",
    "ParticipantBalance",
    "PlanBalance",
    "PlanningExportActivity",
    "PlanningExportArtifact",
    "PlanningImportActivity",
    "PlanningImportRequest",
    "PlanningImportResult",
    "PlanningSection",
    "PlanValidationMessage",
    "PlanValidationReport",
    "ProcessDashboard",
    "ProcessSummary",
    "TeacherLanSummary",
    "TeacherLoadBalance",
]
