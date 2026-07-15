"""Domain fixture factories used across the reparto_service test suite.

The factories return inserted rows. They are not pytest fixtures
themselves — call them from a test or a wrapper fixture that needs the
row + a session.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.classroom_stages import ClassroomStage
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.db_models.departments import Department
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.meeting_sessions import MeetingSession
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.selection_turns import SelectionTurn
from reparto_service.db_models.schools import School
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_activities import (
    TeachingActivity,
    TeachingActivityGroup,
)
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AcademicYearStatus,
    ActivityType,
    AssignmentProcessStatus,
    AssignmentSource,
    AssignmentStatus,
    DepartmentHourAllocationSource,
    FeasibilityStatus,
    HourRequirementStatus,
    MeetingSessionStatus,
    ProcessTeacherStatus,
    SelectionOrderMode,
    SelectionTurnStatus,
    SubjectAllocationCategory,
    TeachingActivitySource,
    TeachingActivitySyncState,
    TeachingPlanStatus,
)


def make_school(
    session: Session,
    *,
    name: str = "IES Test",
    locality: Optional[str] = "Test Town",
) -> School:
    school = School(
        name=name,
        locality=locality,
        province="Test Province",
        region="Andalucía",
    )
    session.add(school)
    session.commit()
    session.refresh(school)
    return school


def make_department(
    session: Session,
    school: School,
    *,
    name: str = "Matemáticas",
    slug: Optional[str] = None,
) -> Department:
    department = Department(
        school_id=school.id,
        name=name,
        slug=slug or name.lower(),
    )
    session.add(department)
    session.commit()
    session.refresh(department)
    return department


def make_academic_year(
    session: Session,
    *,
    creator_id: Optional[uuid.UUID] = None,
    label: str = "2026/2027",
    school: Optional[School] = None,
    status: AcademicYearStatus = AcademicYearStatus.ACTIVE,
) -> AcademicYear:
    year = AcademicYear(
        label=label,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        status=status,
        school_id=school.id if school is not None else None,
        created_by_user_id=creator_id or uuid.uuid4(),
    )
    session.add(year)
    session.commit()
    session.refresh(year)
    return year


def make_assignment_process(
    session: Session,
    *,
    creator_id: Optional[uuid.UUID] = None,
    academic_year: Optional[AcademicYear] = None,
    school: Optional[School] = None,
    department: Optional[Department] = None,
    status: AssignmentProcessStatus = AssignmentProcessStatus.DRAFT,
) -> AssignmentProcess:
    academic_year = academic_year or make_academic_year(session)
    school = school or make_school(session)
    department = department or make_department(session, school)
    process = AssignmentProcess(
        academic_year_id=academic_year.id,
        school_id=school.id,
        department_id=department.id,
        status=status,
        created_by_user_id=creator_id or uuid.uuid4(),
        created_at=datetime.now(tz=timezone.utc),
        updated_at=datetime.now(tz=timezone.utc),
    )
    session.add(process)
    session.commit()
    session.refresh(process)
    return process


def make_allocation_revision(
    session: Session,
    process: AssignmentProcess,
    *,
    revision_number: int = 1,
    allocated_group_weekly_hours: float = 120.0,
    reason: str = "Initial leadership allocation",
    source: DepartmentHourAllocationSource = (
        DepartmentHourAllocationSource.MANUAL_TRANSCRIPTION
    ),
    source_reference: Optional[str] = None,
    superseded_at: Optional[datetime] = None,
    creator_id: Optional[uuid.UUID] = None,
) -> DepartmentHourAllocationRevision:
    revision = DepartmentHourAllocationRevision(
        assignment_process_id=process.id,
        revision_number=revision_number,
        allocated_group_weekly_hours=allocated_group_weekly_hours,
        reason=reason,
        source=source,
        source_reference=source_reference,
        superseded_at=superseded_at,
        created_by_user_id=creator_id or uuid.uuid4(),
    )
    session.add(revision)
    session.commit()
    session.refresh(revision)
    return revision


def make_teaching_plan(
    session: Session,
    process: AssignmentProcess,
    *,
    status: TeachingPlanStatus = TeachingPlanStatus.DRAFT,
    current_generation_number: int = 0,
    feasibility_status: FeasibilityStatus = FeasibilityStatus.NOT_EVALUATED,
    stale_reason: Optional[str] = None,
) -> TeachingPlan:
    plan = TeachingPlan(
        assignment_process_id=process.id,
        status=status,
        current_generation_number=current_generation_number,
        feasibility_status=feasibility_status,
        stale_reason=stale_reason,
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def make_teacher_profile(
    session: Session,
    *,
    display_name: str = "Test Teacher",
    user_id: Optional[uuid.UUID] = None,
) -> TeacherProfile:
    profile = TeacherProfile(
        display_name=display_name,
        user_id=user_id,
        active=True,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def make_process_teacher(
    session: Session,
    process: AssignmentProcess,
    profile: TeacherProfile,
    *,
    base_weekly_hours: float = 18.0,
    extra_weekly_hours: float = 0.0,
    status: ProcessTeacherStatus = ProcessTeacherStatus.ACTIVE,
    selection_position: Optional[int] = None,
    selection_points: Optional[float] = None,
    selection_criteria_label: Optional[str] = None,
    selection_notes: Optional[str] = None,
    order_locked: bool = False,
    participates_in_selection: bool = True,
) -> ProcessTeacher:
    pt = ProcessTeacher(
        assignment_process_id=process.id,
        teacher_profile_id=profile.id,
        base_weekly_hours=base_weekly_hours,
        extra_weekly_hours=extra_weekly_hours,
        status=status,
        selection_position=selection_position,
        selection_points=selection_points,
        selection_criteria_label=selection_criteria_label,
        selection_notes=selection_notes,
        order_locked=order_locked,
        participates_in_selection=participates_in_selection,
    )
    session.add(pt)
    session.commit()
    session.refresh(pt)
    return pt


def make_subject(
    session: Session,
    process: AssignmentProcess,
    *,
    name: str = "Mathematics",
    allocation_category: SubjectAllocationCategory = SubjectAllocationCategory.MAIN,
    activity_type: ActivityType = ActivityType.ORDINARY,
    default_group_weekly_hours: float | None = None,
    default_teacher_weekly_hours_per_position: float | None = None,
    default_required_teacher_count: int = 1,
    allows_multiple_groups: bool = False,
    allows_zero_groups: bool = False,
) -> Subject:
    subject = Subject(
        assignment_process_id=process.id,
        name=name,
        allocation_category=allocation_category,
        activity_type=activity_type,
        default_group_weekly_hours=default_group_weekly_hours,
        default_teacher_weekly_hours_per_position=(
            default_teacher_weekly_hours_per_position
        ),
        default_required_teacher_count=default_required_teacher_count,
        allows_multiple_groups=allows_multiple_groups,
        allows_zero_groups=allows_zero_groups,
    )
    session.add(subject)
    session.commit()
    session.refresh(subject)
    return subject


def make_teaching_group(
    session: Session,
    process: AssignmentProcess,
    *,
    stage: str = "Secundaria",
    stage_label: str = "ESO",
    grade: int = 1,
    group_code: str = "A",
    label: Optional[str] = None,
) -> TeachingGroup:
    classroom_stage = make_classroom_stage(
        session,
        stage=stage,
        label=stage_label,
        min_grade=min(grade, 1),
        max_grade=max(grade, 4),
    )
    group = TeachingGroup(
        assignment_process_id=process.id,
        classroom_stage_id=classroom_stage.id,
        grade=grade,
        group_code=group_code,
        label=label or f"{grade}° {stage_label} {group_code}",
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def make_group_subject(
    session: Session,
    process: AssignmentProcess,
    group: TeachingGroup,
    subject: Subject,
    *,
    group_weekly_hours: float | None = None,
    teacher_weekly_hours_per_position: float | None = None,
    required_teacher_count: int = 1,
    active: bool = True,
    notes: Optional[str] = None,
) -> GroupSubject:
    group_subject = GroupSubject(
        assignment_process_id=process.id,
        teaching_group_id=group.id,
        subject_id=subject.id,
        group_weekly_hours=group_weekly_hours,
        teacher_weekly_hours_per_position=teacher_weekly_hours_per_position,
        required_teacher_count=required_teacher_count,
        active=active,
        notes=notes,
    )
    session.add(group_subject)
    session.commit()
    session.refresh(group_subject)
    return group_subject


def make_teaching_activity(
    session: Session,
    plan: TeachingPlan,
    subject: Subject,
    *,
    allocation_category: SubjectAllocationCategory = SubjectAllocationCategory.SECONDARY,
    activity_type: ActivityType = ActivityType.ORDINARY,
    group_weekly_hours_per_group: float = 2.0,
    teacher_weekly_hours_per_position: float = 2.0,
    required_teacher_count: int = 1,
    source: TeachingActivitySource = TeachingActivitySource.SECONDARY_MANUAL,
    source_group_subject_id: Optional[uuid.UUID] = None,
    sync_state: TeachingActivitySyncState = TeachingActivitySyncState.IN_SYNC,
    notes: Optional[str] = None,
    group_subjects: Optional[list[GroupSubject]] = None,
) -> TeachingActivity:
    activity = TeachingActivity(
        teaching_plan_id=plan.id,
        subject_id=subject.id,
        allocation_category=allocation_category,
        activity_type=activity_type,
        group_weekly_hours_per_group=group_weekly_hours_per_group,
        teacher_weekly_hours_per_position=teacher_weekly_hours_per_position,
        required_teacher_count=required_teacher_count,
        source=source,
        source_group_subject_id=source_group_subject_id,
        sync_state=sync_state,
        notes=notes,
    )
    session.add(activity)
    session.commit()
    session.refresh(activity)
    for cell in group_subjects or []:
        make_teaching_activity_group(session, activity, cell)
    return activity


def make_teaching_activity_group(
    session: Session,
    activity: TeachingActivity,
    group_subject: GroupSubject,
) -> TeachingActivityGroup:
    link = TeachingActivityGroup(
        teaching_activity_id=activity.id,
        group_subject_id=group_subject.id,
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


def make_classroom_stage(
    session: Session,
    *,
    stage: str = "Secundaria",
    label: str = "ESO",
    min_grade: int = 1,
    max_grade: int = 4,
) -> ClassroomStage:
    """Create or return a global classroom stage used by tests."""
    existing = session.exec(
        select(ClassroomStage).where(ClassroomStage.stage == stage)
    ).first()
    if existing is not None:
        return existing
    classroom_stage = ClassroomStage(
        stage=stage,
        label=label,
        min_grade=min_grade,
        max_grade=max_grade,
    )
    session.add(classroom_stage)
    session.commit()
    session.refresh(classroom_stage)
    return classroom_stage


def make_hour_requirement(
    session: Session,
    process: AssignmentProcess,
    activity: TeachingActivity,
    *,
    position_index: int = 0,
    required_teacher_hours: float = 4.0,
    created_generation: int = 1,
    last_validated_generation: int = 1,
    retired_generation: Optional[int] = None,
    superseded_by_requirement_id: Optional[uuid.UUID] = None,
    status: HourRequirementStatus = HourRequirementStatus.AVAILABLE,
) -> HourRequirement:
    """Insert one generated teacher-position slot (plan §5.9, §20.8)."""
    requirement = HourRequirement(
        assignment_process_id=process.id,
        teaching_activity_id=activity.id,
        position_index=position_index,
        required_teacher_hours=required_teacher_hours,
        created_generation=created_generation,
        last_validated_generation=last_validated_generation,
        retired_generation=retired_generation,
        superseded_by_requirement_id=superseded_by_requirement_id,
        status=status,
    )
    session.add(requirement)
    session.commit()
    session.refresh(requirement)
    return requirement


def make_assignment(
    session: Session,
    process: AssignmentProcess,
    requirement: HourRequirement,
    process_teacher: ProcessTeacher,
    *,
    source: AssignmentSource = AssignmentSource.DEPARTMENT_HEAD,
    status: AssignmentStatus = AssignmentStatus.ACTIVE,
    chosen_by_user_id: Optional[uuid.UUID] = None,
    confirmed_by_user_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
) -> Assignment:
    """Insert one complete-slot assignment (plan §5.10, §20.9).

    ``teaching_activity_id`` is denormalised from the requirement, matching the
    controller and the composite FK / active partial-unique indexes.
    """
    assignment = Assignment(
        assignment_process_id=process.id,
        hour_requirement_id=requirement.id,
        teaching_activity_id=requirement.teaching_activity_id,
        process_teacher_id=process_teacher.id,
        source=source,
        status=status,
        chosen_by_user_id=chosen_by_user_id or uuid.uuid4(),
        confirmed_by_user_id=confirmed_by_user_id,
        notes=notes,
    )
    session.add(assignment)
    session.commit()
    session.refresh(assignment)
    return assignment


def make_meeting_session(
    session: Session,
    process: AssignmentProcess,
    *,
    status: MeetingSessionStatus = MeetingSessionStatus.PREPARED,
    lan_access_enabled: bool = True,
    direct_teacher_selection_enabled: bool = False,
    selection_mode: SelectionOrderMode = SelectionOrderMode.NONE,
) -> MeetingSession:
    meeting_session = MeetingSession(
        assignment_process_id=process.id,
        status=status,
        lan_access_enabled=lan_access_enabled,
        direct_teacher_selection_enabled=direct_teacher_selection_enabled,
        selection_mode=selection_mode,
    )
    session.add(meeting_session)
    session.commit()
    session.refresh(meeting_session)
    return meeting_session


def make_selection_turn(
    session: Session,
    meeting_session: MeetingSession,
    process_teacher: ProcessTeacher,
    *,
    position: int = 0,
    status: SelectionTurnStatus = SelectionTurnStatus.PENDING,
) -> SelectionTurn:
    turn = SelectionTurn(
        meeting_session_id=meeting_session.id,
        process_teacher_id=process_teacher.id,
        position=position,
        status=status,
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)
    return turn
