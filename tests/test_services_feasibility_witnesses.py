"""Persistence, caching, confidentiality and repair of feasibility witnesses."""

from __future__ import annotations

import logging
import uuid

import pytest
from fastapi import HTTPException
from auth_sdk_m8.schemas.user import UserModel
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from reparto_service.db_models.feasibility_witnesses import FeasibilityWitness
from reparto_service.enums import FeasibilityStatus, HourRequirementStatus
from reparto_service.services.feasibility import (
    SOLVER_VERSION,
    FeasibilityDiagnostic,
    FeasibilityDiagnosticCode,
    FeasibilityResult,
    FeasibilityWitnessEntry,
)
from reparto_service.services.feasibility_witnesses import (
    FeasibilityWitnessService,
    build_feasibility_snapshot,
)
from reparto_service.services.selection_guards import (
    WitnessRepairCode,
    WitnessRepairResult,
)
from tests import factories


def _path(process_id: uuid.UUID, suffix: str) -> str:
    return (
        f"/reparto/assignment-processes/{process_id}/teaching-plan/feasibility/{suffix}"
    )


def _feasible_setup(session: Session):
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    activity = factories.make_teaching_activity(
        session,
        plan,
        subject,
        required_teacher_count=2,
        teacher_weekly_hours_per_position=4.0,
    )
    slots = [
        factories.make_hour_requirement(
            session,
            process,
            activity,
            position_index=index,
            required_teacher_hours=4.0,
        )
        for index in range(2)
    ]
    teachers = [
        factories.make_process_teacher(
            session,
            process,
            factories.make_teacher_profile(session, display_name=f"Teacher {index}"),
            base_weekly_hours=4.0,
        )
        for index in range(2)
    ]
    return process, plan, slots, teachers


def test_admin_evaluation_persists_reuses_and_exposes_stable_witness(
    admin_client: TestClient,
    session: Session,
) -> None:
    process, plan, slots, teachers = _feasible_setup(session)

    first = admin_client.post(_path(process.id, "evaluate"))
    assert first.status_code == 200
    assert first.json()["status"] == FeasibilityStatus.FEASIBLE.value
    assert first.json()["cache_reused"] is False
    assert first.json()["witness_available"] is True

    second = admin_client.post(_path(process.id, "evaluate"))
    assert second.status_code == 200
    assert second.json()["cache_reused"] is True
    assert second.json()["input_fingerprint"] == first.json()["input_fingerprint"]

    witness = admin_client.get(_path(process.id, "witness"))
    assert witness.status_code == 200
    body = witness.json()
    assert body["solver_version"] == SOLVER_VERSION
    assert [item["slot_id"] for item in body["witness"]] == sorted(
        str(slot.id) for slot in slots
    )
    assert {item["process_teacher_id"] for item in body["witness"]} == {
        str(teacher.id) for teacher in teachers
    }

    session.refresh(plan)
    assert plan.feasibility_input_fingerprint == first.json()["input_fingerprint"]


def test_evaluation_telemetry_is_bounded_and_contains_no_pii(
    admin_client: TestClient,
    session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    process, _plan, _slots, teachers = _feasible_setup(session)
    caplog.set_level(
        logging.INFO,
        logger="reparto_service.services.feasibility_witnesses",
    )

    response = admin_client.post(_path(process.id, "evaluate"))
    assert response.status_code == 200
    records = [
        record.getMessage()
        for record in caplog.records
        if record.name == "reparto_service.services.feasibility_witnesses"
    ]
    assert len(records) == 1
    telemetry = records[0]
    assert "status=feasible" in telemetry
    assert "participant_count=2" in telemetry
    assert "slot_count=2" in telemetry
    assert "max_steps=1000000" in telemetry
    assert str(process.id) not in telemetry
    assert response.json()["input_fingerprint"] not in telemetry
    for teacher in teachers:
        assert str(teacher.id) not in telemetry
    assert "Teacher 0" not in telemetry
    assert "Teacher 1" not in telemetry


def test_regular_writer_cannot_evaluate_or_read_witness(
    writer_client: TestClient, session: Session, writer_user: UserModel
) -> None:
    process, _plan, _slots, _teachers = _feasible_setup(session)
    factories.enrol(session, process, writer_user)
    assert writer_client.post(_path(process.id, "evaluate")).status_code == 403
    assert writer_client.get(_path(process.id, "witness")).status_code == 403


def test_evaluation_includes_fixed_assignments_in_complete_witness(
    admin_client: TestClient, session: Session
) -> None:
    process, _plan, slots, teachers = _feasible_setup(session)
    factories.make_assignment(session, process, slots[0], teachers[0])
    slots[0].status = HourRequirementStatus.ASSIGNED
    session.add(slots[0])
    session.commit()

    response = admin_client.post(_path(process.id, "evaluate"))
    assert response.status_code == 200
    witness = admin_client.get(_path(process.id, "witness")).json()["witness"]
    mapping = {item["slot_id"]: item["process_teacher_id"] for item in witness}
    assert mapping[str(slots[0].id)] == str(teachers[0].id)
    assert mapping[str(slots[1].id)] == str(teachers[1].id)


def test_alternative_selection_repairs_and_persists_post_state_witness(
    admin_client: TestClient, session: Session
) -> None:
    process, plan, slots, teachers = _feasible_setup(session)
    assert admin_client.post(_path(process.id, "evaluate")).status_code == 200
    before = plan.feasibility_input_fingerprint
    persisted = admin_client.get(_path(process.id, "witness")).json()["witness"]
    initial = {item["slot_id"]: item["process_teacher_id"] for item in persisted}
    chosen_slot = slots[0]
    alternative = next(
        teacher
        for teacher in teachers
        if str(teacher.id) != initial[str(chosen_slot.id)]
    )

    assignment = admin_client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "hour_requirement_id": str(chosen_slot.id),
            "process_teacher_id": str(alternative.id),
        },
    )
    assert assignment.status_code == 201
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.FEASIBLE
    assert plan.feasibility_input_fingerprint != before
    repaired = admin_client.get(_path(process.id, "witness")).json()["witness"]
    repaired_map = {item["slot_id"]: item["process_teacher_id"] for item in repaired}
    assert repaired_map[str(chosen_slot.id)] == str(alternative.id)


