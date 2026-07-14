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
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.meeting_sessions import MeetingSession
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.selection_turns import SelectionTurn
from reparto_service.db_models.schools import School
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.db_models.teaching_plans import TeachingPlan
from reparto_service.enums import (
    AcademicYearStatus,
    AssignmentProcessStatus,
    AssignmentSource,
    AssignmentStatus,
    AssignmentType,
    DepartmentHourAllocationSource,
    FeasibilityStatus,
    MeetingSessionStatus,
    ProcessTeacherStatus,
    RequirementType,
    SelectionOrderMode,
    SelectionTurnStatus,
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
    available_hours: float = 18.0,
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
        available_hours=available_hours,
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
) -> Subject:
    subject = Subject(
        assignment_process_id=process.id,
        name=name,
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
    group: TeachingGroup,
    subject: Subject,
    *,
    required_hours: float = 4.0,
    requirement_type: RequirementType = RequirementType.ORDINARY,
) -> HourRequirement:
    requirement = HourRequirement(
        assignment_process_id=process.id,
        teaching_group_id=group.id,
        subject_id=subject.id,
        required_hours=required_hours,
        requirement_type=requirement_type,
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
    assigned_hours: float = 4.0,
    assignment_type: AssignmentType = AssignmentType.MAIN,
    source: AssignmentSource = AssignmentSource.DEPARTMENT_HEAD,
    status: AssignmentStatus = AssignmentStatus.CONFIRMED,
    chosen_by_user_id: Optional[uuid.UUID] = None,
    override_reason: Optional[str] = None,
) -> Assignment:
    assignment = Assignment(
        assignment_process_id=process.id,
        hour_requirement_id=requirement.id,
        process_teacher_id=process_teacher.id,
        assigned_hours=assigned_hours,
        assignment_type=assignment_type,
        source=source,
        status=status,
        chosen_by_user_id=chosen_by_user_id or uuid.uuid4(),
        override_reason=override_reason,
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
