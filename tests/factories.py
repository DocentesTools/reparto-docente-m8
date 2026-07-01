"""Domain fixture factories used across the reparto_service test suite.

The factories return inserted rows. They are not pytest fixtures
themselves — call them from a test or a wrapper fixture that needs the
row + a session.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from sqlmodel import Session

from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.assignments import Assignment
from reparto_service.db_models.departments import Department
from reparto_service.db_models.hour_requirements import HourRequirement
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.schools import School
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.enums import (
    AcademicYearStatus,
    AssignmentProcessStatus,
    AssignmentSource,
    AssignmentStatus,
    AssignmentType,
    ProcessTeacherStatus,
    RequirementType,
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
) -> ProcessTeacher:
    pt = ProcessTeacher(
        assignment_process_id=process.id,
        teacher_profile_id=profile.id,
        available_hours=available_hours,
        status=status,
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
    stage: str = "ESO",
    grade: int = 1,
    group_code: str = "A",
    label: Optional[str] = None,
) -> TeachingGroup:
    group = TeachingGroup(
        assignment_process_id=process.id,
        stage=stage,
        grade=grade,
        group_code=group_code,
        label=label or f"{grade} {stage} {group_code}",
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


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
