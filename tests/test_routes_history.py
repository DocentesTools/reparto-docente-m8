"""API tests for export artifacts, backup and restore (plan §10.4).

Exercises :class:`~reparto_service.controllers.history.HistoryController`: the
export-artifact rendering (JSON/CSV/PDF + final-export blocking), the complete
three-stage backup snapshot, and the restore-into-empty-draft round trip with its
id remapping, mode-gated requirement/assignment restore, plan feasibility reset
and generation/reconciliation consistency validation.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_activities import TeachingActivity
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AssignmentProcessStatus,
    AssignmentStatus,
    FeasibilityStatus,
    HourRequirementStatus,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingPlanStatus,
)
from tests import factories


# ── Counting helpers ─────────────────────────────────────────────────────────


def _count(session: Session, model: Any, process_id: uuid.UUID) -> int:
    rows = session.exec(
        select(model).where(model.assignment_process_id == process_id)
    ).all()
    return len(rows)


def _plan(session: Session, process_id: uuid.UUID) -> TeachingPlan | None:
    return session.exec(
        select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
    ).first()


# ── Scenario builders ────────────────────────────────────────────────────────


def _full_source(session: Session) -> Any:
    """A source process exercising every backup section (plan §10.2, §10.4).

    Two allocation revisions (one superseded), a REQUIREMENTS_GENERATED plan at
    generation 2 with FEASIBLE feasibility, a main and secondary subject/cell, a
    main-generated activity linked to its cell, a live secondary activity, a
    retired secondary activity, a reconciled slot pair (retired-and-superseded +
    its live replacement) with a cancelled and an active assignment, and a spare
    unassigned secondary slot.
    """
    process = factories.make_assignment_process(session)
    factories.make_allocation_revision(
        session,
        process,
        revision_number=1,
        allocated_group_weekly_hours=100.0,
        superseded_at=datetime.now(tz=timezone.utc),
    )
    revision_current = factories.make_allocation_revision(
        session, process, revision_number=2, allocated_group_weekly_hours=120.0
    )
    plan = factories.make_teaching_plan(
        session,
        process,
        status=TeachingPlanStatus.REQUIREMENTS_GENERATED,
        current_generation_number=2,
        feasibility_status=FeasibilityStatus.FEASIBLE,
    )
    plan.allocation_revision_id = revision_current.id
    session.add(plan)
    session.commit()

    subject_main = factories.make_subject(
        session,
        process,
        name="Main",
        allocation_category=SubjectAllocationCategory.MAIN,
    )
    subject_sec = factories.make_subject(
        session,
        process,
        name="Secondary",
        allocation_category=SubjectAllocationCategory.SECONDARY,
    )
    group = factories.make_teaching_group(session, process)
    cell_main = factories.make_group_subject(session, process, group, subject_main)
    cell_sec = factories.make_group_subject(session, process, group, subject_sec)
    activity_main = factories.make_teaching_activity(
        session,
        plan,
        subject_main,
        allocation_category=SubjectAllocationCategory.MAIN,
        source=TeachingActivitySource.MAIN_GENERATED,
        source_group_subject_id=cell_main.id,
        group_subjects=[cell_main],
    )
    activity_sec = factories.make_teaching_activity(
        session, plan, subject_sec, group_subjects=[cell_sec]
    )
    retired_activity = factories.make_teaching_activity(session, plan, subject_sec)
    retired_activity.retired_at = datetime.now(tz=timezone.utc)
    session.add(retired_activity)
    session.commit()

    # Reconciled slot pair on the main activity, position 0.
    slot_new = factories.make_hour_requirement(
        session,
        process,
        activity_main,
        position_index=0,
        created_generation=2,
        last_validated_generation=2,
        status=HourRequirementStatus.ASSIGNED,
    )
    slot_old = factories.make_hour_requirement(
        session,
        process,
        activity_main,
        position_index=0,
        created_generation=1,
        last_validated_generation=1,
        retired_generation=2,
        superseded_by_requirement_id=slot_new.id,
        status=HourRequirementStatus.STALE,
    )
    slot_sec = factories.make_hour_requirement(
        session,
        process,
        activity_sec,
        position_index=0,
        created_generation=1,
        last_validated_generation=2,
        status=HourRequirementStatus.AVAILABLE,
    )
    profile_a = factories.make_teacher_profile(session, display_name="Ana")
    profile_b = factories.make_teacher_profile(session, display_name="Beto")
    teacher_a = factories.make_process_teacher(
        session, process, profile_a, base_weekly_hours=18.0, extra_weekly_hours=2.0
    )
    factories.make_process_teacher(session, process, profile_b, base_weekly_hours=20.0)
    factories.make_assignment(
        session, process, slot_new, teacher_a, status=AssignmentStatus.ACTIVE
    )
    # The released assignment of the reconciled (retired) slot, kept for audit.
    factories.make_assignment(
        session, process, slot_old, teacher_a, status=AssignmentStatus.CANCELLED
    )
    return process, revision_current, slot_new, slot_sec


def _config_only_source(session: Session) -> Any:
    """A source with configuration only — no allocation, plan or activities."""
    process = factories.make_assignment_process(session)
    factories.make_subject(session, process)
    factories.make_teaching_group(session, process)
    profile = factories.make_teacher_profile(session)
    factories.make_process_teacher(session, process, profile)
    return process


def _backup_content(client: TestClient, process_id: uuid.UUID) -> str:
    resp = client.post(
        f"/reparto/assignment-processes/{process_id}/exports",
        json={"export_type": "backup", "format": "json"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["content"]


def _restore(
    client: TestClient,
    target_id: uuid.UUID,
    content: str,
    *,
    restore_assignments: bool = True,
) -> Any:
    return client.post(
        f"/reparto/assignment-processes/{target_id}/restore-draft",
        json={"content": content, "restore_assignments": restore_assignments},
    )


# ── Export artifacts ─────────────────────────────────────────────────────────


def test_create_json_backup_artifact(client: TestClient, session: Session) -> None:
    process, *_ = _full_source(session)
    client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "backup baseline"},
    )

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "backup", "format": "json"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["checksum"]
    assert body["file_path"].endswith(".json")
    snapshot = json.loads(body["content"])
    assert snapshot["process"]["id"] == str(process.id)
    assert len(snapshot["versions"]) == 1
    assert len(snapshot["allocation_revisions"]) == 2
    assert snapshot["teaching_plan"]["current_generation_number"] == 2
    assert len(snapshot["teaching_activities"]) == 3
    assert len(snapshot["requirements"]) == 3
    assert len(snapshot["assignments"]) == 2


def test_backup_snapshot_without_plan(client: TestClient, session: Session) -> None:
    process = _config_only_source(session)

    snapshot = json.loads(_backup_content(client, process.id))

    assert snapshot["teaching_plan"] is None
    assert snapshot["teaching_activities"] == []
    assert snapshot["requirements"] == []


def test_create_csv_export_with_version(client: TestClient, session: Session) -> None:
    process, _rev, slot_new, _slot_sec = _full_source(session)
    version = client.post(
        f"/reparto/assignment-processes/{process.id}/versions",
        json={"reason": "csv"},
    ).json()

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={
            "export_type": "internal_draft",
            "format": "csv",
            "process_version_id": version["id"],
        },
    )

    assert resp.status_code == 201
    content = resp.json()["content"]
    assert content.startswith("section,id,hours,status")
    assert "requirement," in content
    assert "assignment," in content


def test_list_artifacts_endpoint(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(session)
    created = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "backup", "format": "json"},
    )

    resp = client.get(f"/reparto/assignment-processes/{process.id}/exports")

    assert created.status_code == 201
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


def test_create_export_returns_404_for_wrong_version(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={
            "export_type": "backup",
            "format": "json",
            "process_version_id": str(uuid.uuid4()),
        },
    )

    assert resp.status_code == 404


def test_create_export_rejects_version_of_other_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    other = factories.make_assignment_process(session)
    other_version = client.post(
        f"/reparto/assignment-processes/{other.id}/versions",
        json={"reason": "other"},
    ).json()

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={
            "export_type": "backup",
            "format": "json",
            "process_version_id": other_version["id"],
        },
    )

    assert resp.status_code == 404


def test_final_export_blocked_by_validations(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    profile = factories.make_teacher_profile(session)
    # An active, participating teacher below target is a blocking finding.
    factories.make_process_teacher(session, process, profile, base_weekly_hours=18.0)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "final", "format": "json"},
    )

    assert resp.status_code == 400
    assert "blocking validations" in resp.json()["detail"]


def test_pdf_export_returns_not_implemented(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "final", "format": "pdf"},
    )

    assert resp.status_code == 501
    assert resp.json()["detail"] == "PDF export is not implemented."
    session.refresh(process)
    assert process.status != AssignmentProcessStatus.ARCHIVED


def test_final_json_export_archives_process(
    client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "final", "format": "json"},
    )

    assert resp.status_code == 201
    session.refresh(process)
    assert process.status == AssignmentProcessStatus.ARCHIVED


def test_reader_cannot_create_artifact(
    reader_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)

    resp = reader_client.post(
        f"/reparto/assignment-processes/{process.id}/exports",
        json={"export_type": "backup", "format": "json"},
    )

    assert resp.status_code == 403


# ── Restore: happy paths ─────────────────────────────────────────────────────


def test_restore_full_backup(client: TestClient, session: Session) -> None:
    source, revision_current, slot_new, _slot_sec = _full_source(session)
    target = factories.make_assignment_process(session)
    content = _backup_content(client, source.id)

    resp = _restore(client, target.id, content)

    assert resp.status_code == 201, resp.text
    session.refresh(target)
    assert target.created_from_process_id == source.id
    assert _count(session, DepartmentHourAllocationRevision, target.id) == 2
    assert _count(session, Subject, target.id) == 2
    assert _count(session, TeachingGroup, target.id) == 1
    assert _count(session, GroupSubject, target.id) == 2
    assert _count(session, ProcessTeacher, target.id) == 2
    assert _count(session, HourRequirement, target.id) == 3
    assert _count(session, Assignment, target.id) == 2

    plan = _plan(session, target.id)
    assert plan is not None
    restored_activities = session.exec(
        select(TeachingActivity).where(TeachingActivity.teaching_plan_id == plan.id)
    ).all()
    assert len(restored_activities) == 3

    assert plan.status == TeachingPlanStatus.REQUIREMENTS_GENERATED
    assert plan.current_generation_number == 2
    # Feasibility is never trusted from a backup (plan §20.25).
    assert plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED
    assert plan.requirements_generated_at is not None
    assert plan.locked_at is not None
    # The current allocation revision was remapped onto the restored plan.
    current = session.exec(
        select(DepartmentHourAllocationRevision)
        .where(DepartmentHourAllocationRevision.assignment_process_id == target.id)
        .where(col(DepartmentHourAllocationRevision.superseded_at).is_(None))
    ).all()
    assert len(current) == 1
    assert plan.allocation_revision_id == current[0].id

    # The retired-and-superseded slot points at the restored replacement slot.
    superseded = session.exec(
        select(HourRequirement)
        .where(HourRequirement.assignment_process_id == target.id)
        .where(col(HourRequirement.superseded_by_requirement_id).is_not(None))
    ).all()
    assert len(superseded) == 1
    replacement = session.get(
        HourRequirement, superseded[0].superseded_by_requirement_id
    )
    assert replacement is not None
    assert replacement.status == HourRequirementStatus.ASSIGNED


def test_restore_skips_slots_when_mode_off(
    client: TestClient, session: Session
) -> None:
    source, *_ = _full_source(session)
    target = factories.make_assignment_process(session)
    content = _backup_content(client, source.id)

    resp = _restore(client, target.id, content, restore_assignments=False)

    assert resp.status_code == 201, resp.text
    assert _count(session, HourRequirement, target.id) == 0
    assert _count(session, Assignment, target.id) == 0
    # Config, allocation and activities are still restored.
    assert _count(session, DepartmentHourAllocationRevision, target.id) == 2
    plan = _plan(session, target.id)
    assert plan is not None
    # A post-generation status is downgraded to the LOCKED baseline.
    assert plan.status == TeachingPlanStatus.LOCKED
    assert plan.current_generation_number == 0
    assert plan.requirements_generated_at is None
    assert plan.locked_at is not None


def test_restore_config_only_backup(client: TestClient, session: Session) -> None:
    source = _config_only_source(session)
    target = factories.make_assignment_process(session)
    content = _backup_content(client, source.id)

    resp = _restore(client, target.id, content)

    assert resp.status_code == 201, resp.text
    assert _plan(session, target.id) is None
    assert _count(session, Subject, target.id) == 1
    assert _count(session, ProcessTeacher, target.id) == 1


def test_restore_preserves_pre_generation_status(
    client: TestClient, session: Session
) -> None:
    source = factories.make_assignment_process(session)
    factories.make_teaching_plan(session, source, status=TeachingPlanStatus.BALANCED)
    target = factories.make_assignment_process(session)
    content = _backup_content(client, source.id)

    resp = _restore(client, target.id, content, restore_assignments=False)

    assert resp.status_code == 201, resp.text
    plan = _plan(session, target.id)
    assert plan is not None
    assert plan.status == TeachingPlanStatus.BALANCED
    assert plan.locked_at is None


# ── Restore: guards ──────────────────────────────────────────────────────────


def test_restore_requires_draft(client: TestClient, session: Session) -> None:
    process = factories.make_assignment_process(
        session, status=AssignmentProcessStatus.READY_FOR_MEETING
    )

    resp = _restore(client, process.id, "{}")

    assert resp.status_code == 400
    assert "draft process" in resp.json()["detail"]


def test_restore_rejects_invalid_content(client: TestClient, session: Session) -> None:
    target = factories.make_assignment_process(session)

    invalid_json = _restore(client, target.id, "not-json")
    non_object = _restore(client, target.id, "[]")
    missing_process = _restore(client, target.id, '{"subjects":[]}')
    missing_section = _restore(client, target.id, '{"process":{}}')

    assert invalid_json.status_code == 400
    assert non_object.status_code == 400
    assert missing_process.status_code == 400
    assert missing_section.status_code == 400


def test_reader_cannot_restore(reader_client: TestClient, session: Session) -> None:
    target = factories.make_assignment_process(session)

    resp = reader_client.post(
        f"/reparto/assignment-processes/{target.id}/restore-draft",
        json={"content": "{}"},
    )

    assert resp.status_code == 403


# ── Restore: consistency validation (plan §10.4) ─────────────────────────────


def _mutated_backup(client: TestClient, source_id: uuid.UUID, mutate: Any) -> str:
    snapshot = json.loads(_backup_content(client, source_id))
    mutate(snapshot)
    return json.dumps(snapshot)


def test_restore_rejects_requirements_without_plan(
    client: TestClient, session: Session
) -> None:
    source, *_ = _full_source(session)
    target = factories.make_assignment_process(session)

    def _drop_plan(snapshot: dict[str, Any]) -> None:
        snapshot["teaching_plan"] = None

    content = _mutated_backup(client, source.id, _drop_plan)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "no teaching plan" in resp.json()["detail"]


def test_restore_rejects_generation_beyond_plan(
    client: TestClient, session: Session
) -> None:
    source, *_ = _full_source(session)
    target = factories.make_assignment_process(session)

    def _bump(snapshot: dict[str, Any]) -> None:
        snapshot["requirements"][0]["created_generation"] = 99

    content = _mutated_backup(client, source.id, _bump)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "beyond the plan" in resp.json()["detail"]


def test_restore_rejects_bad_retirement_generation(
    client: TestClient, session: Session
) -> None:
    source, *_ = _full_source(session)
    target = factories.make_assignment_process(session)

    def _break(snapshot: dict[str, Any]) -> None:
        for row in snapshot["requirements"]:
            if row["retired_generation"] is not None:
                row["retired_generation"] = 0  # below created_generation

    content = _mutated_backup(client, source.id, _break)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "retirement generation" in resp.json()["detail"]


def test_restore_rejects_dangling_supersession(
    client: TestClient, session: Session
) -> None:
    source, *_ = _full_source(session)
    target = factories.make_assignment_process(session)

    def _dangle(snapshot: dict[str, Any]) -> None:
        for row in snapshot["requirements"]:
            if row["superseded_by_requirement_id"] is not None:
                row["superseded_by_requirement_id"] = str(uuid.uuid4())

    content = _mutated_backup(client, source.id, _dangle)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "superseded by a slot missing" in resp.json()["detail"]


def test_restore_rejects_assignment_missing_requirement(
    client: TestClient, session: Session
) -> None:
    source, *_ = _full_source(session)
    target = factories.make_assignment_process(session)

    def _orphan(snapshot: dict[str, Any]) -> None:
        snapshot["assignments"][0]["hour_requirement_id"] = str(uuid.uuid4())

    content = _mutated_backup(client, source.id, _orphan)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "requirement missing" in resp.json()["detail"]


def test_restore_rejects_assignment_activity_mismatch(
    client: TestClient, session: Session
) -> None:
    source, *_ = _full_source(session)
    target = factories.make_assignment_process(session)

    def _mismatch(snapshot: dict[str, Any]) -> None:
        snapshot["assignments"][0]["teaching_activity_id"] = str(uuid.uuid4())

    content = _mutated_backup(client, source.id, _mismatch)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "does not match its requirement" in resp.json()["detail"]


def test_restore_rejects_assignment_missing_teacher(
    client: TestClient, session: Session
) -> None:
    source, *_ = _full_source(session)
    target = factories.make_assignment_process(session)

    def _no_teacher(snapshot: dict[str, Any]) -> None:
        snapshot["assignments"][0]["process_teacher_id"] = str(uuid.uuid4())

    content = _mutated_backup(client, source.id, _no_teacher)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "teacher missing" in resp.json()["detail"]


def test_restore_rejects_two_active_on_slot(
    client: TestClient, session: Session
) -> None:
    source, _rev, slot_new, _slot_sec = _full_source(session)
    target = factories.make_assignment_process(session)

    def _double(snapshot: dict[str, Any]) -> None:
        active = next(
            row for row in snapshot["assignments"] if row["status"] == "active"
        )
        clone = dict(active)
        clone["id"] = str(uuid.uuid4())
        snapshot["assignments"].append(clone)

    content = _mutated_backup(client, source.id, _double)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "more than one active assignment" in resp.json()["detail"]


def test_restore_rejects_teacher_twice_on_activity(
    client: TestClient, session: Session
) -> None:
    source, _rev, slot_new, slot_sec = _full_source(session)
    target = factories.make_assignment_process(session)

    def _twice(snapshot: dict[str, Any]) -> None:
        active = next(
            row for row in snapshot["assignments"] if row["status"] == "active"
        )
        clone = dict(active)
        clone["id"] = str(uuid.uuid4())
        # Same activity + teacher, different requirement slot -> distinct-teacher
        # violation on the activity.
        clone["hour_requirement_id"] = str(slot_sec.id)
        clone["teaching_activity_id"] = active["teaching_activity_id"]
        # Point the spare slot at the same activity so the slot/activity pair is
        # consistent for the earlier check.
        for req in snapshot["requirements"]:
            if req["id"] == str(slot_sec.id):
                req["teaching_activity_id"] = active["teaching_activity_id"]
        snapshot["assignments"].append(clone)

    content = _mutated_backup(client, source.id, _twice)
    resp = _restore(client, target.id, content)

    assert resp.status_code == 400
    assert "assigned twice on one activity" in resp.json()["detail"]
