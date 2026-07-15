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


class ExportArtifactType(str, Enum):
    """Generated artifact type for process history/export flows."""

    INTERNAL_DRAFT = "internal_draft"
    SCHOOL_LEADERSHIP = "school_leadership"
    FINAL = "final"
    TEACHER_SUMMARY = "teacher_summary"
    BACKUP = "backup"


class ExportArtifactFormat(str, Enum):
    """Storage/rendering format for an export artifact."""

    PDF = "pdf"
    CSV = "csv"
    JSON = "json"


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


# ── Three-stage planning adaptation (plan §5, §6, §20) ────────────────────────
#
# The enums below introduce the intermediate department teaching-load planning
# stage and the assignment-feasibility third invariant. They are ADDED alongside
# the existing two-stage vocabulary; the obsolete members (``AssignmentType``,
# ``RequirementType``, ``RequirementBalanceState``, ``TeacherBalanceState``,
# ``GlobalBalanceState``) are removed in the later model-redesign tasks, so this
# step keeps every existing model, service, route and test importable and green.
#
# Allowed transitions for the enums that carry a lifecycle (``TeachingPlanStatus``,
# ``HourRequirementStatus``, ``FeasibilityStatus``) are documented centrally in
# :mod:`reparto_service.services.planning_lifecycle`.


class SubjectAllocationCategory(str, Enum):
    """Whether a subject is a mandatory main input or an optional secondary one.

    Extensible code enum (plan §3.5); never a boolean ``is_main``. ``MAIN`` rows
    are mandatory planning candidates, ``SECONDARY`` rows optional. Forward-
    extensible code values, not runtime-configurable (plan §20.19).
    """

    MAIN = "main"
    SECONDARY = "secondary"


class ActivityType(str, Enum):
    """Descriptive teaching-activity category (plan §5.3, §5.6).

    Descriptive ONLY (plan §20.17): no domain behaviour may branch on this value
    (never ``if activity_type == CO_TEACHING``). It controls labels, filters,
    defaults, reports, exports and analytics. Actual behaviour derives from the
    hour, count and linked-group fields.
    """

    ORDINARY = "ordinary"
    TUTORING = "tutoring"
    CO_TEACHING = "co_teaching"
    SUPPORT = "support"
    DEPARTMENT_LEVEL = "department_level"
    OTHER = "other"


class GroupSubjectBulkMode(str, Enum):
    """Mode for a group-subject matrix bulk operation (plan §7.2).

    ``CREATE_MISSING`` only inserts cells for matched groups that have no row
    for the subject yet (existing rows are left untouched). ``UPDATE_EXISTING``
    only patches matched groups that already have a row (matched groups without
    one are reported as conflicts). ``UPSERT`` does both.
    """

    CREATE_MISSING = "create_missing"
    UPDATE_EXISTING = "update_existing"
    UPSERT = "upsert"


class TeachingActivitySource(str, Enum):
    """Origin of a teaching-plan activity (plan §5.6).

    Distinct from :class:`AssignmentSource` (which records how an *assignment*
    was made). ``MAIN_GENERATED`` activities are one-to-one with a single
    ``GroupSubject`` and single-group; multi-group activities are
    ``SECONDARY_MANUAL`` only (plan §20.10).
    """

    MAIN_GENERATED = "main_generated"
    SECONDARY_MANUAL = "secondary_manual"
    COPIED_FROM_PREVIOUS_YEAR = "copied_from_previous_year"
    IMPORTED = "imported"


class TeachingActivitySyncState(str, Enum):
    """Sync state of a ``MAIN_GENERATED`` activity vs its source ``GroupSubject``.

    Editing a source ``GroupSubject`` never silently overwrites a materialised
    activity (plan §20.10): the activity becomes ``OUT_OF_SYNC`` until an
    explicit sync-preview/apply reconciles it back to ``IN_SYNC``.
    """

    IN_SYNC = "in_sync"
    OUT_OF_SYNC = "out_of_sync"


