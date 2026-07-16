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


class AssignmentSource(str, Enum):
    """Origin of an assignment record."""

    DEPARTMENT_HEAD = "department_head"
    TEACHER_DIRECT = "teacher_direct"
    IMPORTED_FROM_PREVIOUS_YEAR = "imported_from_previous_year"
    SYSTEM_COPY = "system_copy"


class AssignmentStatus(str, Enum):
    """Lifecycle status of a single assignment record.

    Redesigned for the three-stage adaptation (plan §5.10, §20.9). An
    assignment is one teacher occupying one complete, indivisible requirement
    slot: it is either ``ACTIVE`` (the live occupancy) or ``CANCELLED`` (undone
    or reassigned away, retained for audit/version traceability). The obsolete
    two-stage ``DRAFT``/``CONFIRMED``/``OVERRIDDEN`` states — which modelled
    partial coverage and over-assignment overrides — are gone (plan §3.6, §5.10).

    SQLAlchemy stores the enum *member name*, so ``ACTIVE`` is the literal the
    active partial-unique indexes on
    :class:`~reparto_service.db_models.assignments.Assignment` filter on
    (one active assignment per requirement; one teacher per activity; plan
    §20.9); that name is part of the schema contract.
    """

    ACTIVE = "active"
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


class PlanningExportMode(str, Enum):
    """Strictness mode of a planning artifact export (plan §3.10, §7.8).

    ``DRAFT`` and ``PROVISIONAL`` artifacts are **never blocked** by a plan being
    inexact, unbalanced or stale — they carry the validation findings and both
    balance states so leadership can review an in-progress plan. ``FINAL``
    **retains blocking validation** (plan §7.8): a plan with any blocking finding
    cannot be exported as final.
    """

    DRAFT = "draft"
    PROVISIONAL = "provisional"
    FINAL = "final"


# ── Balance and validation states (plan 9) ────────────────────────────────────


