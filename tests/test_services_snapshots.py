"""Unit tests for the three-stage snapshot/comparison service (plan §10.2/§10.3).

Cover :class:`~reparto_service.services.snapshots.SnapshotService` end to end: the
full snapshot sections (allocation revisions, teaching plan, dual balances,
assignment summary, subjects, group-subject matrix, activities with linked cells,
generated requirement slots and participant targets) and every plan §10.3
comparison dimension, including the "absent section" fallbacks a minimal snapshot
exercises.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from reparto_service.enums import (
    HourRequirementStatus,
    SubjectAllocationCategory,
    TeachingPlanStatus,
)
from reparto_service.services.snapshots import SnapshotService
from tests import factories


# ── build_snapshot ────────────────────────────────────────────────────────────


def _rich_process(session: Session):
    """A process exercising every snapshot section (plan §10.2)."""
    process = factories.make_assignment_process(session)
    factories.make_allocation_revision(
        session, process, allocated_group_weekly_hours=120.0
    )
    plan = factories.make_teaching_plan(
        session,
        process,
        status=TeachingPlanStatus.REQUIREMENTS_GENERATED,
        current_generation_number=1,
    )
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    activity = factories.make_teaching_activity(
        session,
        plan,
        subject,
        group_weekly_hours_per_group=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=1,
        group_subjects=[cell],
    )
    requirement = factories.make_hour_requirement(
        session,
        process,
        activity,
        required_teacher_hours=4.0,
        status=HourRequirementStatus.ASSIGNED,
    )
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=18.0, extra_weekly_hours=2.0
    )
    factories.make_assignment(session, process, requirement, teacher)
    return process, plan, cell, activity


def test_build_snapshot_full_sections(session: Session) -> None:
    process, plan, cell, activity = _rich_process(session)

    snapshot = SnapshotService.build_snapshot(session, process.id)

    assert snapshot["process"]["id"] == str(process.id)
    assert snapshot["current_allocation"] == "120.00"
    assert len(snapshot["allocation_revisions"]) == 1
    assert (
        snapshot["teaching_plan"]["status"] == TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    assert snapshot["plan_balance"]["group"]["total_group_load"] == "4.00"
    assert snapshot["plan_balance"]["teacher"]["total_teacher_load"] == "4.00"
    # Participant target total feeds the assignment summary (base 18 + extra 2).
    assert snapshot["assignment_summary"]["total_target_hours"] == "20.00"
    assert len(snapshot["subjects"]) == 1
    assert len(snapshot["group_subjects"]) == 1

    (activity_row,) = snapshot["teaching_activities"]
    assert activity_row["id"] == str(activity.id)
    assert activity_row["group_subject_ids"] == [str(cell.id)]
    assert activity_row["linked_group_count"] == 1
    assert activity_row["group_load"] == "4.00"
    assert activity_row["teacher_load"] == "4.00"

    (requirement_row,) = snapshot["requirements"]
    assert requirement_row["required_teacher_hours"] == "4.00"
    assert requirement_row["status"] == HourRequirementStatus.ASSIGNED.value

    (teacher_row,) = snapshot["teachers"]
    assert teacher_row["base_weekly_hours"] == 18.0
    assert teacher_row["extra_weekly_hours"] == 2.0
    assert teacher_row["target_weekly_hours"] == 20.0
    assert teacher_row["is_overloaded"] is True


def test_build_snapshot_without_plan(session: Session) -> None:
    process = factories.make_assignment_process(session)

    snapshot = SnapshotService.build_snapshot(session, process.id)

    assert snapshot["current_allocation"] is None
    assert snapshot["teaching_plan"] is None
    assert snapshot["plan_balance"] is None
    assert snapshot["teaching_activities"] == []
    assert snapshot["requirements"] == []
    assert snapshot["allocation_revisions"] == []
    assert snapshot["assignment_summary"]["total_target_hours"] == "0.00"


def test_build_snapshot_missing_process_404(session: Session) -> None:
    with pytest.raises(HTTPException) as exc:
        SnapshotService.build_snapshot(session, uuid.uuid4())

    assert exc.value.status_code == 404


def test_build_snapshot_excludes_retired_rows(session: Session) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    live_activity = factories.make_teaching_activity(session, plan, subject)
    retired_activity = factories.make_teaching_activity(session, plan, subject)
    retired_activity.retired_at = datetime.now(tz=timezone.utc)
    session.add(retired_activity)
    session.commit()
    factories.make_hour_requirement(session, process, live_activity, position_index=0)
    factories.make_hour_requirement(
        session,
        process,
        live_activity,
        position_index=1,
        retired_generation=1,
        status=HourRequirementStatus.STALE,
    )

    snapshot = SnapshotService.build_snapshot(session, process.id)

    assert [row["id"] for row in snapshot["teaching_activities"]] == [
        str(live_activity.id)
    ]
    assert len(snapshot["requirements"]) == 1
    assert snapshot["requirements"][0]["position_index"] == 0


def test_build_snapshot_activity_without_links(session: Session) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process, allows_zero_groups=True)
    factories.make_teaching_activity(
        session, plan, subject, group_weekly_hours_per_group=3.0
    )

    snapshot = SnapshotService.build_snapshot(session, process.id)

    (activity_row,) = snapshot["teaching_activities"]
    assert activity_row["group_subject_ids"] == []
    assert activity_row["linked_group_count"] == 0
    # No linked group → the group load is zero regardless of per-group hours.
    assert activity_row["group_load"] == "0.00"


# ── compare_snapshots (plan §10.3) ────────────────────────────────────────────


def _baseline_snapshot() -> dict[str, Any]:
    """A fully-populated snapshot dict for precise comparison control."""
    subject_id = str(uuid.uuid4())
    activity_id = str(uuid.uuid4())
    cell_id = str(uuid.uuid4())
    teacher_id = str(uuid.uuid4())
    requirement_id = str(uuid.uuid4())
    return {
        "current_allocation": "120.00",
        "allocation_revisions": [{"revision_number": 1}],
        "teaching_plan": {"current_generation_number": 1},
        "plan_balance": {
            "group": {"total_group_load": "116.00"},
            "teacher": {"total_teacher_load": "124.00"},
        },
        "assignment_summary": {"total_target_hours": "120.00"},
        "subjects": [{"id": subject_id, "allocation_category": "MAIN"}],
        "group_subjects": [{"id": cell_id}],
        "teaching_activities": [
            {
                "id": activity_id,
                "required_teacher_count": 2,
                "group_subject_ids": [cell_id],
            }
        ],
        "requirements": [{"id": requirement_id}],
        "teachers": [
            {
                "id": teacher_id,
                "base_weekly_hours": "18.00",
                "extra_weekly_hours": "0.00",
            }
        ],
    }


def _compare(left: dict[str, Any], right: dict[str, Any]):
    return SnapshotService.compare_snapshots(uuid.uuid4(), uuid.uuid4(), left, right)


def test_compare_identical_is_all_unchanged() -> None:
    snapshot = _baseline_snapshot()

    result = _compare(snapshot, dict(snapshot))

    assert result.changed_sections == []
    assert result.allocation_changed is False
    assert result.group_hours_changed is False
    assert result.teacher_load_changed is False
    assert result.subject_category_changed is False
    assert result.activity_added_or_removed is False
    assert result.group_link_added_or_removed is False
    assert result.teacher_position_count_changed is False
    assert result.participant_target_changed is False
    assert result.requirement_generation_changed is False
    assert result.allocation_delta == "0.00"
    assert result.group_load_delta == "0.00"
    assert result.teacher_load_delta == "0.00"
    assert result.participant_target_total_delta == "0.00"
    assert result.generation_number_delta == 0
    assert result.teacher_count_delta == 0
    assert result.activity_count_delta == 0
    assert result.requirement_count_delta == 0


def test_compare_allocation_value_change() -> None:
    left = _baseline_snapshot()
    right = _baseline_snapshot()
    right["current_allocation"] = "130.00"
    # keep the rest identical to isolate the allocation dimension
    right["allocation_revisions"] = left["allocation_revisions"]

    result = _compare(left, right)

    assert result.allocation_changed is True
    assert result.allocation_delta == "10.00"


def test_compare_allocation_from_none_has_no_delta() -> None:
    left = _baseline_snapshot()
    left["current_allocation"] = None
    right = _baseline_snapshot()

    result = _compare(left, right)

    assert result.allocation_changed is True
    assert result.allocation_delta is None


def test_compare_group_and_teacher_load_change() -> None:
    left = _baseline_snapshot()
    right = _baseline_snapshot()
    right["plan_balance"] = {
        "group": {"total_group_load": "118.00"},
        "teacher": {"total_teacher_load": "120.00"},
    }
    right["assignment_summary"] = {"total_target_hours": "121.50"}

    result = _compare(left, right)

    assert result.group_hours_changed is True
    assert result.teacher_load_changed is True
    assert result.group_load_delta == "2.00"
    assert result.teacher_load_delta == "-4.00"
    assert result.participant_target_total_delta == "1.50"


def test_compare_subject_category_change() -> None:
    left = _baseline_snapshot()
    right = _baseline_snapshot()
    right["subjects"] = [
        {"id": left["subjects"][0]["id"], "allocation_category": "SECONDARY"}
    ]

    result = _compare(left, right)

    assert result.subject_category_changed is True


def test_compare_activity_and_group_link_change() -> None:
    left = _baseline_snapshot()
    right = _baseline_snapshot()
    right["teaching_activities"] = [
        {
            "id": str(uuid.uuid4()),
            "required_teacher_count": 2,
            "group_subject_ids": [],
        }
    ]

    result = _compare(left, right)

    assert result.activity_added_or_removed is True
    assert result.group_link_added_or_removed is True
    assert result.activity_count_delta == 0


def test_compare_teacher_position_count_change() -> None:
    left = _baseline_snapshot()
    right = _baseline_snapshot()
    right["teaching_activities"] = [
        {
            "id": left["teaching_activities"][0]["id"],
            "required_teacher_count": 3,
            "group_subject_ids": left["teaching_activities"][0]["group_subject_ids"],
        }
    ]

    result = _compare(left, right)

    assert result.teacher_position_count_changed is True


def test_compare_participant_target_change() -> None:
    left = _baseline_snapshot()
    right = _baseline_snapshot()
    right["teachers"] = [
        {
            "id": left["teachers"][0]["id"],
            "base_weekly_hours": "18.00",
            "extra_weekly_hours": "4.00",
        }
    ]

    result = _compare(left, right)

    assert result.participant_target_changed is True


def test_compare_requirement_generation_change() -> None:
    left = _baseline_snapshot()
    right = _baseline_snapshot()
    right["teaching_plan"] = {"current_generation_number": 2}
    right["requirements"] = [{"id": str(uuid.uuid4())}, {"id": str(uuid.uuid4())}]

    result = _compare(left, right)

    assert result.requirement_generation_changed is True
    assert result.generation_number_delta == 1
    assert result.requirement_count_delta == 1


def test_compare_against_empty_snapshot_uses_fallbacks() -> None:
    """A minimal snapshot exercises every absent-section fallback branch."""
    left: dict[str, Any] = {}
    right = _baseline_snapshot()

    result = _compare(left, right)

    # No plan_balance / assignment_summary / teaching_plan on the left → zeros.
    assert result.group_load_delta == "116.00"
    assert result.teacher_load_delta == "124.00"
    assert result.participant_target_total_delta == "120.00"
    assert result.generation_number_delta == 1
    # Sections present only on the right count as added.
    assert result.activity_added_or_removed is True
    assert result.teacher_count_delta == 1
    assert result.requirement_count_delta == 1
    assert "teachers" in result.changed_sections


def test_compare_changed_sections_lists_differing_sections() -> None:
    left = _baseline_snapshot()
    right = deepcopy(left)
    right["teachers"] = [
        {
            "id": str(uuid.uuid4()),
            "base_weekly_hours": "10.00",
            "extra_weekly_hours": "0.00",
        }
    ]

    result = _compare(left, right)

    assert "teachers" in result.changed_sections
    assert "subjects" not in result.changed_sections
