"""C14 document-catalog golden and contract tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from reparto_service.controllers.history import HistoryController
from reparto_service.core.i18n import reset_current_locale, set_current_locale
from reparto_service.enums import (
    ExportArtifactFormat,
    ExportArtifactLocale,
    ExportArtifactType,
)
from reparto_service.services.document_catalog import (
    DOCUMENT_CATALOG_REVISION,
    load_document_catalog,
)
from reparto_service.services.document_rendering import (
    DocumentIdentityContext,
    DocumentRenderingService,
)
from reparto_service.services.stale_reasons import (
    MAIN_GENERATED_ACTIVITY_VALUES_CHANGED,
)


GOLDEN_ROOT = Path(__file__).parent / "golden_documents"

_PROCESS_ID = "00000000-0000-4000-8000-000000000001"
_TEACHER_ONE_ID = "00000000-0000-4000-8000-000000000011"
_TEACHER_TWO_ID = "00000000-0000-4000-8000-000000000012"
_PROFILE_ONE_ID = "00000000-0000-4000-8000-000000000021"
_PROFILE_TWO_ID = "00000000-0000-4000-8000-000000000022"
_SUBJECT_ONE_ID = "00000000-0000-4000-8000-000000000031"
_SUBJECT_TWO_ID = "00000000-0000-4000-8000-000000000032"
_GROUP_ID = "00000000-0000-4000-8000-000000000041"
_CELL_ONE_ID = "00000000-0000-4000-8000-000000000051"
_CELL_TWO_ID = "00000000-0000-4000-8000-000000000052"
_ACTIVITY_ONE_ID = "00000000-0000-4000-8000-000000000061"
_ACTIVITY_TWO_ID = "00000000-0000-4000-8000-000000000062"
_REQUIREMENT_ONE_ID = "00000000-0000-4000-8000-000000000071"
_REQUIREMENT_TWO_ID = "00000000-0000-4000-8000-000000000072"


def _snapshot() -> dict[str, object]:
    return {
        "process": {
            "id": _PROCESS_ID,
            "status": "ready_for_meeting",
            "updated_at": "2026-09-14T08:30:00+00:00",
            "closed_at": "2026-09-14T09:45:00+00:00",
        },
        "teaching_plan": {
            "status": "stale",
            "feasibility_status": "infeasible",
            "current_generation_number": 3,
            "stale_reason": MAIN_GENERATED_ACTIVITY_VALUES_CHANGED.message,
        },
        "allocation_revisions": [
            {
                "revision_number": 2,
                "allocated_group_weekly_hours": "4.00",
                "superseded_at": None,
            }
        ],
        "teachers": [
            {
                "id": _TEACHER_ONE_ID,
                "teacher_profile_id": _PROFILE_ONE_ID,
                "base_weekly_hours": "2.00",
                "extra_weekly_hours": "0.00",
                "extra_hours_reason": None,
            },
            {
                "id": _TEACHER_TWO_ID,
                "teacher_profile_id": _PROFILE_TWO_ID,
                "base_weekly_hours": "1.00",
                "extra_weekly_hours": "0.50",
                "extra_hours_reason": "Approved for the bilingual programme.",
            },
        ],
        "subjects": [
            {"id": _SUBJECT_ONE_ID, "name": "Mathematics"},
            {"id": _SUBJECT_TWO_ID, "name": "Physics"},
        ],
        "teaching_groups": [{"id": _GROUP_ID, "group_code": "2A"}],
        "group_subjects": [
            {
                "id": _CELL_ONE_ID,
                "teaching_group_id": _GROUP_ID,
            },
            {
                "id": _CELL_TWO_ID,
                "teaching_group_id": _GROUP_ID,
            },
        ],
        "teaching_activities": [
            {"id": _ACTIVITY_ONE_ID, "subject_id": _SUBJECT_ONE_ID},
            {"id": _ACTIVITY_TWO_ID, "subject_id": _SUBJECT_TWO_ID},
        ],
        "teaching_activity_groups": [
            {
                "teaching_activity_id": _ACTIVITY_ONE_ID,
                "group_subject_id": _CELL_ONE_ID,
            },
            {
                "teaching_activity_id": _ACTIVITY_TWO_ID,
                "group_subject_id": _CELL_TWO_ID,
            },
        ],
        "requirements": [
            {
                "id": _REQUIREMENT_ONE_ID,
                "teaching_activity_id": _ACTIVITY_ONE_ID,
                "position_index": 1,
                "required_teacher_hours": "2.00",
                "status": "assigned",
                "retired_generation": None,
                "superseded_by_requirement_id": None,
            },
            {
                "id": _REQUIREMENT_TWO_ID,
                "teaching_activity_id": _ACTIVITY_TWO_ID,
                "position_index": 2,
                "required_teacher_hours": "1.50",
                "status": "reconciliation_required",
                "retired_generation": None,
                "superseded_by_requirement_id": None,
            },
        ],
        "assignments": [
            {
                "id": "00000000-0000-4000-8000-000000000081",
                "hour_requirement_id": _REQUIREMENT_ONE_ID,
                "teaching_activity_id": _ACTIVITY_ONE_ID,
                "process_teacher_id": _TEACHER_ONE_ID,
                "source": "department_head",
                "status": "active",
            }
        ],
    }


def _identity() -> DocumentIdentityContext:
    return DocumentIdentityContext(
        school_name="IES Test",
        department_name="Science",
        academic_year_label="2026/2027",
        teacher_display_names_by_profile_id={
            _PROFILE_ONE_ID: "Alice Martin",
            _PROFILE_TWO_ID: "Bruno García",
        },
    )


def _versions() -> list[dict[str, object]]:
    return [{"version_number": 4, "status": "department_proposal"}]


@pytest.mark.parametrize("locale", list(ExportArtifactLocale))
@pytest.mark.parametrize(
    "export_type",
    [
        ExportArtifactType.INTERNAL_DRAFT,
        ExportArtifactType.SCHOOL_LEADERSHIP,
        ExportArtifactType.TEACHER_SUMMARY,
        ExportArtifactType.FINAL,
    ],
)
def test_document_matches_reviewable_golden_artifact(
    locale: ExportArtifactLocale,
    export_type: ExportArtifactType,
) -> None:
    catalog = load_document_catalog(locale)
    expected = (GOLDEN_ROOT / locale.value / f"{export_type.value}.txt").read_text(
        encoding="utf-8"
    )

    first = DocumentRenderingService.render(
        export_type,
        _snapshot(),
        _versions(),
        _identity(),
        catalog,
        MAIN_GENERATED_ACTIVITY_VALUES_CHANGED,
    )
    second = DocumentRenderingService.render(
        export_type,
        _snapshot(),
        _versions(),
        _identity(),
        catalog,
        MAIN_GENERATED_ACTIVITY_VALUES_CHANGED,
    )

    assert first == second == expected


@pytest.mark.parametrize(
    ("locale", "count", "expected"),
    [
        (ExportArtifactLocale.EN, 0, "  Ada — 0 assignments, 0.00 h"),
        (ExportArtifactLocale.EN, 1, "  Ada — 1 assignment, 1.00 h"),
        (ExportArtifactLocale.EN, 3, "  Ada — 3 assignments, 3.00 h"),
        (ExportArtifactLocale.ES, 0, "  Ada — 0 asignaciones, 0.00 h"),
        (ExportArtifactLocale.ES, 1, "  Ada — 1 asignación, 1.00 h"),
        (ExportArtifactLocale.ES, 3, "  Ada — 3 asignaciones, 3.00 h"),
        (ExportArtifactLocale.FR, 0, "  Ada — 0 affectation, 0.00 h"),
        (ExportArtifactLocale.FR, 1, "  Ada — 1 affectation, 1.00 h"),
        (ExportArtifactLocale.FR, 3, "  Ada — 3 affectations, 3.00 h"),
    ],
)
def test_document_catalog_uses_ngettext_for_zero_one_and_many(
    locale: ExportArtifactLocale,
    count: int,
    expected: str,
) -> None:
    assert (
        load_document_catalog(locale).plural(
            "document.assignment.count",
            count,
            {"teacher": "Ada", "count": count, "hours": f"{count}.00"},
        )
        == expected
    )


def test_document_catalog_is_injected_not_read_from_request_context() -> None:
    token = set_current_locale("fr")
    try:
        spanish = DocumentRenderingService.render(
            ExportArtifactType.INTERNAL_DRAFT,
            _snapshot(),
            _versions(),
            _identity(),
            load_document_catalog(ExportArtifactLocale.ES),
            MAIN_GENERATED_ACTIVITY_VALUES_CHANGED,
        )
    finally:
        reset_current_locale(token)

    assert "REPARTO — BORRADOR INTERNO" in spanish
    assert "RÉPARTITION — BROUILLON INTERNE" not in spanish


def test_document_catalog_refuses_mismatched_revision_and_locale_metadata() -> None:
    catalog = load_document_catalog(ExportArtifactLocale.EN)

    with pytest.raises(ValueError, match="Unsupported document catalog revision"):
        replace(catalog, revision="obsolete")
    with pytest.raises(ValueError, match="catalog locale and translator locale"):
        replace(catalog, locale=ExportArtifactLocale.ES)
    with pytest.raises(ValueError, match="catalog and translator revisions"):
        replace(
            catalog,
            _translator=replace(catalog._translator, revision="obsolete"),
            revision=DOCUMENT_CATALOG_REVISION,
        )


def test_historical_stale_reason_and_user_content_remain_verbatim() -> None:
    snapshot = _snapshot()
    historical_reason = "Motif historique saisi avant le catalogue."
    teaching_plan = snapshot["teaching_plan"]
    assert isinstance(teaching_plan, dict)
    teaching_plan["stale_reason"] = historical_reason

    spanish = DocumentRenderingService.render(
        ExportArtifactType.SCHOOL_LEADERSHIP,
        snapshot,
        _versions(),
        _identity(),
        load_document_catalog(ExportArtifactLocale.ES),
        None,
    )

    assert historical_reason in spanish
    assert "Approved for the bilingual programme." in spanish


def test_json_export_contract_keeps_raw_locked_status() -> None:
    snapshot = {"requirements": [], "teaching_plan": {"status": "locked"}}

    content = HistoryController._render_artifact(
        ExportArtifactFormat.JSON,
        ExportArtifactType.INTERNAL_DRAFT,
        snapshot,
        [],
        None,
        ExportArtifactLocale.FR,
        None,
    )

    assert content == '{"requirements":[],"teaching_plan":{"status":"locked"}}'