class ValidationSeverity(str, Enum):
    """Severity of a single validation message (plan 9.4)."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


# ── Three-stage planning adaptation (plan §5, §6, §20) ────────────────────────
#
# The enums below carry the intermediate department teaching-load planning stage
# and the assignment-feasibility third invariant. The two-stage vocabulary they
# replaced (``AssignmentType``, ``RequirementType``, ``RequirementBalanceState``,
# ``TeacherBalanceState``, ``GlobalBalanceState``) is gone: those members modelled
# shared assignments, leadership-typed requirement rows, partial coverage and a
# single global balance — concepts the three-stage domain does not have (plan
# §3.1, §3.6, §5.10).
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

    Replaces the removed ``RequirementBalanceState`` (which modelled the
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

    Replaces the removed ``TeacherBalanceState`` semantics — in particular its
    ``OVERLOADED`` member, which meant "assigned beyond capacity, possibly via a
    department-head override". An over-target assignment is now impossible
    (plan §3.8): hours are authorized in advance or not at all.
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


class AuditEventType(str, Enum):
    """Canonical registry of every domain audit event type (plan §8.14, §13.1).

    Historically each controller passed a free-form ``event_type`` string to
    :meth:`DomainController.record_audit_event`. This registry is the single
    source of truth for that vocabulary so the whole three-stage audit trail is
    declared once, discoverable, and typo-proof, and so the read side can offer
    a validated ``event_type`` filter (plan §13.1 "Extend audit events").

    The member *values* are byte-identical to the strings the controllers have
    always persisted, so introducing the registry changes no stored data. The
    dotted ``entity.action`` convention is kept.
    """

    # ── Assignment process lifecycle (plan §8.4) ──────────────────────────────
    PROCESS_CREATED = "process.created"
    PROCESS_UPDATED = "process.updated"
    PROCESS_TRANSITIONED = "process.transitioned"
    PROCESS_REOPENED = "process.reopened"
    PROCESS_COPIED_FROM_PREVIOUS_YEAR = "process.copied_from_previous_year"
    PROCESS_RESTORED_FROM_BACKUP = "process.restored_from_backup"

    # ── Stage 1: configuration ────────────────────────────────────────────────
    PROCESS_TEACHER_CREATED = "process_teacher.created"
    PROCESS_TEACHER_UPDATED = "process_teacher.updated"
    PROCESS_TEACHER_EXTRA_HOURS_UPDATED = "process_teacher.extra_hours_updated"
    PROCESS_TEACHER_DELETED = "process_teacher.deleted"
    SUBJECT_CREATED = "subject.created"
    SUBJECT_UPDATED = "subject.updated"
    SUBJECT_DELETED = "subject.deleted"
    TEACHING_GROUP_CREATED = "teaching_group.created"
    TEACHING_GROUP_UPDATED = "teaching_group.updated"
    TEACHING_GROUP_DELETED = "teaching_group.deleted"

    # ── Stage 2: department teaching-load planning ────────────────────────────
    ALLOCATION_REVISED = "allocation.revised"
    GROUP_SUBJECT_CREATED = "group_subject.created"
    GROUP_SUBJECT_UPDATED = "group_subject.updated"
    GROUP_SUBJECT_DELETED = "group_subject.deleted"
    GROUP_SUBJECT_BULK_APPLIED = "group_subject.bulk_applied"
    TEACHING_PLAN_CREATED = "teaching_plan.created"
    TEACHING_PLAN_STALE = "teaching_plan.stale"
    # Reserved for the dedicated "Build plan lock and requirement generation"
    # workflow task (plan §7.3, §20.1); registered here so the lock/unlock audit
    # vocabulary is fixed up front even though the endpoints land later.
    TEACHING_PLAN_LOCKED = "teaching_plan.locked"
    TEACHING_PLAN_UNLOCKED = "teaching_plan.unlocked"
    TEACHING_ACTIVITY_CREATED = "teaching_activity.created"
    TEACHING_ACTIVITY_UPDATED = "teaching_activity.updated"
    TEACHING_ACTIVITY_DELETED = "teaching_activity.deleted"
    TEACHING_ACTIVITY_MATERIALIZED = "teaching_activity.materialized"
    TEACHING_ACTIVITY_IMPORTED = "teaching_activity.imported"
    REQUIREMENTS_GENERATED = "requirements.generated"
    REQUIREMENTS_RECONCILED = "requirements.reconciled"

    # ── Stage 3: assignment to teachers ───────────────────────────────────────
    ASSIGNMENT_CREATED = "assignment.created"
    ASSIGNMENT_DIRECT_CHOICE = "assignment.direct_choice"
    ASSIGNMENT_UPDATED = "assignment.updated"
    ASSIGNMENT_CANCELLED = "assignment.cancelled"
    SELECTION_TURN_STARTED = "selection_turn.started"
    SELECTION_TURN_COMPLETED = "selection_turn.completed"
    SELECTION_TURN_SKIPPED = "selection_turn.skipped"
    SELECTION_TURN_OVERRIDDEN = "selection_turn.overridden"


# ── SSE stream (plan §11, §20.25) ─────────────────────────────────────────────


class SseEventType(str, Enum):
    """Canonical registry of every server-sent domain event type (plan §11).

    The SSE vocabulary is deliberately *separate* from
    :class:`AuditEventType`. The audit trail is an append-only forensic record
    of who changed what; the SSE stream is a live cache-invalidation signal for
    connected viewers. The two overlap in name for the changes that are both
    audited and streamed, but they are not the same vocabulary: the stream adds
    control frames (``stream.opened``/``stream.gap``) that are never audited,
    and the audit trail records configuration edits that no viewer streams.

    Every payload is projected per viewer role before it leaves the process
    (plan §11 "LAN-safe response schemas appropriate to the viewer role",
    §20.25); see :mod:`reparto_service.services.sse`.
    """

    # ── Stream control (not domain changes) ───────────────────────────────────
    #: Sent once when a subscriber connects, carrying the current readiness so a
    #: client has a baseline without a separate fetch.
    STREAM_OPENED = "stream.opened"
    #: Sent when the subscriber's buffer overflowed and events were dropped. The
    #: client must refetch rather than assume continuity — the same best-effort
    #: contract the auth event stream uses (:mod:`reparto_service.core.events`).
    STREAM_GAP = "stream.gap"

    # ── Stage 2: department teaching-load planning (plan §3.11, §9) ────────────
    ALLOCATION_REVISED = "allocation.revised"
    TEACHING_PLAN_UPDATED = "teaching_plan.updated"
    TEACHING_PLAN_BALANCED = "teaching_plan.balanced"
    TEACHING_PLAN_LOCKED = "teaching_plan.locked"
    TEACHING_PLAN_STALE = "teaching_plan.stale"
    REQUIREMENTS_GENERATED = "requirements.generated"
    REQUIREMENTS_RECONCILED = "requirements.reconciled"
    REQUIREMENTS_RECONCILIATION_REQUIRED = "requirements.reconciliation_required"

    # ── Stage 1/3: participant hours (plan §3.8) ──────────────────────────────
    PARTICIPANT_EXTRA_HOURS_UPDATED = "participant.extra_hours_updated"


class SseAudience(str, Enum):
    """Viewer tier deciding how much of an event payload is published (§20.25).

    Ordered from most to least privileged; :func:`~reparto_service.services.sse.resolve_audience`
    only ever *downgrades* a caller from the tier their role grants, never up.
    """

    #: Department head / administrator: the full payload.
    DEPARTMENT_HEAD = "department_head"
    #: A participating teacher on the LAN: plan readiness, whether selection is
    #: blocked, and their **own** participant figures — never another teacher's.
    TEACHER = "teacher"
    #: The shared projection screen: readiness only, no identifiers at all.
    SHARED_SCREEN = "shared_screen"


class PlanReadiness(str, Enum):
    """Coarse, role-safe projection of the teaching-plan status (plan §20.25).

    The shared screen and teacher tiers see only this three-value axis instead
    of the full :class:`TeachingPlanStatus`, which would leak planning-stage
    detail to viewers who must not act on it.
    """

    #: The plan is generated: selections may proceed.
    READY = "ready"
    #: Planning is still in progress; the assignment stage cannot be entered.
    NOT_READY = "not_ready"
    #: An allocation change invalidated the plan; the head must reconcile before
    #: selections continue.
    RECALCULATION_REQUIRED = "recalculation_required"
