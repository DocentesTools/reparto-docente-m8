"""API tests for the AssignmentProcess lifecycle endpoints.

Covers the state machine (``POST /transition``, ``POST /reopen``) and
the copy-from-previous-year endpoint (``POST /copy-from/{source_id}``)
introduced for the Phase 1 state machine (plan §8.4, §10.2, §10.1).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    ActivityType,
    AssignmentProcessStatus,
    SubjectAllocationCategory,
    TeachingActivitySource,
)
from tests import factories


# ── Transition (plan §8.4) ────────────────────────────────────────────────────


def test_transition_draft_to_ready_for_meeting(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "ready_for_meeting"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready_for_meeting"
    assert resp.json()["closed_at"] is None


def test_transition_to_final_records_close_metadata(
    client: TestClient, session: Session, current_user
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.INTERNAL_REVISION
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "final", "reason": "approved by leadership"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "final"
    assert body["closed_at"] is not None
    assert body["closed_by_user_id"] == str(current_user.id)


def test_transition_draft_to_final_is_rejected(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "final"},
    )
    assert resp.status_code == 400
    assert "Illegal transition" in resp.json()["detail"]


def test_transition_final_to_reopen_must_use_reopen_endpoint(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "reopened"},
    )
    assert resp.status_code == 400
    assert "reopen" in resp.json()["detail"].lower()


def test_transition_self_loop_rejected(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "draft"},
    )
    assert resp.status_code == 400


def test_transition_returns_404_for_missing_process(
    client: TestClient,
) -> None:
    resp = client.post(
        f"/reparto/assignment-processes/{uuid.uuid4()}/transition",
        json={"target_status": "ready_for_meeting"},
    )
    assert resp.status_code == 404


def test_transition_blocks_reader(session: Session, reader_client: TestClient) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/transition",
        json={"target_status": "ready_for_meeting"},
    )
    assert resp.status_code == 403


def test_update_process_rejects_status_field(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.patch(
        f"/reparto/assignment-processes/{process.id}",
        json={"status": "ready_for_meeting"},
    )
    assert resp.status_code == 400
    assert "transition endpoint" in resp.json()["detail"]


# ── Reopen (plan §8.4) ──────────────────────────────────────────────────────


def test_reopen_final_to_reopened_clears_close_metadata(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    # Manually mark a close so the clear is observable.
    process.closed_at = datetime.now(tz=timezone.utc)
    process.closed_by_user_id = uuid.uuid4()
    session.add(process)
    session.commit()
    session.refresh(process)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/reopen",
        json={"reason": "leadership returned it"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "reopened"
    assert body["closed_at"] is None
    assert body["closed_by_user_id"] is None


def test_reopen_rejects_non_final_process(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/reopen",
        json={"reason": "too early"},
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


def test_reopen_requires_reason(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/reopen",
        json={},
    )
    assert resp.status_code == 422  # pydantic min_length=1


# ── Copy from previous year (plan §10.1) ─────────────────────────────────────


def _populate_source_process(
    session: Session,
    *,
    with_plan: bool = False,
) -> tuple[AssignmentProcess, TeacherProfile, TeacherProfile]:
    """Seed a FINAL source process with the three-stage configuration.

    Always creates two participants (one with an approved extra-hour block),
    a subject, a teaching group, a group-subject cell and an immutable
    leadership allocation revision. When ``with_plan`` is set it also creates a
    teaching plan carrying a live SECONDARY_MANUAL activity linked to the cell,
    a retired secondary activity and a MAIN_GENERATED activity — the latter two
    must never be copied as templates.
    """
    source = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    profile_a = factories.make_teacher_profile(session, display_name="Alice")
    profile_b = factories.make_teacher_profile(session, display_name="Bob")
    factories.make_process_teacher(
        session,
        source,
        profile_a,
        base_weekly_hours=18,
        extra_weekly_hours=3,
        selection_position=1,
    )
    factories.make_process_teacher(
        session, source, profile_b, base_weekly_hours=20, selection_position=2
    )
    subject = factories.make_subject(session, source, name="Mathematics")
    group = factories.make_teaching_group(
        session, source, stage="ESO", grade=1, group_code="A", label="1 ESO A"
    )
    factories.make_group_subject(
        session, source, group, subject, group_weekly_hours=4.0
    )
    # A leadership allocation revision that must never be re-activated on copy.
    factories.make_allocation_revision(
        session, source, allocated_group_weekly_hours=120.0
    )
    if with_plan:
        cell = session.exec(
            select(GroupSubject).where(GroupSubject.assignment_process_id == source.id)
        ).one()
        plan = factories.make_teaching_plan(session, source)
        factories.make_teaching_activity(
            session,
            plan,
            subject,
            allocation_category=SubjectAllocationCategory.SECONDARY,
            activity_type=ActivityType.CO_TEACHING,
            group_weekly_hours_per_group=2.0,
            teacher_weekly_hours_per_position=2.0,
            required_teacher_count=2,
            group_subjects=[cell],
        )
        # A retired secondary activity — excluded from the template copy.
        retired = factories.make_teaching_activity(session, plan, subject)
        retired.retired_at = datetime.now(tz=timezone.utc)
        session.add(retired)
        # A main-generated activity — re-materialised, never copied.
        factories.make_teaching_activity(
            session,
            plan,
            subject,
            source=TeachingActivitySource.MAIN_GENERATED,
        )
        session.commit()
    return source, profile_a, profile_b


def _make_target_in_same_school(
    session: Session,
    source: AssignmentProcess,
    *,
    status: AssignmentProcessStatus,
) -> AssignmentProcess:
    """Create a target process that shares school / year / department with
    ``source`` so copy-from tests do not trip the school-scope guard."""
    from reparto_service.db_models.academic_years import AcademicYear
    from reparto_service.db_models.departments import Department
    from reparto_service.db_models.schools import School

    academic_year = session.get(AcademicYear, source.academic_year_id)
    school = session.get(School, source.school_id)
    department = session.get(Department, source.department_id)
    if academic_year is None or school is None or department is None:
        # Source was built by the factory which always inserts the three
        # parents; the assertion is here to surface drift in the factory.
        raise AssertionError("source process is missing its parents")
    return factories.make_assignment_process(
        session,
        academic_year=academic_year,
        school=school,
        department=department,
        status=status,
    )


def test_copy_from_copies_configuration_only_by_default(
    client: TestClient, session: Session
) -> None:
    source, profile_a, profile_b = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["created_from_process_id"] == str(source.id)
    # Subject, group, group-subject cell and both teachers copied.
    resp = client.get(f"/reparto/assignment-processes/{target.id}/subjects/")
    assert resp.json()["count"] == 1
    resp = client.get(f"/reparto/assignment-processes/{target.id}/groups/")
    assert resp.json()["count"] == 1
    resp = client.get(f"/reparto/assignment-processes/{target.id}/group-subjects/")
    assert resp.json()["count"] == 1
    resp = client.get(f"/reparto/assignment-processes/{target.id}/teachers/")
    tbody = resp.json()
    assert tbody["count"] == 2
    assert {t["teacher_profile_id"] for t in tbody["data"]} == {
        str(profile_a.id),
        str(profile_b.id),
    }
    # Selection-order fields preserved from the source.
    positions = sorted(t["selection_position"] for t in tbody["data"])
    assert positions == [1, 2]
    # No teaching plan is created for a structure-only copy.
    assert (
        session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == target.id)
        ).first()
        is None
    )


def test_copy_from_preserves_base_hours_but_drops_extra_approvals(
    client: TestClient, session: Session
) -> None:
    source, profile_a, profile_b = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 200
    copied = {
        pt.teacher_profile_id: pt
        for pt in session.exec(
            select(ProcessTeacher).where(
                ProcessTeacher.assignment_process_id == target.id
            )
        ).all()
    }
    # Base hours carried; extra-hour approval dropped and audit pointer cleared.
    alice = copied[profile_a.id]
    assert alice.base_weekly_hours == 18
    assert alice.extra_weekly_hours == 0
    assert alice.target_weekly_hours == 18
    assert alice.extra_hours_reason is None
    assert alice.extra_hours_updated_by_user_id is None
    assert alice.extra_hours_updated_at is None
    assert copied[profile_b.id].base_weekly_hours == 20


def test_copy_from_does_not_activate_previous_allocation(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 200
    # The previous leadership allocation is never copied as a revision.
    assert (
        session.exec(
            select(DepartmentHourAllocationRevision).where(
                DepartmentHourAllocationRevision.assignment_process_id == target.id
            )
        ).first()
        is None
    )


def test_copy_from_preserves_subject_planning_fields(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    # A secondary co-teaching subject with non-default planning defaults.
    factories.make_subject(
        session,
        source,
        name="Co-teaching support",
        allocation_category=SubjectAllocationCategory.SECONDARY,
        activity_type=ActivityType.CO_TEACHING,
        default_group_weekly_hours=2.0,
        default_teacher_weekly_hours_per_position=2.0,
        default_required_teacher_count=2,
        allows_multiple_groups=True,
        allows_zero_groups=True,
    )
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 200
    copied = session.exec(
        select(Subject).where(
            Subject.assignment_process_id == target.id,
            Subject.name == "Co-teaching support",
        )
    ).one()
    assert copied.allocation_category == SubjectAllocationCategory.SECONDARY
    assert copied.activity_type == ActivityType.CO_TEACHING
    assert copied.default_group_weekly_hours == 2.0
    assert copied.default_teacher_weekly_hours_per_position == 2.0
    assert copied.default_required_teacher_count == 2
    assert copied.allows_multiple_groups is True
    assert copied.allows_zero_groups is True


def test_copy_from_preserves_group_subject_cell_values(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 200
    source_cell = session.exec(
        select(GroupSubject).where(GroupSubject.assignment_process_id == source.id)
    ).one()
    target_cell = session.exec(
        select(GroupSubject).where(GroupSubject.assignment_process_id == target.id)
    ).one()
    assert target_cell.group_weekly_hours == 4.0
    # The cell references the freshly-copied group and subject, not the source.
    assert target_cell.teaching_group_id != source_cell.teaching_group_id
    assert target_cell.subject_id != source_cell.subject_id


def test_copy_activities_copies_secondary_templates_into_fresh_plan(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session, with_plan=True)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": True},
    )
    assert resp.status_code == 200
    target_plan = session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == target.id)
    ).one()
    # Fresh draft plan: generation 0, no allocation, no lock.
    assert target_plan.status.value == "draft"
    assert target_plan.current_generation_number == 0
    assert target_plan.allocation_revision_id is None
    # Only the live SECONDARY_MANUAL activity is copied (main + retired skipped).
    activities = session.exec(
        select(TeachingActivity).where(
            TeachingActivity.teaching_plan_id == target_plan.id
        )
    ).all()
    assert len(activities) == 1
    copied = activities[0]
    assert copied.source == TeachingActivitySource.SECONDARY_MANUAL
    assert copied.required_teacher_count == 2
    assert copied.retired_at is None
    # Its group link was remapped onto the target's copied cell.
    target_cell = session.exec(
        select(GroupSubject).where(GroupSubject.assignment_process_id == target.id)
    ).one()
    link = session.exec(
        select(TeachingActivityGroup).where(
            TeachingActivityGroup.teaching_activity_id == copied.id
        )
    ).one()
    assert link.group_subject_id == target_cell.id


def test_copy_activities_noop_when_source_has_no_plan(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)  # no plan
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": True},
    )
    assert resp.status_code == 200
    # No source plan → no target plan is created.
    assert (
        session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == target.id)
        ).first()
        is None
    )


def test_copy_from_rejects_non_draft_target(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.READY_FOR_MEETING
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 400
    assert "draft" in resp.json()["detail"]


def test_copy_from_rejects_target_with_existing_subjects(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    factories.make_subject(session, target, name="Pre-existing")
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 400
    assert "subjects" in resp.json()["detail"]


def test_copy_from_rejects_target_with_existing_teachers(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    profile = factories.make_teacher_profile(session, display_name="Carl")
    factories.make_process_teacher(session, target, profile)
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 400
    assert "teachers" in resp.json()["detail"]


def test_copy_from_rejects_target_with_existing_groups(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    factories.make_teaching_group(session, target, group_code="Z")
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 400
    assert "teaching groups" in resp.json()["detail"]


def test_copy_from_rejects_target_with_existing_group_subjects(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    # A bare group-subject cell (no subject/group rows) so the earlier
    # emptiness checks pass and the group-subject branch is reached.
    session.add(
        GroupSubject(
            assignment_process_id=target.id,
            teaching_group_id=uuid.uuid4(),
            subject_id=uuid.uuid4(),
        )
    )
    session.commit()
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 400
    assert "group-subject" in resp.json()["detail"]


def test_copy_from_rejects_target_with_existing_plan(
    client: TestClient, session: Session
) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    factories.make_teaching_plan(session, target)
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 400
    assert "teaching plan" in resp.json()["detail"]


def test_copy_from_rejects_self_target(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/copy-from/{process.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 400


def test_copy_from_rejects_different_schools(
    client: TestClient, session: Session
) -> None:
    school_a = factories.make_school(session, name="A")
    school_b = factories.make_school(session, name="B")
    year = factories.make_academic_year(session)
    dept_a = factories.make_department(session, school_a)
    dept_b = factories.make_department(session, school_b)
    source = factories.make_assignment_process(
        session,
        academic_year=year,
        school=school_a,
        department=dept_a,
        status=AssignmentProcessStatus.FINAL,
    )
    target = factories.make_assignment_process(
        session,
        academic_year=year,
        school=school_b,
        department=dept_b,
        status=AssignmentProcessStatus.DRAFT,
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 400
    assert "school" in resp.json()["detail"].lower()


def test_copy_from_404_for_missing_source(client: TestClient, session: Session) -> None:
    target = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.DRAFT
    )
    resp = client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{uuid.uuid4()}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 404


def test_copy_from_blocks_reader(session: Session, reader_client: TestClient) -> None:
    source, _, _ = _populate_source_process(session)
    target = _make_target_in_same_school(
        session, source, status=AssignmentProcessStatus.DRAFT
    )
    resp = reader_client.post(
        f"/reparto/assignment-processes/{target.id}/copy-from/{source.id}",
        json={"copy_activities": False},
    )
    assert resp.status_code == 403


# ── Process mutability guard (plan §8.4) ──────────────────────────────────────


def test_cannot_add_teacher_to_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    profile = factories.make_teacher_profile(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/teachers/",
        json={
            "assignment_process_id": str(process.id),
            "teacher_profile_id": str(profile.id),
            "base_weekly_hours": 18,
        },
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


def test_cannot_add_subject_to_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/subjects/",
        json={"assignment_process_id": str(process.id), "name": "Math"},
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()


def test_cannot_add_group_to_final_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.FINAL
    )
    stage = factories.make_classroom_stage(session)
    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/groups/",
        json={
            "assignment_process_id": str(process.id),
            "classroom_stage_id": str(stage.id),
            "grade": 1,
            "group_code": "A",
            "label": "1 ESO A",
        },
    )
    assert resp.status_code == 400
    assert "final" in resp.json()["detail"].lower()
