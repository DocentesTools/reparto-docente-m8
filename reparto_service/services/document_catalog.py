"""Injected gettext catalog and vocabulary for deterministic documents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from reparto_service.core.errors import JsonValue
from reparto_service.core.i18n import TranslationCatalog, load_translation_catalog
from reparto_service.enums import (
    AssignmentProcessStatus,
    AssignmentSource,
    ExportArtifactLocale,
    FeasibilityStatus,
    HourRequirementStatus,
    TeachingPlanStatus,
)
from reparto_service.services.stale_reasons import SERVICE_STALE_REASONS


# Historical artifacts store bytes and are never re-rendered. Determinism for a
# new render is therefore scoped to this deployed renderer/catalog revision.
DOCUMENT_CATALOG_REVISION = "2026-09-14.c14.v1"

DOCUMENT_MESSAGE_DEFAULTS: Mapping[str, str] = MappingProxyType(
    {
        "document.balance.allocated_group_hours": "  Allocated group hours   {hours}",
        "document.balance.assigned_hours": "  Assigned hours          {hours}",
        "document.balance.required_slot_hours": "  Required slot hours     {hours}",
        "document.balance.uncovered_hours": "  Uncovered hours         {hours}",
        "document.exception.justification": "      justification: {reason}",
        "document.exception.no_reason_recorded": "(no reason recorded)",
        "document.exception.summary": (
            "  {teacher} — base {base_hours} h + extra {extra_hours} h"
        ),
        "document.field.academic_year": "Academic year:  {value}",
        "document.field.closed_at": "Closed at:      {value}",
        "document.field.department": "Department:     {value}",
        "document.field.feasibility": "Feasibility:    {value}",
        "document.field.generation": "Generation:     {value}",
        "document.field.plan_missing": (
            "Plan:           none — planning has not started"
        ),
        "document.field.plan_status": "Plan status:    {value}",
        "document.field.process_reference": "Process reference: {value}",
        "document.field.process_status": "Status:         {value}",
        "document.field.school": "School:         {value}",
        "document.field.state_as_of": "State as of:    {value} (UTC)",
        "document.field.version": "Version:        v{number} ({status})",
        "document.field.version_missing": "Version:        none captured",
        "document.group.assignment": ("      {activity}  {hours} h  →  {teacher}"),
        "document.group.heading": "  Group {code}",
        "document.header.final": "REPARTO — FINAL",
        "document.header.internal_draft": "REPARTO — INTERNAL DRAFT",
        "document.header.internal_draft_banner": (
            "DRAFT — internal working document, not for distribution."
        ),
        "document.header.school_leadership": ("REPARTO — SCHOOL LEADERSHIP COPY"),
        "document.header.teacher_summary": "REPARTO — TEACHER SUMMARY",
        "document.placeholder.academic_year_unavailable": (
            "(academic year unavailable)"
        ),
        "document.placeholder.department_unavailable": "(department unavailable)",
        "document.placeholder.not_recorded": "(not recorded)",
        "document.placeholder.process_teacher_unavailable": (
            "(process teacher unavailable)"
        ),
        "document.placeholder.school_unavailable": "(school unavailable)",
        "document.placeholder.subject_unavailable": "(subject unavailable)",
        "document.placeholder.teacher_profile_unavailable": (
            "(teacher profile unavailable)"
        ),
        "document.placeholder.teaching_activity_unavailable": (
            "(teaching activity unavailable)"
        ),
        "document.placeholder.unlinked": "(unlinked)",
        "document.requirement.uncovered": (
            "  {activity}  position {position}  {hours} h  [{status}]"
        ),
        "document.section.assignment_by_group": "ASSIGNMENT BY GROUP",
        "document.section.assignment_by_teacher": "ASSIGNMENT BY TEACHER",
        "document.section.empty": "  (none)",
        "document.section.exceptions_and_justifications": (
            "EXCEPTIONS AND JUSTIFICATIONS"
        ),
        "document.section.final_assignment_list": "FINAL ASSIGNMENT LIST",
        "document.section.final_summary": "FINAL SUMMARY",
        "document.section.global_balance": "GLOBAL BALANCE",
        "document.section.hours_summary": "HOURS SUMMARY",
        "document.section.teacher_balances": "TEACHER BALANCES",
        "document.section.uncovered_requirements": "UNCOVERED REQUIREMENTS",
        "document.section.warnings_and_incidents": "WARNINGS AND INCIDENTS",
        "document.teacher.assignment_detail": "      {activity}  {hours} h  [{source}]",
        "document.teacher.balance": (
            "  {teacher}\n      target {target}  assigned {assigned}  "
            "difference {difference}{flags}"
        ),
        "document.teacher.overload_authorized": " [OVERLOAD AUTHORIZED]",
        "document.warning.feasibility_not_validated": (
            "  Feasibility is {status} — this document does not describe a "
            "validated plan."
        ),
        "document.warning.hours_difference": (
            "  Required and assigned hours differ by {hours} h."
        ),
        "document.warning.no_teaching_plan": (
            "  No teaching plan exists for this process yet."
        ),
        "document.warning.plan_stale": "  Plan is stale: {reason}",
        **{reason.code: reason.message for reason in SERVICE_STALE_REASONS},
    }
)

DOCUMENT_PLURAL_DEFAULTS: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "document.assignment.count": (
            "  {teacher} — {count} assignment, {hours} h",
            "  {teacher} — {count} assignments, {hours} h",
        ),
        "document.warning.uncovered_requirements": (
            "  {count} requirement slot is still uncovered.",
            "  {count} requirement slots are still uncovered.",
        ),
    }
)

_DOCUMENT_ENUM_TYPES = (
    AssignmentProcessStatus,
    AssignmentSource,
    FeasibilityStatus,
    HourRequirementStatus,
    TeachingPlanStatus,
)
DOCUMENT_ENUM_VALUES = frozenset(
    member.value for enum_type in _DOCUMENT_ENUM_TYPES for member in enum_type
)
DOCUMENT_ENUM_DEFAULTS: Mapping[str, str] = MappingProxyType(
    {
        f"document.enum.{value}": value.replace("_", " ").upper()
        for value in sorted(DOCUMENT_ENUM_VALUES)
    }
)

DOCUMENT_CATALOG_CODES = frozenset(
    (*DOCUMENT_MESSAGE_DEFAULTS, *DOCUMENT_PLURAL_DEFAULTS, *DOCUMENT_ENUM_DEFAULTS)
)

DOCUMENT_TRACE_ID_LINE_PREFIXES = frozenset(
    {
        "Process reference:",
        "Referencia del proceso:",
        "Référence du processus :",
    }
)


@dataclass(frozen=True)
class DocumentCatalog:
    """The explicit locale, revision, vocabulary, and gettext translator."""

    locale: ExportArtifactLocale
    revision: str
    _translator: TranslationCatalog

    def __post_init__(self) -> None:
        if self.revision != DOCUMENT_CATALOG_REVISION:
            raise ValueError(
                f"Unsupported document catalog revision: {self.revision!r}"
            )
        if self._translator.locale != self.locale.value:
            raise ValueError("Document catalog locale and translator locale differ")
        if self._translator.revision != self.revision:
            raise ValueError("Document catalog and translator revisions differ")

    def text(
        self,
        code: str,
        params: Mapping[str, JsonValue] | None = None,
    ) -> str:
        """Translate one declared document message."""
        default = DOCUMENT_MESSAGE_DEFAULTS[code]
        return self._translator.gettext(code, default, params or {})

    def plural(
        self,
        code: str,
        count: int,
        params: Mapping[str, JsonValue],
    ) -> str:
        """Translate one declared plural through the locale's plural rule."""
        singular, plural = DOCUMENT_PLURAL_DEFAULTS[code]
        return self._translator.ngettext(
            code,
            f"{code}.plural",
            singular,
            plural,
            count,
            params,
        )

    def enum_label(self, value: object) -> str:
        """Translate a known wire enum without changing the wire value."""
        raw_value = str(getattr(value, "value", value)).lower()
        code = f"document.enum.{raw_value}"
        default = DOCUMENT_ENUM_DEFAULTS.get(code, raw_value.replace("_", " ").upper())
        return self._translator.gettext(code, default, {})

    def stored_reason(
        self,
        code: str,
        default: str,
        params: Mapping[str, JsonValue],
    ) -> str:
        """Translate structured rows, retaining their stored fallback on drift."""
        return self._translator.gettext(code, default, params)


def load_document_catalog(
    locale: ExportArtifactLocale,
    *,
    localedir: Path | None = None,
) -> DocumentCatalog:
    """Load the explicitly requested catalog outside the pure renderer."""
    translator = load_translation_catalog(
        locale.value,
        revision=DOCUMENT_CATALOG_REVISION,
        localedir=localedir,
    )
    return DocumentCatalog(locale, DOCUMENT_CATALOG_REVISION, translator)


__all__ = [
    "DOCUMENT_CATALOG_CODES",
    "DOCUMENT_CATALOG_REVISION",
    "DOCUMENT_ENUM_DEFAULTS",
    "DOCUMENT_MESSAGE_DEFAULTS",
    "DOCUMENT_PLURAL_DEFAULTS",
    "DOCUMENT_TRACE_ID_LINE_PREFIXES",
    "DocumentCatalog",
    "load_document_catalog",
]