def test_feasible_selection_fails_closed_without_current_witness(
    admin_client: TestClient, session: Session
) -> None:
    process, plan, slots, teachers = _feasible_setup(session)
    plan.feasibility_status = FeasibilityStatus.FEASIBLE
    session.add(plan)
    session.commit()

    response = admin_client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "hour_requirement_id": str(slots[0].id),
            "process_teacher_id": str(teachers[0].id),
        },
    )
    assert response.status_code == 409
    assert "missing or stale" in response.json()["detail"]


def test_selection_fails_closed_when_bounded_repair_cannot_finish(
    admin_client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    process, _plan, slots, teachers = _feasible_setup(session)
    admin_client.post(_path(process.id, "evaluate"))
    monkeypatch.setattr(
        FeasibilityWitnessService,
        "repair_for_selection",
        lambda *args, **kwargs: WitnessRepairResult(
            WitnessRepairCode.REPAIR_LIMIT_REACHED, None, 1
        ),
    )

    response = admin_client.post(
        f"/reparto/assignment-processes/{process.id}/assignments/",
        json={
            "hour_requirement_id": str(slots[0].id),
            "process_teacher_id": str(teachers[0].id),
        },
    )
    assert response.status_code == 409
    assert WitnessRepairCode.REPAIR_LIMIT_REACHED.value in response.json()["detail"]


def test_fingerprint_drift_expires_witness_and_re_evaluates(
    admin_client: TestClient, session: Session
) -> None:
    process, plan, _slots, teachers = _feasible_setup(session)
    first = admin_client.post(_path(process.id, "evaluate")).json()
    teachers[0].base_weekly_hours = 5.0
    teachers[1].base_weekly_hours = 3.0
    session.add(teachers[0])
    session.add(teachers[1])
    session.commit()

    assert admin_client.get(_path(process.id, "witness")).status_code == 409
    second = admin_client.post(_path(process.id, "evaluate")).json()
    assert second["cache_reused"] is False
    assert second["input_fingerprint"] != first["input_fingerprint"]
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.INFEASIBLE


def test_invalidation_removes_witness_and_is_safe_without_plan(
    admin_client: TestClient, session: Session
) -> None:
    process, plan, _slots, _teachers = _feasible_setup(session)
    admin_client.post(_path(process.id, "evaluate"))
    FeasibilityWitnessService.invalidate(session, process.id)
    session.commit()
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED
    assert plan.feasibility_input_fingerprint is None
    assert session.exec(select(FeasibilityWitness)).first() is None

    other = factories.make_assignment_process(session)
    FeasibilityWitnessService.invalidate(session, other.id)


def test_participant_mutation_endpoint_invalidates_cached_witness(
    admin_client: TestClient, session: Session
) -> None:
    process, plan, _slots, teachers = _feasible_setup(session)
    admin_client.post(_path(process.id, "evaluate"))
    response = admin_client.patch(
        f"/reparto/assignment-processes/{process.id}/teachers/{teachers[0].id}",
        json={"base_weekly_hours": 5.0},
    )
    assert response.status_code == 200
    session.refresh(plan)
    assert plan.feasibility_status == FeasibilityStatus.NOT_EVALUATED
    assert session.exec(select(FeasibilityWitness)).first() is None


def test_infeasible_result_is_cached_without_exposing_a_witness(
    admin_client: TestClient, session: Session
) -> None:
    process, _plan, slots, teachers = _feasible_setup(session)
    teachers[0].base_weekly_hours = 3.0
    teachers[1].base_weekly_hours = 5.0
    session.add(teachers[0])
    session.add(teachers[1])
    session.commit()

    first = admin_client.post(_path(process.id, "evaluate"))
    assert first.json()["status"] == FeasibilityStatus.INFEASIBLE.value
    assert first.json()["witness_available"] is False
    assert admin_client.get(_path(process.id, "witness")).status_code == 409
    assert admin_client.post(_path(process.id, "evaluate")).json()["cache_reused"]
    row = session.exec(select(FeasibilityWitness)).one()
    assert row.witness_json == []
    assert row.diagnostics_json
    assert len(slots) == 2


def test_service_rejects_missing_plan_and_inconsistent_repair(
    session: Session,
) -> None:
    missing = factories.make_assignment_process(session)
    with pytest.raises(HTTPException) as error:
        FeasibilityWitnessService.evaluate(session, missing.id)
    assert error.value.status_code == 404

    process, _plan, _slots, _teachers = _feasible_setup(session)
    with pytest.raises(HTTPException) as inconsistent:
        FeasibilityWitnessService.persist_repair(
            session,
            process_id=process.id,
            repaired_remaining=(FeasibilityWitnessEntry("unknown", "unknown"),),
        )
    assert inconsistent.value.status_code == 409


def test_snapshot_fingerprint_is_deterministic(session: Session) -> None:
    process, _plan, _slots, _teachers = _feasible_setup(session)
    first = build_feasibility_snapshot(session, process.id)
    second = build_feasibility_snapshot(session, process.id)
    assert first == second


def test_missing_or_mismatched_internal_row_is_never_reused(
    admin_client: TestClient, session: Session
) -> None:
    process, _plan, _slots, _teachers = _feasible_setup(session)
    admin_client.post(_path(process.id, "evaluate"))
    row = session.exec(select(FeasibilityWitness)).one()
    row.input_fingerprint = "mismatch"
    session.add(row)
    session.commit()
    assert admin_client.get(_path(process.id, "witness")).status_code == 409

    session.delete(row)
    session.commit()
    refreshed = admin_client.post(_path(process.id, "evaluate"))
    assert refreshed.status_code == 200
    assert refreshed.json()["cache_reused"] is False


# ── Administration-only diagnostics (plan §7.3, §20.24) ──────────────────────


def test_diagnostics_report_feasible_evaluation_has_no_findings(
    admin_client: TestClient, session: Session
) -> None:
    process, plan, _slots, _teachers = _feasible_setup(session)
    admin_client.post(_path(process.id, "evaluate"))

    response = admin_client.get(_path(process.id, "diagnostics"))

    assert response.status_code == 200
    body = response.json()
    assert body["teaching_plan_id"] == str(plan.id)
    assert body["assignment_process_id"] == str(process.id)
    assert body["status"] == FeasibilityStatus.FEASIBLE.value
    assert body["checked_at"] is not None
    assert body["diagnostics"] == []


def test_diagnostics_report_infeasible_lists_stable_findings(
    admin_client: TestClient, session: Session
) -> None:
    process, _plan, _slots, teachers = _feasible_setup(session)
    teachers[0].base_weekly_hours = 3.0
    session.add(teachers[0])
    session.commit()
    evaluation = admin_client.post(_path(process.id, "evaluate"))
    assert evaluation.json()["status"] == FeasibilityStatus.INFEASIBLE.value

    response = admin_client.get(_path(process.id, "diagnostics"))

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == FeasibilityStatus.INFEASIBLE.value
    assert body["diagnostics"] == [
        {
            "code": "incompatible_residual_totals",
            "message": (
                "Remaining participant targets and slot hours have different totals."
            ),
            "related_ids": [],
        }
    ]


def test_diagnostics_related_ids_identify_the_oversized_slot(
    admin_client: TestClient, session: Session
) -> None:
    process = factories.make_assignment_process(session)
    plan = factories.make_teaching_plan(session, process)
    subject = factories.make_subject(session, process)
    oversized = factories.make_teaching_activity(
        session,
        plan,
        subject,
        required_teacher_count=1,
        teacher_weekly_hours_per_position=3.0,
    )
    small = factories.make_teaching_activity(
        session,
        plan,
        subject,
        required_teacher_count=1,
        teacher_weekly_hours_per_position=1.0,
    )
    oversized_slot = factories.make_hour_requirement(
        session, process, oversized, required_teacher_hours=3.0
    )
    factories.make_hour_requirement(session, process, small, required_teacher_hours=1.0)
    for index in range(2):
        factories.make_process_teacher(
            session,
            process,
            factories.make_teacher_profile(session, display_name=f"Teacher {index}"),
            base_weekly_hours=2.0,
        )
    evaluation = admin_client.post(_path(process.id, "evaluate"))
    assert evaluation.json()["status"] == FeasibilityStatus.INFEASIBLE.value

    response = admin_client.get(_path(process.id, "diagnostics"))

    assert response.status_code == 200
    (diagnostic,) = response.json()["diagnostics"]
    assert diagnostic["code"] == "slot_exceeds_every_target"
    assert diagnostic["related_ids"] == [str(oversized_slot.id)]


def test_diagnostics_fail_closed_without_a_current_evaluation(
    admin_client: TestClient, session: Session
) -> None:
    process, plan, _slots, _teachers = _feasible_setup(session)
    assert admin_client.get(_path(process.id, "diagnostics")).status_code == 409

    admin_client.post(_path(process.id, "evaluate"))
    plan.current_generation_number = 7
    session.add(plan)
    session.commit()
    stale = admin_client.get(_path(process.id, "diagnostics"))
    assert stale.status_code == 409
    assert "evaluation is required" in stale.json()["detail"]

    missing = factories.make_assignment_process(session)
    assert admin_client.get(_path(missing.id, "diagnostics")).status_code == 404


def test_diagnostics_fail_closed_after_invalidation(
    admin_client: TestClient, session: Session
) -> None:
    process, _plan, _slots, teachers = _feasible_setup(session)
    admin_client.post(_path(process.id, "evaluate"))
    assert admin_client.get(_path(process.id, "diagnostics")).status_code == 200

    patch = admin_client.patch(
        f"/reparto/assignment-processes/{process.id}/teachers/{teachers[0].id}",
        json={"base_weekly_hours": 6.0},
    )
    assert patch.status_code == 200

    assert admin_client.get(_path(process.id, "diagnostics")).status_code == 409


def test_diagnostics_checked_at_falls_back_to_the_row_timestamp(
    admin_client: TestClient, session: Session
) -> None:
    process, plan, _slots, _teachers = _feasible_setup(session)
    admin_client.post(_path(process.id, "evaluate"))
    plan.feasibility_checked_at = None
    session.add(plan)
    session.commit()

    response = admin_client.get(_path(process.id, "diagnostics"))

    assert response.status_code == 200
    row = session.exec(select(FeasibilityWitness)).one()
    assert response.json()["checked_at"] == row.updated_at.isoformat()


def test_regular_writer_cannot_read_diagnostics(
    writer_client: TestClient, session: Session, writer_user: UserModel
) -> None:
    process, _plan, _slots, _teachers = _feasible_setup(session)
    factories.enrol(session, process, writer_user)
    assert writer_client.get(_path(process.id, "diagnostics")).status_code == 403


def test_the_published_summary_lists_each_related_id_once(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two findings about the same slot are one entry, not two (plan §20.25)."""

    process, plan, slots, _teachers = _feasible_setup(session)
    slot_id = str(slots[0].id)
    published: list[dict[str, object]] = []

    def _capture(_session, **kwargs):
        published.append(kwargs)

    monkeypatch.setattr("reparto_service.services.sse.publish_domain_event", _capture)

    result = FeasibilityResult(
        status=FeasibilityStatus.INFEASIBLE,
        witness=None,
        diagnostics=(
            FeasibilityDiagnostic(
                code=FeasibilityDiagnosticCode.SLOT_EXCEEDS_EVERY_TARGET,
                message="First finding.",
                related_ids=(slot_id,),
            ),
            FeasibilityDiagnostic(
                code=FeasibilityDiagnosticCode.INCOMPATIBLE_RESIDUAL_TOTALS,
                message="Second finding about the same slot.",
                related_ids=(slot_id, str(slots[1].id)),
            ),
        ),
        states_explored=1,
        memoization_hits=0,
    )
    evaluation = FeasibilityWitnessService._evaluation_public(
        plan, result=result, cache_reused=False, witness_available=False
    )

    FeasibilityWitnessService._publish_evaluation(
        session, plan, result, evaluation, started_at=0.0
    )

    assert len(published) == 1
    payload = published[0]["payload"]
    assert isinstance(payload, dict)
    assert payload["affected_ids"] == [slot_id, str(slots[1].id)]
    assert payload["diagnostic_codes"] == [
        FeasibilityDiagnosticCode.SLOT_EXCEEDS_EVERY_TARGET.value,
        FeasibilityDiagnosticCode.INCOMPATIBLE_RESIDUAL_TOTALS.value,
    ]
    assert published[0]["process_id"] == process.id
