"""GroupSubject -> MAIN_GENERATED sync flow tests (plan §20.10)."""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from auth_sdk_m8.schemas.user import UserModel
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.controllers.group_subjects import GroupSubjectController
from reparto_service.db_models.audit_events import AuditEvent
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.teaching_activities import (
    MainActivityAssignmentImpact,
    TeachingActivity,
)
from reparto_service.enums import (
    AssignmentStatus,
    HourRequirementStatus,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingActivitySyncState,
    TeachingPlanStatus,
)
from reparto_service.services.validations import (
    CODE_ACTIVITY_OUT_OF_SYNC,
    PlanValidationService,
)
from tests import factories


def _preview_url(process_id, cell_id) -> str:
    return (
        f"/reparto/assignment-processes/{process_id}/group-subjects/"
        f"{cell_id}/sync-preview"
    )


def _apply_url(process_id, cell_id) -> str:
    return (
        f"/reparto/assignment-processes/{process_id}/group-subjects/"
        f"{cell_id}/sync-apply"
    )


def _cell_url(process_id, cell_id) -> str:
    return f"/reparto/assignment-processes/{process_id}/group-subjects/{cell_id}"


def _setup(
    session: Session, *, plan_status: TeachingPlanStatus = TeachingPlanStatus.DRAFT
):
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process, status=plan_status)
    subject = factories.make_subject(
        session,
        process,
        allocation_category=SubjectAllocationCategory.MAIN,
        default_group_weekly_hours=4.0,
        default_teacher_weekly_hours_per_position=4.0,
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(
        session,
        process,
        group,
        subject,
        group_weekly_hours=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=2,
    )
    activity = factories.make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        source=TeachingActivitySource.MAIN_GENERATED,
        source_group_subject_id=cell.id,
        group_weekly_hours_per_group=4.0,
        teacher_weekly_hours_per_position=4.0,
        required_teacher_count=2,
        group_subjects=[cell],
    )
    return process, plan, subject, cell, activity


def _assign_first_slot(session: Session, process, activity):
    requirement = factories.make_hour_requirement(
        session,
        process,
        activity,
        position_index=0,
        required_teacher_hours=4.0,
        status=HourRequirementStatus.ASSIGNED,
    )
    profile = factories.make_teacher_profile(session)
    teacher = factories.make_process_teacher(
        session, process, profile, base_weekly_hours=4.0
    )
    factories.make_assignment(
        session,
        process,
        requirement,
        teacher,
        status=AssignmentStatus.ACTIVE,
    )
    return requirement


