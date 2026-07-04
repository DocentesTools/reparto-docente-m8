"""Domain enums for reparto_service.

Every enum here is used both as a SQLAlchemy column type (in
``reparto_service.db_models``) and as a Pydantic/JSON-serialised value
(returned by the API). Single source of truth: ``(str, Enum)`` Python
enums that the FastAPI response models accept without coercion.
"""

from __future__ import annotations

from enum import Enum


# ── AssignmentProcess lifecycle (plan 8.4) ────────────────────────────────────


class AssignmentProcessStatus(str, Enum):
    """Status of an annual departmental assignment process."""

    DRAFT = "draft"
    READY_FOR_MEETING = "ready_for_meeting"
    MEETING_OPEN = "meeting_open"
    ASSIGNING = "assigning"
    DEPARTMENT_PROPOSAL = "department_proposal"
    SENT_TO_SCHOOL_LEADERSHIP = "sent_to_school_leadership"
    RETURNED_BY_SCHOOL_LEADERSHIP = "returned_by_school_leadership"
    INTERNAL_REVISION = "internal_revision"
    FINAL = "final"
    REOPENED = "reopened"
    ARCHIVED = "archived"


class AcademicYearStatus(str, Enum):
    """Status of an academic year record."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ProcessTeacherStatus(str, Enum):
    """Status of a teacher inside one assignment process."""

    ACTIVE = "active"
    INACTIVE = "inactive"


# ── Domain classification enums (plan 8.7, 8.8, 8.9, 8.10) ───────────────────


class SelectionOrderMode(str, Enum):
    """How the configured selection order is enforced during a meeting."""

    NONE = "none"
    INFORMATIVE = "informative"
    STRICT = "strict"


class MeetingSessionStatus(str, Enum):
    """Status of an assignment meeting session."""

    PREPARED = "prepared"
    OPEN = "open"
    SELECTING = "selecting"
    PAUSED = "paused"
    CLOSED = "closed"
    REOPENED = "reopened"


class SelectionTurnStatus(str, Enum):
    """Status of one teacher turn inside a meeting session."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    OVERRIDDEN = "overridden"


class RequirementType(str, Enum):
    """Type of required hours communicated by school leadership."""

    ORDINARY = "ordinary"
    REINFORCEMENT = "reinforcement"
    SPLIT_GROUP = "split_group"
    OPTIONAL = "optional"
    BILINGUAL = "bilingual"
    OTHER = "other"


class AssignmentType(str, Enum):
    """Type of an assignment record."""

    MAIN = "main"
    SHARED = "shared"
    REINFORCEMENT = "reinforcement"
    SPLIT_GROUP = "split_group"
    OTHER = "other"


class AssignmentSource(str, Enum):
    """Origin of an assignment record."""

    DEPARTMENT_HEAD = "department_head"
    TEACHER_DIRECT = "teacher_direct"
    IMPORTED_FROM_PREVIOUS_YEAR = "imported_from_previous_year"
    SYSTEM_COPY = "system_copy"


class AssignmentStatus(str, Enum):
    """Status of a single assignment record."""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    OVERRIDDEN = "overridden"
    CANCELLED = "cancelled"


# ── Balance and validation states (plan 9) ────────────────────────────────────


class GlobalBalanceState(str, Enum):
    """Aggregate balance state of an assignment process."""

    BALANCED = "balanced"
    PENDING = "pending"
    EXCEEDED = "exceeded"
    WARNING = "warning"


class TeacherBalanceState(str, Enum):
    """Per-teacher balance state inside one assignment process."""

    BALANCED = "balanced"
    PENDING = "pending"
    OVERLOADED = "overloaded"
    INACTIVE = "inactive"
    NOT_PARTICIPATING = "not_participating"


class RequirementBalanceState(str, Enum):
    """Per-requirement balance state."""

    UNCOVERED = "uncovered"
    PARTIAL = "partial"
    COVERED = "covered"
    OVER_ASSIGNED = "over_assigned"
    EXPLICITLY_SHARED = "explicitly_shared"


class ValidationSeverity(str, Enum):
    """Severity of a single validation message (plan 9.4)."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"