class TeachingPlanStatus(str, Enum):
    """Operational stage of the intermediate teaching plan (plan §5.2).

    Orthogonal to :class:`FeasibilityStatus` (plan §20.1): ``status`` answers
    "what operational stage is the plan in", feasibility answers "can the
    indivisible slots be distributed exactly". The two are stored separately and
    never folded into a single value. Legal edges: see
    :data:`reparto_service.services.planning_lifecycle.TEACHING_PLAN_LIFECYCLE`.
    """

    DRAFT = "draft"
    UNBALANCED = "unbalanced"
    BALANCED = "balanced"
    LOCKED = "locked"
    REQUIREMENTS_GENERATED = "requirements_generated"
    STALE = "stale"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class FeasibilityStatus(str, Enum):
    """Assignment-partition feasibility — the third invariant (plan §20.1).

    Stored separately from :class:`TeachingPlanStatus`. ``NOT_EVALUATED`` is the
    default and the reset target: any relevant change resets feasibility to it
    rather than preserving a stale result (plan §20.14). ``UNKNOWN`` is the
    bounded-search-limit outcome and is fail-closed for gating (plan §20.6,
    §20.23). Legal edges: see
    :data:`reparto_service.services.planning_lifecycle.FEASIBILITY_LIFECYCLE`.
    """

    NOT_EVALUATED = "not_evaluated"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNKNOWN = "unknown"


class HourRequirementStatus(str, Enum):
    """Status of one generated, indivisible teacher-position slot (plan §5.9).

    Replaces the obsolete :class:`RequirementBalanceState` (which modelled
    partial/shared coverage that no longer exists). No partial-coverage state
    exists: a slot is either ``AVAILABLE`` or fully ``ASSIGNED``. Legal edges:
    see
    :data:`reparto_service.services.planning_lifecycle.HOUR_REQUIREMENT_LIFECYCLE`.
    """

    AVAILABLE = "available"
    ASSIGNED = "assigned"
    STALE = "stale"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class ParticipantBalanceState(str, Enum):
    """Per-participant assignment state (plan §6.2).

    Replaces the obsolete :class:`TeacherBalanceState` semantics.
    ``OVERLOADED_AUTHORIZED`` identifies ``extra_weekly_hours > 0``; it does NOT
    mean assigned hours exceed the target (plan §6.2).
    """

    PENDING = "pending"
    BALANCED = "balanced"
    OVERLOADED_AUTHORIZED = "overloaded_authorized"
    INACTIVE = "inactive"
    NOT_PARTICIPATING = "not_participating"


class DepartmentHourAllocationSource(str, Enum):
    """How a school-leadership allocation revision entered the system (plan §20.16).

    ``created_by_user_id`` records who transcribed/imported the revision, not
    necessarily who decided it. No new identity role is introduced (plan §20.16).
    """

    MANUAL_TRANSCRIPTION = "manual_transcription"
    FILE_IMPORT = "file_import"
    COPIED_DRAFT = "copied_draft"
    OTHER = "other"


class AssignmentEligibilityMode(str, Enum):
    """Teacher-eligibility policy (plan §20.4, CONFIRMED 2026-07-14).

    A single non-persisted domain policy constant, not a per-process column: it
    keeps the ``HOURS_ONLY`` assumption visible in code and stops ad hoc
    subject/group/stage filtering creeping back in. Restricted eligibility is a
    documented future extension only (plan §20.4).
    """

    HOURS_ONLY = "hours_only"


class WitnessStatus(str, Enum):
    """Availability of the feasibility witness (plan §20.24, OPTIONAL for MVP).

    The required hot-path guard is witness presence plus a fingerprint AND
    solver-version match (plan §20.24), so persisting this enum is not mandatory;
    it is defined centrally for the diagnostics/telemetry vocabulary.
    """

    AVAILABLE = "available"
    MISSING = "missing"
    INVALID = "invalid"
    EXPIRED = "expired"