def test_source_edit_marks_out_of_sync_without_overwriting_activity(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, cell, activity = _setup(session)

    response = client.patch(
        _cell_url(process.id, cell.id),
        json={
            "group_weekly_hours": 5.0,
            "teacher_weekly_hours_per_position": 6.0,
            "required_teacher_count": 3,
        },
    )
    assert response.status_code == 200
    session.refresh(activity)
    assert activity.sync_state == TeachingActivitySyncState.OUT_OF_SYNC
    assert activity.group_weekly_hours_per_group == 4.0
    assert activity.teacher_weekly_hours_per_position == 4.0
    assert activity.required_teacher_count == 2

    report = PlanValidationService.compute_plan_validations(session, plan)
    assert CODE_ACTIVITY_OUT_OF_SYNC in {message.code for message in report.messages}
    events = session.exec(
        select(AuditEvent).where(
            AuditEvent.event_type == "teaching_activity.out_of_sync"
        )
    ).all()
    assert [event.entity_id for event in events] == [activity.id]


def test_preview_and_apply_show_source_current_diff_and_update_activity(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, cell, activity = _setup(session)
    client.patch(
        _cell_url(process.id, cell.id),
        json={
            "group_weekly_hours": 5.0,
            "teacher_weekly_hours_per_position": 6.0,
            "required_teacher_count": 3,
        },
    )

    preview_response = client.post(_preview_url(process.id, cell.id))
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["source_values"] == {
        "group_weekly_hours_per_group": 5.0,
        "teacher_weekly_hours_per_position": 6.0,
        "required_teacher_count": 3,
    }
    assert preview["current_values"] == {
        "group_weekly_hours_per_group": 4.0,
        "teacher_weekly_hours_per_position": 4.0,
        "required_teacher_count": 2,
    }
    assert [difference["field"] for difference in preview["differences"]] == [
        "group_weekly_hours_per_group",
        "teacher_weekly_hours_per_position",
        "required_teacher_count",
    ]
    assert preview["assignment_impact"]["affected_assignment_count"] == 0
    assert len(preview["preview_fingerprint"]) == 64

    result_response = client.post(
        _apply_url(process.id, cell.id),
        json={"expected_preview_fingerprint": preview["preview_fingerprint"]},
    )
    assert result_response.status_code == 200
    result = result_response.json()
    assert result["activity"]["sync_state"] == "in_sync"
    assert result["activity"]["group_weekly_hours_per_group"] == 5.0
    assert result["activity"]["teacher_weekly_hours_per_position"] == 6.0
    assert result["activity"]["required_teacher_count"] == 3
    assert result["teaching_plan_status"] == TeachingPlanStatus.UNBALANCED.value
    assert result["was_noop"] is False
    session.refresh(activity)
    session.refresh(plan)
    assert activity.sync_state == TeachingActivitySyncState.IN_SYNC
    assert plan.status == TeachingPlanStatus.UNBALANCED


def test_assigned_value_change_routes_plan_and_slot_to_reconciliation(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, cell, activity = _setup(
        session, plan_status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    requirement = _assign_first_slot(session, process, activity)

    response = client.patch(
        _cell_url(process.id, cell.id),
        json={"teacher_weekly_hours_per_position": 5.0},
    )
    assert response.status_code == 200
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.RECONCILIATION_REQUIRED

    preview = client.post(_preview_url(process.id, cell.id)).json()
    assert preview["assignment_impact"] == {
        "active_assignment_count": 1,
        "affected_assignment_count": 1,
        "affected_requirement_ids": [str(requirement.id)],
        "requires_reconciliation": True,
    }
    result = client.post(
        _apply_url(process.id, cell.id),
        json={"expected_preview_fingerprint": preview["preview_fingerprint"]},
    )
    assert result.status_code == 200
    session.refresh(requirement)
    session.refresh(plan)
    assert requirement.status == HourRequirementStatus.RECONCILIATION_REQUIRED
    assert plan.status == TeachingPlanStatus.RECONCILIATION_REQUIRED


def test_group_only_change_with_assignment_requires_regeneration_not_reconciliation(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, cell, activity = _setup(
        session, plan_status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    requirement = _assign_first_slot(session, process, activity)

    response = client.patch(
        _cell_url(process.id, cell.id), json={"group_weekly_hours": 5.0}
    )
    assert response.status_code == 200
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.STALE
    preview = client.post(_preview_url(process.id, cell.id)).json()
    assert preview["assignment_impact"]["active_assignment_count"] == 1
    assert preview["assignment_impact"]["affected_assignment_count"] == 0
    assert preview["assignment_impact"]["requires_reconciliation"] is False
    session.refresh(requirement)
    assert requirement.status == HourRequirementStatus.ASSIGNED


def test_apply_rejects_stale_preview(client: TestClient, session: Session) -> None:
    process, _plan, _subject, cell, _activity = _setup(session)
    client.patch(_cell_url(process.id, cell.id), json={"group_weekly_hours": 5.0})
    preview = client.post(_preview_url(process.id, cell.id)).json()
    client.patch(_cell_url(process.id, cell.id), json={"group_weekly_hours": 6.0})

    response = client.post(
        _apply_url(process.id, cell.id),
        json={"expected_preview_fingerprint": preview["preview_fingerprint"]},
    )
    assert response.status_code == 409
    assert "changed since preview" in response.json()["detail"]


def test_inactive_source_requires_guarded_retirement(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, cell, activity = _setup(
        session, plan_status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    requirement = _assign_first_slot(session, process, activity)
    assert (
        client.patch(_cell_url(process.id, cell.id), json={"active": False}).status_code
        == 409
    )

    activity_path = (
        f"/reparto/assignment-processes/{process.id}/teaching-activities/"
        f"{activity.id}/retire"
    )
    assert client.post(activity_path).status_code == 200
    session.refresh(activity)
    session.refresh(requirement)
    session.refresh(plan)
    assert activity.retired_at is not None
    assert requirement.status == HourRequirementStatus.RECONCILIATION_REQUIRED
    assert plan.status == TeachingPlanStatus.RECONCILIATION_REQUIRED

    assert client.post(f"{_cell_url(process.id, cell.id)}/retire").status_code == 200
    session.refresh(cell)
    assert cell.active is False


def test_sync_apply_rejects_legacy_inactive_source(
    client: TestClient, session: Session
) -> None:
    process, _plan, _subject, cell, activity = _setup(session)
    requirement = _assign_first_slot(session, process, activity)
    cell.active = False
    session.add(cell)
    session.commit()

    preview = client.post(_preview_url(process.id, cell.id)).json()
    assert preview["retirement_required"] is True
    assert preview["assignment_impact"]["affected_requirement_ids"] == [
        str(requirement.id)
    ]
    response = client.post(
        _apply_url(process.id, cell.id),
        json={"expected_preview_fingerprint": preview["preview_fingerprint"]},
    )
    assert response.status_code == 409
    assert "guarded activity-retirement" in response.json()["detail"]


def test_noop_apply_is_idempotent_and_keeps_plan_state(
    client: TestClient, session: Session
) -> None:
    process, plan, _subject, cell, _activity = _setup(session)
    preview = client.post(_preview_url(process.id, cell.id)).json()
    assert preview["is_noop"] is True

    response = client.post(
        _apply_url(process.id, cell.id),
        json={"expected_preview_fingerprint": preview["preview_fingerprint"]},
    )
    assert response.status_code == 200
    assert response.json()["was_noop"] is True
    session.refresh(plan)
    assert plan.status == TeachingPlanStatus.DRAFT
    events = session.exec(
        select(AuditEvent).where(
            AuditEvent.event_type == "teaching_activity.sync_applied"
        )
    ).all()
    assert events == []


def test_same_effective_source_value_does_not_mark_out_of_sync(
    client: TestClient, session: Session
) -> None:
    process, _plan, _subject, cell, activity = _setup(session)
    cell.group_weekly_hours = None
    session.add(cell)
    session.commit()
    session.refresh(cell)

    response = client.patch(
        _cell_url(process.id, cell.id), json={"group_weekly_hours": 4.0}
    )
    assert response.status_code == 200
    session.refresh(activity)
    assert activity.sync_state == TeachingActivitySyncState.IN_SYNC


def test_sync_requires_materialized_activity(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session, process, allocation_category=SubjectAllocationCategory.MAIN
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)

    assert (
        client.patch(
            _cell_url(process.id, cell.id), json={"group_weekly_hours": 2.0}
        ).status_code
        == 200
    )
    assert client.post(_preview_url(process.id, cell.id)).status_code == 409


def test_sync_requires_writer(
    reader_client: TestClient, session: Session, reader: UserModel
) -> None:
    process, _plan, _subject, cell, _activity = _setup(session)
    factories.enrol(session, process, reader)
    assert reader_client.post(_preview_url(process.id, cell.id)).status_code == 403


def test_bulk_update_marks_materialized_activity_out_of_sync(
    client: TestClient, session: Session
) -> None:
    process, _plan, _subject, _cell, activity = _setup(session)
    preview_payload = {
        "subject_id": str(activity.subject_id),
        "mode": "update_existing",
        "group_weekly_hours": 7.0,
    }
    bulk_preview = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/bulk-preview",
        json=preview_payload,
    ).json()
    response = client.post(
        f"/reparto/assignment-processes/{process.id}/group-subjects/bulk-apply",
        json={
            **preview_payload,
            "expected_affected_count": bulk_preview["expected_affected_count"],
        },
    )
    assert response.status_code == 200
    session.refresh(activity)
    assert activity.sync_state == TeachingActivitySyncState.OUT_OF_SYNC


def test_apply_marks_only_affected_assigned_requirements(
    client: TestClient, session: Session
) -> None:
    process, _plan, _subject, cell, activity = _setup(
        session, plan_status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    assigned = _assign_first_slot(session, process, activity)
    unassigned = factories.make_hour_requirement(
        session,
        process,
        activity,
        position_index=1,
        required_teacher_hours=4.0,
        status=HourRequirementStatus.AVAILABLE,
    )
    client.patch(
        _cell_url(process.id, cell.id),
        json={"teacher_weekly_hours_per_position": 5.0},
    )
    preview = client.post(_preview_url(process.id, cell.id)).json()
    client.post(
        _apply_url(process.id, cell.id),
        json={"expected_preview_fingerprint": preview["preview_fingerprint"]},
    )
    rows = {row.id: row.status for row in session.exec(select(HourRequirement)).all()}
    assert rows[assigned.id] == HourRequirementStatus.RECONCILIATION_REQUIRED
    assert rows[unassigned.id] == HourRequirementStatus.AVAILABLE


def test_sync_resolves_subject_defaults_and_zero_fallbacks(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(
        session,
        process,
        allocation_category=SubjectAllocationCategory.MAIN,
        default_group_weekly_hours=None,
        default_teacher_weekly_hours_per_position=None,
    )
    group = factories.make_teaching_group(session, process)
    cell = factories.make_group_subject(session, process, group, subject)
    factories.make_teaching_activity(
        session,
        plan,
        subject,
        allocation_category=SubjectAllocationCategory.MAIN,
        source=TeachingActivitySource.MAIN_GENERATED,
        source_group_subject_id=cell.id,
        group_weekly_hours_per_group=0.0,
        teacher_weekly_hours_per_position=0.0,
        group_subjects=[cell],
    )

    preview = client.post(_preview_url(process.id, cell.id)).json()
    assert preview["source_values"]["group_weekly_hours_per_group"] == 0.0
    assert preview["source_values"]["teacher_weekly_hours_per_position"] == 0.0
    subject.default_group_weekly_hours = 2.0
    subject.default_teacher_weekly_hours_per_position = 3.0
    session.add(subject)
    session.commit()
    preview = client.post(_preview_url(process.id, cell.id)).json()
    assert preview["source_values"]["group_weekly_hours_per_group"] == 2.0
    assert preview["source_values"]["teacher_weekly_hours_per_position"] == 3.0


def test_apply_detects_activity_removed_after_preview(
    client: TestClient, session: Session
) -> None:
    process, _plan, _subject, cell, activity = _setup(session)
    activity.retired_at = activity.updated_at
    session.add(activity)
    session.commit()

    response = client.post(
        _apply_url(process.id, cell.id),
        json={"expected_preview_fingerprint": "0" * 64},
    )
    assert response.status_code == 409
    assert "no live MAIN_GENERATED" in response.json()["detail"]


def test_out_of_sync_invalidation_covers_balanced_locked_and_missing_plans(
    client: TestClient, session: Session
) -> None:
    process, balanced, _subject, cell, _activity = _setup(
        session, plan_status=TeachingPlanStatus.BALANCED
    )
    assert (
        client.patch(
            _cell_url(process.id, cell.id), json={"group_weekly_hours": 5.0}
        ).status_code
        == 200
    )
    session.refresh(balanced)
    assert balanced.status == TeachingPlanStatus.UNBALANCED

    process2, locked, _subject2, cell2, _activity2 = _setup(
        session, plan_status=TeachingPlanStatus.LOCKED
    )
    assert (
        client.patch(
            _cell_url(process2.id, cell2.id), json={"group_weekly_hours": 5.0}
        ).status_code
        == 200
    )
    session.refresh(locked)
    assert locked.status == TeachingPlanStatus.STALE

    process_without_plan = factories.make_assignment_process(session)
    GroupSubjectController._invalidate_plan_for_out_of_sync(
        session,
        process_without_plan.id,
        requires_reconciliation=False,
    )


def test_plan_sync_lifecycle_helper_covers_existing_operational_states(
    session: Session,
) -> None:
    empty_impact = MainActivityAssignmentImpact(
        active_assignment_count=0,
        affected_assignment_count=0,
        affected_requirement_ids=[],
        requires_reconciliation=False,
    )
    process, unbalanced, _subject, _cell, _activity = _setup(
        session, plan_status=TeachingPlanStatus.UNBALANCED
    )
    GroupSubjectController._advance_plan_after_sync(session, unbalanced, empty_impact)
    assert unbalanced.status == TeachingPlanStatus.UNBALANCED

    _process2, locked, _subject2, _cell2, _activity2 = _setup(
        session, plan_status=TeachingPlanStatus.LOCKED
    )
    GroupSubjectController._advance_plan_after_sync(session, locked, empty_impact)
    assert locked.status == TeachingPlanStatus.STALE

    _process3, generated, _subject3, _cell3, _activity3 = _setup(
        session, plan_status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    GroupSubjectController._advance_plan_after_sync(session, generated, empty_impact)
    assert generated.status == TeachingPlanStatus.STALE

    reconciling_impact = empty_impact.model_copy(
        update={"requires_reconciliation": True}
    )
    _process4, generated2, _subject4, _cell4, _activity4 = _setup(
        session, plan_status=TeachingPlanStatus.REQUIREMENTS_GENERATED
    )
    GroupSubjectController._advance_plan_after_sync(
        session, generated2, reconciling_impact
    )
    assert generated2.status == TeachingPlanStatus.RECONCILIATION_REQUIRED

    orphan = TeachingActivity(
        teaching_plan_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        group_weekly_hours_per_group=0.0,
        teacher_weekly_hours_per_position=0.0,
    )
    with pytest.raises(HTTPException, match="no owning teaching plan"):
        GroupSubjectController._plan_for_activity(session, process.id, orphan)
