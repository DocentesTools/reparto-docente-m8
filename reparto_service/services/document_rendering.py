"""Deterministic text-backed export documents (plan §15.1-15.3).

Phase 5 settled that the four *document* export types are rendered as
deterministic text stored in ``ExportArtifact.content`` under the ``pdf``
format, "until a template renderer is introduced". This module is that
renderer: :meth:`DocumentRenderingService.render` turns one process snapshot
into the document plan §15 describes for the requested type.

Two properties the rest of the export flow depends on:

* **It is a pure function of its inputs.** No clock, no session, no query, no
  ambient locale and no ambient catalog load: the controller injects a
  revision-labelled :class:`DocumentCatalog` whose locale is persisted on the
  artifact row (C13/C14), never the request ``ContextVar`` the HTTP error
  boundary reads. The
  artifact's ``checksum`` is a SHA-256 of what this returns, so two exports of
  an unchanged process must produce the same bytes — that is what makes the
  checksum able to answer "has anything moved since the last document?". Where
  plan §15.1 asks for a "date", the document prints the process's own
  ``updated_at`` (the state it describes) rather than the wall clock, which
  keeps that property intact and is the more useful date besides. The checksum
  is never salted with the locale: once the catalog (C14) makes the text
  depend on it, two languages differ by content, and therefore by checksum,
  on their own.
* **It never refuses.** A document describes the process as it stands, so a
  missing plan, an unbalanced plan or an incomplete reparto are *reported in
  the document* rather than raised. Only the strict ``final`` export is gated,
  and that gate lives in the controller (`_ensure_no_blocking_validations`),
  ahead of this module. A draft document of a half-finished process is exactly
  the artifact a department head needs mid-meeting.

Every hour value in a snapshot is already the canonical two-place string of
plan §3.9; sums are taken in :class:`~decimal.Decimal` and quantized back to
two places before printing, so no total is ever computed in binary floating
point.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Optional

from reparto_service.core.decimals import quantize_hours
from reparto_service.enums import ExportArtifactType
from reparto_service.services.document_catalog import (
    DOCUMENT_TRACE_ID_LINE_PREFIXES,
    DocumentCatalog,
)
from reparto_service.services.stale_reasons import ServiceStaleReason

#: The literal JSON value an ``ACTIVE`` assignment status serialises to.
_ACTIVE_ASSIGNMENT = "active"

#: Requirement statuses that mean the slot still needs a teacher (plan §15.1).
#: There is no partial-coverage state (plan §5.9): a slot is AVAILABLE or fully
#: ASSIGNED, and RECONCILIATION_REQUIRED is a live slot whose coverage the head
#: must revisit — so both belong in a document's "uncovered" list.
_UNCOVERED_REQUIREMENT_STATUSES = frozenset({"available", "reconciliation_required"})

#: The status of a slot superseded by a later generation.
_STALE_REQUIREMENT_STATUS = "stale"

_ZERO = Decimal("0.00")

_RULE = "=" * 72
_THIN_RULE = "-" * 72


@dataclass(frozen=True)
class DocumentIdentityContext:
    """Human labels joined by the controller and injected into the renderer.

    This data deliberately remains separate from the restorable snapshot. The
    renderer therefore stays pure while backup JSON keeps its established
    schema and byte identity.
    """

    school_name: Optional[str]
    department_name: Optional[str]
    academic_year_label: Optional[str]
    teacher_display_names_by_profile_id: Mapping[str, str]


class DocumentRenderingService:
    """Render the plan §15 export documents as deterministic text."""

    @staticmethod
    def render(
        export_type: ExportArtifactType,
        snapshot: dict[str, Any],
        versions: list[dict[str, Any]],
        identity: DocumentIdentityContext,
        catalog: DocumentCatalog,
        stale_reason: ServiceStaleReason | None = None,
    ) -> str:
        """Render ``export_type`` from ``snapshot``, or raise for a non-document.

        ``versions`` is the process's version summaries, oldest first, as
        :meth:`HistoryController._version_summaries` builds them; the leadership
        and final documents name the latest one (plan §15.2, §15.3).

        ``catalog`` models the language, vocabulary revision, and gettext
        translator as one injected input. The renderer never reads the request
        context or loads catalog files. ``stale_reason`` is supplied only for a
        newly structured generated reason; absent metadata is a historical or
        user-authored string and remains verbatim.
        """
        if not isinstance(catalog, DocumentCatalog):
            raise TypeError(f"catalog must be a DocumentCatalog, got {catalog!r}")
        view = _SnapshotView(snapshot, versions, identity, catalog, stale_reason)
        if export_type == ExportArtifactType.INTERNAL_DRAFT:
            return _render_internal_draft(view)
        if export_type == ExportArtifactType.SCHOOL_LEADERSHIP:
            return _render_school_leadership(view)
        if export_type == ExportArtifactType.TEACHER_SUMMARY:
            return _render_teacher_summary(view)
        if export_type == ExportArtifactType.FINAL:
            return _render_final(view)
        raise AssertionError(
            f"{export_type} is not a rendered document type"
        )  # pragma: no cover


# ── Snapshot indexing ────────────────────────────────────────────────────────


class _SnapshotView:
    """Index one snapshot into the lookups the four documents read.

    The snapshot is a set of flat, id-keyed sections; every document needs the
    same handful of joins across them (an assignment reaches its teacher, its
    slot's hours, its activity's subject and that activity's groups). Building
    them once here keeps each renderer a description of its document rather
    than a re-derivation of the same maps.
    """

    def __init__(
        self,
        snapshot: dict[str, Any],
        versions: list[dict[str, Any]],
        identity: DocumentIdentityContext,
        catalog: DocumentCatalog,
        stale_reason: ServiceStaleReason | None,
    ):
        self.process: dict[str, Any] = snapshot["process"]
        self.plan: Optional[dict[str, Any]] = snapshot.get("teaching_plan")
        self.versions = versions
        self.identity = identity
        self.catalog = catalog
        self.stale_reason = stale_reason
        self.teachers: list[dict[str, Any]] = snapshot["teachers"]
        self.requirements: list[dict[str, Any]] = snapshot["requirements"]
        self.assignments: list[dict[str, Any]] = snapshot["assignments"]
        self.allocation_revisions: list[dict[str, Any]] = snapshot[
            "allocation_revisions"
        ]

        self.subject_by_id = _by_id(snapshot["subjects"])
        self.group_by_id = _by_id(snapshot["teaching_groups"])
        self.cell_by_id = _by_id(snapshot["group_subjects"])
        self.activity_by_id = _by_id(snapshot["teaching_activities"])
        self.requirement_by_id = _by_id(self.requirements)
        self.teacher_by_id = _by_id(self.teachers)

        # An activity reaches its groups through the link table; a document
        # names the group codes, so resolve the whole chain once.
        self.group_codes_by_activity: dict[str, list[str]] = {}
        for link in snapshot["teaching_activity_groups"]:
            cell = self.cell_by_id.get(str(link["group_subject_id"]))
            if cell is None:
                continue
            group = self.group_by_id.get(str(cell["teaching_group_id"]))
            if group is None:
                continue
            codes = self.group_codes_by_activity.setdefault(
                str(link["teaching_activity_id"]), []
            )
            code = str(group["group_code"])
            if code not in codes:
                codes.append(code)
        for codes in self.group_codes_by_activity.values():
            codes.sort()

        self.active_assignments = [
            row
            for row in self.assignments
            if str(row["status"]).lower() == _ACTIVE_ASSIGNMENT
        ]

    # ── Derived figures ──────────────────────────────────────────────────────

    @property
    def allocated_group_hours(self) -> Decimal:
        """The current (non-superseded) allocation, or zero when there is none."""
        current = [
            row for row in self.allocation_revisions if row.get("superseded_at") is None
        ]
        if not current:
            return _ZERO
        latest = max(current, key=lambda row: int(row["revision_number"]))
        return _hours(latest["allocated_group_weekly_hours"])

    @property
    def required_hours(self) -> Decimal:
        """Total hours across every slot still in play."""
        return _sum_hours(
            row["required_teacher_hours"] for row in self.live_requirements
        )

    @property
    def assigned_hours(self) -> Decimal:
        """Total hours carried by the active assignments."""
        return _sum_hours(
            self.requirement_hours(row["hour_requirement_id"])
            for row in self.active_assignments
        )

    @property
    def live_requirements(self) -> list[dict[str, Any]]:
        """Slots still in play for the current generation.

        A snapshot keeps the retired rows so generation lineage round-trips, so
        a document must exclude them explicitly — by lineage (retired by, or
        superseded into, a later generation) as well as by status, since the
        two are set together and either one alone would be a silent overcount.
        """
        return [
            row
            for row in self.requirements
            if row.get("retired_generation") is None
            and row.get("superseded_by_requirement_id") is None
            and str(row["status"]).lower() != _STALE_REQUIREMENT_STATUS
        ]

    @property
    def uncovered_requirements(self) -> list[dict[str, Any]]:
        """Live slots still waiting for a teacher (plan §15.1)."""
        return [
            row
            for row in self.live_requirements
            if str(row["status"]).lower() in _UNCOVERED_REQUIREMENT_STATUSES
        ]

    @property
    def latest_version(self) -> Optional[dict[str, Any]]:
        return self.versions[-1] if self.versions else None

    def requirement_hours(self, requirement_id: Any) -> str:
        """The slot's canonical hour string; ``"0.00"`` when it is not in the
        snapshot, so an orphaned assignment cannot make a total unreadable."""
        slot = self.requirement_by_id.get(str(requirement_id))
        return "0.00" if slot is None else str(slot["required_teacher_hours"])

    @staticmethod
    def teacher_target(teacher: dict[str, Any]) -> Decimal:
        """``base + extra`` (plan §3.8), recomputed from the stored parts.

        ``target_weekly_hours`` and ``is_overloaded`` are computed fields on
        the *public* schema, so a snapshot — a dump of the table model — does
        not carry them.
        """
        return quantize_hours(
            Decimal(str(teacher["base_weekly_hours"]))
            + Decimal(str(teacher["extra_weekly_hours"]))
        )

    @staticmethod
    def teacher_is_overloaded(teacher: dict[str, Any]) -> bool:
        """True when the head authorized extra hours (plan §3.8)."""
        return Decimal(str(teacher["extra_weekly_hours"])) > _ZERO

    def assigned_hours_for_teacher(self, teacher_id: str) -> Decimal:
        return _sum_hours(
            self.requirement_hours(row["hour_requirement_id"])
            for row in self.active_assignments
            if str(row["process_teacher_id"]) == teacher_id
        )

    def activity_label(self, activity_id: Any) -> str:
        """``Subject (GROUP-A, GROUP-B)`` or a non-identifying placeholder."""
        activity = self.activity_by_id.get(str(activity_id))
        if activity is None:
            return self.catalog.text(
                "document.placeholder.teaching_activity_unavailable"
            )
        subject = self.subject_by_id.get(str(activity["subject_id"]))
        name = (
            str(subject["name"])
            if subject
            else self.catalog.text("document.placeholder.subject_unavailable")
        )
        codes = self.group_codes_by_activity.get(str(activity_id), [])
        return f"{name} ({', '.join(codes)})" if codes else name

    def teacher_label(self, teacher_id: Any) -> str:
        """Resolve a participant through the separately enriched profile map."""
        teacher = self.teacher_by_id.get(str(teacher_id))
        if teacher is None:
            return self.catalog.text("document.placeholder.process_teacher_unavailable")
        display_name = self.identity.teacher_display_names_by_profile_id.get(
            str(teacher["teacher_profile_id"])
        )
        return display_name or self.catalog.text(
            "document.placeholder.teacher_profile_unavailable"
        )

    @property
    def school_label(self) -> str:
        """School name, with no identifier fallback."""
        return self.identity.school_name or self.catalog.text(
            "document.placeholder.school_unavailable"
        )

    @property
    def department_label(self) -> str:
        """Department name, with no identifier fallback."""
        return self.identity.department_name or self.catalog.text(
            "document.placeholder.department_unavailable"
        )

    @property
    def academic_year_label(self) -> str:
        """Academic-year label, with no identifier fallback."""
        return self.identity.academic_year_label or self.catalog.text(
            "document.placeholder.academic_year_unavailable"
        )


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in rows}


def _hours(value: Any) -> Decimal:
    return quantize_hours(Decimal(str(value)))


def _sum_hours(values: Any) -> Decimal:
    total = _ZERO
    for value in values:
        total += Decimal(str(value))
    return quantize_hours(total)


def _fmt(value: Decimal) -> str:
    """A two-place decimal string that may carry a sign.

    Deliberately not :func:`~reparto_service.core.decimals.hours_to_str`: that
    validates an *hour quantity* and refuses a negative, which is right for a
    stored field and wrong for a balance. "assigned minus target" is signed by
    definition — a participant below target is the ordinary mid-process state,
    and the document has to be able to print it.
    """
    return str(quantize_hours(value))


# ── Shared document furniture ────────────────────────────────────────────────


def _header(
    view: _SnapshotView,
    title_code: str,
    banner_code: Optional[str],
) -> list[str]:
    """The block every document opens with (plan §15.1: date and status)."""
    lines = [_RULE, view.catalog.text(title_code), _RULE]
    if banner_code is not None:
        lines.append(view.catalog.text(banner_code))
        lines.append("")
    lines.extend(
        [
            view.catalog.text(
                "document.field.process_reference",
                {"value": str(view.process["id"])},
            ),
            view.catalog.text(
                "document.field.process_status",
                {"value": view.catalog.enum_label(view.process["status"])},
            ),
            view.catalog.text(
                "document.field.state_as_of",
                {"value": str(view.process["updated_at"])},
            ),
        ]
    )
    return lines


def _plan_lines(view: _SnapshotView) -> list[str]:
    if view.plan is None:
        return [view.catalog.text("document.field.plan_missing")]
    return [
        view.catalog.text(
            "document.field.plan_status",
            {"value": view.catalog.enum_label(view.plan["status"])},
        ),
        view.catalog.text(
            "document.field.feasibility",
            {"value": view.catalog.enum_label(view.plan["feasibility_status"])},
        ),
        view.catalog.text(
            "document.field.generation",
            {"value": int(view.plan["current_generation_number"])},
        ),
    ]


def _version_line(view: _SnapshotView) -> str:
    version = view.latest_version
    if version is None:
        return view.catalog.text("document.field.version_missing")
    return view.catalog.text(
        "document.field.version",
        {
            "number": int(version["version_number"]),
            "status": view.catalog.enum_label(version["status"]),
        },
    )


def _section(view: _SnapshotView, title_code: str, rows: list[str]) -> list[str]:
    """A titled block, or an explicit empty marker — never a silent gap."""
    lines = ["", _THIN_RULE, view.catalog.text(title_code), _THIN_RULE]
    lines.extend(rows if rows else [view.catalog.text("document.section.empty")])
    return lines


def _balance_rows(view: _SnapshotView) -> list[str]:
    allocated = view.allocated_group_hours
    required = view.required_hours
    assigned = view.assigned_hours
    return [
        view.catalog.text(
            "document.balance.allocated_group_hours",
            {"hours": f"{_fmt(allocated):>10}"},
        ),
        view.catalog.text(
            "document.balance.required_slot_hours",
            {"hours": f"{_fmt(required):>10}"},
        ),
        view.catalog.text(
            "document.balance.assigned_hours",
            {"hours": f"{_fmt(assigned):>10}"},
        ),
        view.catalog.text(
            "document.balance.uncovered_hours",
            {"hours": f"{_fmt(quantize_hours(required - assigned)):>10}"},
        ),
    ]


def _teacher_balance_rows(view: _SnapshotView) -> list[str]:
    rows = []
    for teacher in view.teachers:
        teacher_id = str(teacher["id"])
        target = view.teacher_target(teacher)
        assigned = view.assigned_hours_for_teacher(teacher_id)
        difference = quantize_hours(assigned - target)
        flags = (
            view.catalog.text("document.teacher.overload_authorized")
            if view.teacher_is_overloaded(teacher)
            else ""
        )
        rows.append(
            view.catalog.text(
                "document.teacher.balance",
                {
                    "teacher": view.teacher_label(teacher_id),
                    "target": _fmt(target),
                    "assigned": _fmt(assigned),
                    "difference": _fmt(difference),
                    "flags": flags,
                },
            )
        )
    return rows


def _assignments_by_teacher_rows(view: _SnapshotView) -> list[str]:
    rows = []
    for teacher in view.teachers:
        teacher_id = str(teacher["id"])
        mine = [
            row
            for row in view.active_assignments
            if str(row["process_teacher_id"]) == teacher_id
        ]
        assigned = view.assigned_hours_for_teacher(teacher_id)
        rows.append(
            view.catalog.plural(
                "document.assignment.count",
                len(mine),
                {
                    "teacher": view.teacher_label(teacher_id),
                    "count": len(mine),
                    "hours": _fmt(assigned),
                },
            )
        )
        for row in sorted(mine, key=lambda item: str(item["id"])):
            hours = view.requirement_hours(row["hour_requirement_id"])
            rows.append(
                view.catalog.text(
                    "document.teacher.assignment_detail",
                    {
                        "activity": view.activity_label(row["teaching_activity_id"]),
                        "hours": hours,
                        "source": view.catalog.enum_label(row["source"]),
                    },
                )
            )
    return rows


def _assignments_by_group_rows(view: _SnapshotView) -> list[str]:
    """Active assignments folded onto the groups their activity teaches."""
    by_group: dict[str, list[str]] = {}
    for row in sorted(view.active_assignments, key=lambda item: str(item["id"])):
        codes = view.group_codes_by_activity.get(
            str(row["teaching_activity_id"]),
            [view.catalog.text("document.placeholder.unlinked")],
        )
        hours = view.requirement_hours(row["hour_requirement_id"])
        for code in codes:
            by_group.setdefault(code, []).append(
                view.catalog.text(
                    "document.group.assignment",
                    {
                        "activity": view.activity_label(row["teaching_activity_id"]),
                        "hours": hours,
                        "teacher": view.teacher_label(row["process_teacher_id"]),
                    },
                )
            )
    rows = []
    for code in sorted(by_group):
        rows.append(view.catalog.text("document.group.heading", {"code": code}))
        rows.extend(by_group[code])
    return rows


def _uncovered_rows(view: _SnapshotView) -> list[str]:
    return [
        view.catalog.text(
            "document.requirement.uncovered",
            {
                "activity": view.activity_label(row["teaching_activity_id"]),
                "position": int(row["position_index"]),
                "hours": str(row["required_teacher_hours"]),
                "status": view.catalog.enum_label(row["status"]),
            },
        )
        for row in view.uncovered_requirements
    ]


def _exception_rows(view: _SnapshotView) -> list[str]:
    """Authorized overloads with the head's written justification (plan §15.2)."""
    rows = []
    for teacher in view.teachers:
        if not view.teacher_is_overloaded(teacher):
            continue
        reason = teacher.get("extra_hours_reason") or view.catalog.text(
            "document.exception.no_reason_recorded"
        )
        rows.append(
            "\n".join(
                (
                    view.catalog.text(
                        "document.exception.summary",
                        {
                            "teacher": view.teacher_label(teacher["id"]),
                            "base_hours": str(teacher["base_weekly_hours"]),
                            "extra_hours": str(teacher["extra_weekly_hours"]),
                        },
                    ),
                    view.catalog.text(
                        "document.exception.justification",
                        {"reason": str(reason)},
                    ),
                )
            )
        )
    return rows


def _warning_rows(view: _SnapshotView) -> list[str]:
    """Everything a reader must not mistake for a settled plan (plan §15.1)."""
    rows = []
    if view.plan is None:
        rows.append(view.catalog.text("document.warning.no_teaching_plan"))
    else:
        if view.plan.get("stale_reason"):
            stored_reason = str(view.plan["stale_reason"])
            reason = (
                view.catalog.stored_reason(
                    view.stale_reason.code,
                    view.stale_reason.message,
                    view.stale_reason.params,
                )
                if view.stale_reason is not None
                else stored_reason
            )
            rows.append(
                view.catalog.text("document.warning.plan_stale", {"reason": reason})
            )
        if str(view.plan["feasibility_status"]).lower() != "feasible":
            rows.append(
                view.catalog.text(
                    "document.warning.feasibility_not_validated",
                    {
                        "status": view.catalog.enum_label(
                            view.plan["feasibility_status"]
                        )
                    },
                )
            )
    uncovered = view.uncovered_requirements
    if uncovered:
        rows.append(
            view.catalog.plural(
                "document.warning.uncovered_requirements",
                len(uncovered),
                {"count": len(uncovered)},
            )
        )
    difference = quantize_hours(view.required_hours - view.assigned_hours)
    if difference != _ZERO:
        rows.append(
            view.catalog.text(
                "document.warning.hours_difference",
                {"hours": _fmt(difference)},
            )
        )
    return rows


# ── The four documents ───────────────────────────────────────────────────────


def _render_internal_draft(view: _SnapshotView) -> str:
    """Plan §15.1: the department's own working document."""
    lines = _header(
        view,
        "document.header.internal_draft",
        "document.header.internal_draft_banner",
    )
    lines.extend(_plan_lines(view))
    lines.extend(_section(view, "document.section.global_balance", _balance_rows(view)))
    lines.extend(
        _section(view, "document.section.teacher_balances", _teacher_balance_rows(view))
    )
    lines.extend(
        _section(
            view,
            "document.section.uncovered_requirements",
            _uncovered_rows(view),
        )
    )
    lines.extend(
        _section(
            view,
            "document.section.warnings_and_incidents",
            _warning_rows(view),
        )
    )
    return "\n".join(lines) + "\n"


def _render_school_leadership(view: _SnapshotView) -> str:
    """Plan §15.2: the copy that leaves the department."""
    lines = _header(
        view,
        "document.header.school_leadership",
        None,
    )
    lines.extend(
        [
            view.catalog.text("document.field.school", {"value": view.school_label}),
            view.catalog.text(
                "document.field.department", {"value": view.department_label}
            ),
            view.catalog.text(
                "document.field.academic_year",
                {"value": view.academic_year_label},
            ),
            _version_line(view),
        ]
    )
    lines.extend(_plan_lines(view))
    lines.extend(
        _section(
            view,
            "document.section.assignment_by_teacher",
            _assignments_by_teacher_rows(view),
        )
    )
    lines.extend(
        _section(
            view,
            "document.section.assignment_by_group",
            _assignments_by_group_rows(view),
        )
    )
    lines.extend(_section(view, "document.section.hours_summary", _balance_rows(view)))
    lines.extend(
        _section(
            view,
            "document.section.exceptions_and_justifications",
            _exception_rows(view),
        )
    )
    lines.extend(
        _section(
            view,
            "document.section.warnings_and_incidents",
            _warning_rows(view),
        )
    )
    return "\n".join(lines) + "\n"


def _render_teacher_summary(view: _SnapshotView) -> str:
    """The participant-facing recap: each teacher's own load, and nothing more.

    Deliberately carries neither the extra-hours justification nor another
    participant's balance — the confidentiality tier §20.25 calls
    department-head stays in the two documents above.
    """
    lines = _header(view, "document.header.teacher_summary", None)
    lines.append(_version_line(view))
    lines.extend(
        _section(
            view,
            "document.section.assignment_by_teacher",
            _assignments_by_teacher_rows(view),
        )
    )
    return "\n".join(lines) + "\n"


def _render_final(view: _SnapshotView) -> str:
    """Plan §15.3: the closing document, produced only from an accepted reparto."""
    lines = _header(view, "document.header.final", None)
    closed_at = view.process.get("closed_at") or view.catalog.text(
        "document.placeholder.not_recorded"
    )
    lines.extend(
        [
            view.catalog.text("document.field.school", {"value": view.school_label}),
            view.catalog.text(
                "document.field.department", {"value": view.department_label}
            ),
            view.catalog.text(
                "document.field.academic_year",
                {"value": view.academic_year_label},
            ),
            _version_line(view),
            view.catalog.text("document.field.closed_at", {"value": str(closed_at)}),
        ]
    )
    lines.extend(_plan_lines(view))
    lines.extend(
        _section(
            view,
            "document.section.final_assignment_list",
            _assignments_by_teacher_rows(view),
        )
    )
    lines.extend(_section(view, "document.section.final_summary", _balance_rows(view)))
    lines.extend(
        _section(
            view,
            "document.section.exceptions_and_justifications",
            _exception_rows(view),
        )
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "DOCUMENT_TRACE_ID_LINE_PREFIXES",
    "DocumentIdentityContext",
    "DocumentRenderingService",
]
