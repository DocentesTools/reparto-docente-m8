"""TeachingActivity controller (per process).

CRUD logic for department teaching-plan activities and their group links
(plan §5.6, §5.7, §7.4). The API is process-scoped; the owning
:class:`~reparto_service.db_models.teaching_plans.TeachingPlan` is resolved
server-side, and every mutation enforces the structural invariants §5/§20 fix:

* the process must own a teaching plan and that plan must be mutable — a
  ``LOCKED``/``REQUIREMENTS_GENERATED``/stale plan blocks normal activity
  mutation (plan §5.6 "LOCKED prevents normal activity mutation", §5.5, §20.14);
* only ``SECONDARY_MANUAL`` activities are created here — ``MAIN_GENERATED``
  activities are one-to-one with a ``GroupSubject`` and come from the
  materialisation flow (plan §20.10, its own later task);
* every linked ``GroupSubject`` cell must live in the same process and match the
  activity subject (plan §5.7, §20.10);
* link count is policy-checked: zero links need the subject's
  ``allows_zero_groups`` flag, more than one needs ``allows_multiple_groups``
  (plan §5.6, §5.3); a multi-group activity uses one uniform
  ``group_weekly_hours_per_group`` for every group (plan §20.11).

Balance recomputation and the balanced→unbalanced status transition that an
activity change triggers (plan §20.14) belong to the dual-balance SummaryService
task and are deferred here, matching every prior model task. Guarded retirement
against generated requirements / assignments (plan §20.12) is a no-op today
because the redesigned ``HourRequirement.teaching_activity_id`` link does not
exist yet; it is wired in with that redesign.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from auth_sdk_m8.schemas.user import UserModel

from reparto_service.controllers.base import DomainController
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teaching_activities import (
    MainMaterializationResult,
    TeachingActivitiesPublic,
    TeachingActivity,
    TeachingActivityCreate,
    TeachingActivityGroup,
    TeachingActivityPublic,
    TeachingActivityUpdate,
)
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AuditEventType,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingPlanStatus,
)

# Plan statuses in which normal activity mutation is allowed (plan §5.6, §20.14):
# still-planning states. LOCKED / REQUIREMENTS_GENERATED / STALE /
# RECONCILIATION_REQUIRED all require an explicit unlock/reconcile first.
_MUTABLE_PLAN_STATUSES: frozenset[TeachingPlanStatus] = frozenset(
    {
        TeachingPlanStatus.DRAFT,
        TeachingPlanStatus.UNBALANCED,
        TeachingPlanStatus.BALANCED,
    }
)


def _first_hours(override: float | None, default: float | None) -> float:
    """Resolve an effective hour value for a materialised activity (plan §5.5).

    A cell override wins; otherwise the subject default is inherited; when both
    are unset the value materialises as ``0.0`` so a main cell always yields a
    concrete activity (the resulting group imbalance is surfaced by the planning
    validations, never silently blocked). Actual values stay ``float`` today
    (§3.9 Decimal sweep deferred).
    """
    if override is not None:
        return override
    if default is not None:
        return default
    return 0.0


class TeachingActivityController(DomainController):
    """CRUD logic for teaching activities inside one assignment process."""

    @staticmethod
    def list_teaching_activities(
        session: Session, process_id: uuid.UUID
    ) -> TeachingActivitiesPublic:
        DomainController.get_process_or_404(session, process_id)
        plan = TeachingActivityController._plan_row(session, process_id)
        if plan is None:
            return TeachingActivitiesPublic(data=[], count=0)
        activities = list(
            session.exec(
                select(TeachingActivity)
                .where(TeachingActivity.teaching_plan_id == plan.id)
                .order_by(col(TeachingActivity.created_at))
            ).all()
        )
        return TeachingActivitiesPublic(
            data=[
                TeachingActivityController._to_public(session, activity)
                for activity in activities
            ],
            count=len(activities),
        )

    @staticmethod
    def get_teaching_activity(
        session: Session, process_id: uuid.UUID, activity_id: uuid.UUID
    ) -> TeachingActivityPublic:
        activity = TeachingActivityController._get_or_404(
            session, process_id, activity_id
        )
        return TeachingActivityController._to_public(session, activity)

    @staticmethod
    def create_teaching_activity(
        session: Session,
        process_id: uuid.UUID,
        activity_in: TeachingActivityCreate,
        current_user: UserModel,
    ) -> TeachingActivityPublic:
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        plan = TeachingActivityController._require_mutable_plan(session, process_id)

        if activity_in.source is not TeachingActivitySource.SECONDARY_MANUAL:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Only SECONDARY_MANUAL activities can be created here; "
                    "MAIN_GENERATED activities are materialised from group "
                    "subjects (plan §20.10)."
                ),
            )
        subject = TeachingActivityController._get_subject_or_404(
            session, process_id, activity_in.subject_id
        )
        link_ids = TeachingActivityController._validate_links(
            session, process_id, subject, activity_in.group_subject_ids
        )

        activity = TeachingActivity(
            teaching_plan_id=plan.id,
            subject_id=subject.id,
            allocation_category=activity_in.allocation_category,
            activity_type=activity_in.activity_type,
            group_weekly_hours_per_group=activity_in.group_weekly_hours_per_group,
            teacher_weekly_hours_per_position=(
                activity_in.teacher_weekly_hours_per_position
            ),
            required_teacher_count=activity_in.required_teacher_count,
            source=TeachingActivitySource.SECONDARY_MANUAL,
            notes=activity_in.notes,
        )
        session.add(activity)
        for cell_id in link_ids:
            session.add(
                TeachingActivityGroup(
                    teaching_activity_id=activity.id, group_subject_id=cell_id
                )
            )
        TeachingActivityController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.TEACHING_ACTIVITY_CREATED,
            entity_type="teaching_activity",
            entity_id=activity.id,
            before=None,
            after=activity,
        )
        session.commit()
        session.refresh(activity)
        return TeachingActivityController._to_public(session, activity)

    @staticmethod
    def update_teaching_activity(
        session: Session,
        process_id: uuid.UUID,
        activity_id: uuid.UUID,
        activity_in: TeachingActivityUpdate,
        current_user: UserModel,
    ) -> TeachingActivityPublic:
        activity = TeachingActivityController._get_or_404(
            session, process_id, activity_id
        )
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        TeachingActivityController._require_mutable_plan(session, process_id)

        before = TeachingActivity.model_validate(activity.model_dump())
        patch = activity_in.model_dump(exclude_unset=True)
        new_links = patch.pop("group_subject_ids", None)
        activity.sqlmodel_update(patch)
        session.add(activity)

        if new_links is not None:
            subject = TeachingActivityController._get_subject_or_404(
                session, process_id, activity.subject_id
            )
            link_ids = TeachingActivityController._validate_links(
                session, process_id, subject, new_links
            )
            TeachingActivityController._replace_links(session, activity.id, link_ids)

        TeachingActivityController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.TEACHING_ACTIVITY_UPDATED,
            entity_type="teaching_activity",
            entity_id=activity.id,
            before=before,
            after=activity,
        )
        session.commit()
        session.refresh(activity)
        return TeachingActivityController._to_public(session, activity)

    @staticmethod
    def delete_teaching_activity(
        session: Session,
        process_id: uuid.UUID,
        activity_id: uuid.UUID,
        current_user: UserModel,
    ) -> TeachingActivityPublic:
        activity = TeachingActivityController._get_or_404(
            session, process_id, activity_id
        )
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        TeachingActivityController._require_mutable_plan(session, process_id)

        public = TeachingActivityController._to_public(session, activity)
        before = TeachingActivity.model_validate(activity.model_dump())
        TeachingActivityController._replace_links(session, activity.id, [])
        session.delete(activity)
        TeachingActivityController.record_audit_event(
            session,
            process_id=process_id,
            current_user=current_user,
            event_type=AuditEventType.TEACHING_ACTIVITY_DELETED,
            entity_type="teaching_activity",
            entity_id=activity.id,
            before=before,
            after=None,
        )
        session.commit()
        return public

    # ── Main-activity materialization (plan §7.3, §20.10) ────────────────────

    @staticmethod
    def materialize_main(
        session: Session,
        process_id: uuid.UUID,
        current_user: UserModel,
    ) -> MainMaterializationResult:
        """Generate one MAIN_GENERATED activity per active main group-subject cell.

        For every active MAIN ``GroupSubject`` cell that has no live
        ``MAIN_GENERATED`` activity, materialise a single-group activity whose
        planning values come from the cell (falling back to the subject default,
        then ``0``) — one link, ``source_group_subject_id`` set (plan §5.6,
        §20.10). The run is **idempotent and deterministic** (plan §19): cells
        processed in ``id`` order, already-materialised cells skipped (never
        duplicated — the active partial-unique index is the DB backstop), so
        re-running yields no new rows.

        Requires a mutable process and a mutable (unlocked) plan: a
        ``LOCKED``/``REQUIREMENTS_GENERATED``/stale plan blocks materialisation
        just like any other activity mutation (plan §5.6, §20.14).
        """
        DomainController.ensure_process_mutable(
            DomainController.get_process_or_404(session, process_id)
        )
        plan = TeachingActivityController._require_mutable_plan(session, process_id)
        already = TeachingActivityController._materialized_main_source_ids(
            session, plan
        )

        created: list[TeachingActivityPublic] = []
        skipped: list[uuid.UUID] = []
        for cell, subject in TeachingActivityController._active_main_cells(
            session, process_id
        ):
            if cell.id in already:
                skipped.append(cell.id)
                continue
            activity = TeachingActivity(
                teaching_plan_id=plan.id,
                subject_id=cell.subject_id,
                allocation_category=SubjectAllocationCategory.MAIN,
                activity_type=subject.activity_type,
                group_weekly_hours_per_group=_first_hours(
                    cell.group_weekly_hours, subject.default_group_weekly_hours
                ),
                teacher_weekly_hours_per_position=_first_hours(
                    cell.teacher_weekly_hours_per_position,
                    subject.default_teacher_weekly_hours_per_position,
                ),
                required_teacher_count=cell.required_teacher_count,
                source=TeachingActivitySource.MAIN_GENERATED,
                source_group_subject_id=cell.id,
            )
            session.add(activity)
            session.add(
                TeachingActivityGroup(
                    teaching_activity_id=activity.id, group_subject_id=cell.id
                )
            )
            TeachingActivityController.record_audit_event(
                session,
                process_id=process_id,
                current_user=current_user,
                event_type=AuditEventType.TEACHING_ACTIVITY_MATERIALIZED,
                entity_type="teaching_activity",
                entity_id=activity.id,
                before=None,
                after=activity,
            )
            created.append(TeachingActivityController._to_public(session, activity))

        session.commit()
        return MainMaterializationResult(
            created=created,
            created_count=len(created),
            skipped_source_ids=skipped,
            skipped_count=len(skipped),
        )

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _plan_row(session: Session, process_id: uuid.UUID) -> TeachingPlan | None:
        return session.exec(
            select(TeachingPlan).where(TeachingPlan.assignment_process_id == process_id)
        ).first()

    @staticmethod
    def _require_mutable_plan(session: Session, process_id: uuid.UUID) -> TeachingPlan:
        """Return the process's plan, or 400 when it is missing or locked."""
        plan = TeachingActivityController._plan_row(session, process_id)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Process {process_id} has no teaching plan; create one "
                    "before adding activities."
                ),
            )
        if plan.status not in _MUTABLE_PLAN_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Teaching plan is {plan.status.value}; unlock it before "
                    "mutating activities (plan §5.6, §20.14)."
                ),
            )
        return plan

    @staticmethod
    def _get_or_404(
        session: Session, process_id: uuid.UUID, activity_id: uuid.UUID
    ) -> TeachingActivity:
        DomainController.get_process_or_404(session, process_id)
        plan = TeachingActivityController._plan_row(session, process_id)
        activity = session.get(TeachingActivity, activity_id)
        if activity is None or plan is None or activity.teaching_plan_id != plan.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"TeachingActivity {activity_id} not found in process {process_id}."
                ),
            )
        return activity

    @staticmethod
    def _materialized_main_source_ids(
        session: Session, plan: TeachingPlan
    ) -> set[uuid.UUID]:
        """Source cell IDs of the plan's live MAIN_GENERATED activities (§20.10)."""
        rows = session.exec(
            select(TeachingActivity.source_group_subject_id)
            .where(TeachingActivity.teaching_plan_id == plan.id)
            .where(col(TeachingActivity.retired_at).is_(None))
            .where(TeachingActivity.source == TeachingActivitySource.MAIN_GENERATED)
            .where(col(TeachingActivity.source_group_subject_id).is_not(None))
        ).all()
        return {source_id for source_id in rows if source_id is not None}

    @staticmethod
    def _active_main_cells(
        session: Session, process_id: uuid.UUID
    ) -> list[tuple[GroupSubject, Subject]]:
        """Active MAIN group-subject cells with their subjects, ordered by cell id.

        A cell is a main planning candidate when its subject's
        ``allocation_category`` is ``MAIN`` (plan §5.5); the ``id`` ordering makes
        materialisation deterministic (plan §19).
        """
        return list(
            session.exec(
                select(GroupSubject, Subject)
                .where(GroupSubject.assignment_process_id == process_id)
                .where(col(GroupSubject.active).is_(True))
                .where(GroupSubject.subject_id == Subject.id)
                .where(Subject.allocation_category == SubjectAllocationCategory.MAIN)
                .order_by(col(GroupSubject.id))
            ).all()
        )

    @staticmethod
    def _get_subject_or_404(
        session: Session, process_id: uuid.UUID, subject_id: uuid.UUID
    ) -> Subject:
        subject = session.get(Subject, subject_id)
        if subject is None or subject.assignment_process_id != process_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Subject {subject_id} not found in process {process_id}.",
            )
        return subject

    @staticmethod
    def _validate_links(
        session: Session,
        process_id: uuid.UUID,
        subject: Subject,
        group_subject_ids: list[uuid.UUID],
    ) -> list[uuid.UUID]:
        """Validate the requested links and return the de-duplicated cell IDs.

        Enforces plan §5.7 / §20.10 (cells in-process and same subject), the
        no-duplicate rule and the §5.6/§5.3 link-count policy.
        """
        seen: list[uuid.UUID] = []
        for cell_id in group_subject_ids:
            if cell_id in seen:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Duplicate linked group-subject {cell_id}.",
                )
            seen.append(cell_id)

        cell_count = len(seen)
        if cell_count == 0 and not subject.allows_zero_groups:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "A zero-group activity requires a subject that allows zero "
                    "groups (plan §5.6)."
                ),
            )
        if cell_count > 1 and not subject.allows_multiple_groups:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "A multi-group activity requires a subject that allows "
                    "multiple groups (plan §5.6, §20.10)."
                ),
            )

        for cell_id in seen:
            cell = session.get(GroupSubject, cell_id)
            if cell is None or cell.assignment_process_id != process_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"GroupSubject {cell_id} not found in process {process_id}."
                    ),
                )
            if cell.subject_id != subject.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Linked group-subject {cell_id} teaches a different "
                        "subject than the activity (plan §5.7)."
                    ),
                )
        return seen

    @staticmethod
    def _replace_links(
        session: Session, activity_id: uuid.UUID, cell_ids: list[uuid.UUID]
    ) -> None:
        """Replace an activity's link set with ``cell_ids`` (delete then insert).

        The deletes are flushed before the inserts so re-linking a cell that was
        already linked does not collide with the stale row on the
        ``(teaching_activity_id, group_subject_id)`` unique constraint.
        """
        existing_links = session.exec(
            select(TeachingActivityGroup).where(
                TeachingActivityGroup.teaching_activity_id == activity_id
            )
        ).all()
        for existing in existing_links:
            session.delete(existing)
        if existing_links:
            session.flush()
        for cell_id in cell_ids:
            session.add(
                TeachingActivityGroup(
                    teaching_activity_id=activity_id, group_subject_id=cell_id
                )
            )

    @staticmethod
    def _link_ids(session: Session, activity_id: uuid.UUID) -> list[uuid.UUID]:
        """Return the group-subject cell IDs an activity links, creation-ordered."""
        return list(
            session.exec(
                select(TeachingActivityGroup.group_subject_id)
                .where(TeachingActivityGroup.teaching_activity_id == activity_id)
                .order_by(col(TeachingActivityGroup.group_subject_id))
            ).all()
        )

    @staticmethod
    def _to_public(
        session: Session, activity: TeachingActivity
    ) -> TeachingActivityPublic:
        link_ids = TeachingActivityController._link_ids(session, activity.id)
        return TeachingActivityPublic.model_validate(
            {
                **activity.model_dump(),
                "group_subject_ids": link_ids,
                "linked_group_count": len(link_ids),
            }
        )


__all__ = ["TeachingActivityController"]
