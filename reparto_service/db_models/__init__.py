"""reparto_service SQLModel table models.

Every domain table the FastAPI app and Alembic autogenerate know about
must be importable from this package so the SQLModel metadata is
populated before ``alembic upgrade`` runs and before any test engine
calls ``SQLModel.metadata.create_all``.
"""

from .academic_years import (
    AcademicYear,
    AcademicYearCreate,
    AcademicYearPublic,
    AcademicYearsPublic,
    AcademicYearUpdate,
)
from .assignment_processes import (
    AssignmentProcess,
    AssignmentProcessCreate,
    AssignmentProcessPublic,
    AssignmentProcessesPublic,
    AssignmentProcessUpdate,
    ProcessCopyRequest,
    ProcessReopenRequest,
    ProcessTransitionRequest,
)
from .assignments import (
    Assignment,
    AssignmentCreate,
    AssignmentPublic,
    AssignmentsPublic,
    AssignmentUpdate,
)
from .departments import (
    Department,
    DepartmentCreate,
    DepartmentGenerators,
    DepartmentPublic,
    DepartmentsPublic,
    DepartmentUpdate,
)
from .hour_requirements import (
    HourRequirement,
    HourRequirementCreate,
    HourRequirementPublic,
    HourRequirementsPublic,
    HourRequirementUpdate,
)
from .meeting_sessions import (
    MeetingSession,
    MeetingSessionCreate,
    MeetingSessionPublic,
    MeetingSessionsPublic,
    MeetingSessionUpdate,
)
from .selection_turns import (
    SelectionTurn,
    SelectionTurnAction,
    SelectionTurnComplete,
    SelectionTurnCreate,
    SelectionTurnPublic,
    SelectionTurnsPublic,
)
from .process_teachers import (
    ProcessTeacher,
    ProcessTeacherCreate,
    ProcessTeacherPublic,
    ProcessTeachersPublic,
    ProcessTeacherUpdate,
)
from .schools import (
    School,
    SchoolCreate,
    SchoolPublic,
    SchoolsPublic,
    SchoolUpdate,
)
from .subjects import (
    Subject,
    SubjectCreate,
    SubjectPublic,
    SubjectsPublic,
    SubjectUpdate,
)
from .teacher_profiles import (
    TeacherProfile,
    TeacherProfileCreate,
    TeacherProfilePublic,
    TeacherProfilesPublic,
    TeacherProfileUpdate,
)
from .teaching_groups import (
    TeachingGroup,
    TeachingGroupCreate,
    TeachingGroupPublic,
    TeachingGroupsPublic,
    TeachingGroupUpdate,
)

__all__ = [
    "AcademicYear",
    "AcademicYearCreate",
    "AcademicYearPublic",
    "AcademicYearUpdate",
    "AcademicYearsPublic",
    "Assignment",
    "AssignmentCreate",
    "AssignmentProcess",
    "AssignmentProcessCreate",
    "AssignmentProcessPublic",
    "AssignmentProcessUpdate",
    "AssignmentProcessesPublic",
    "AssignmentPublic",
    "AssignmentUpdate",
    "AssignmentsPublic",
    "Department",
    "DepartmentCreate",
    "DepartmentGenerators",
    "DepartmentPublic",
    "DepartmentUpdate",
    "DepartmentsPublic",
    "HourRequirement",
    "HourRequirementCreate",
    "HourRequirementPublic",
    "HourRequirementUpdate",
    "HourRequirementsPublic",
    "MeetingSession",
    "MeetingSessionCreate",
    "MeetingSessionPublic",
    "MeetingSessionUpdate",
    "MeetingSessionsPublic",
    "ProcessCopyRequest",
    "ProcessReopenRequest",
    "ProcessTeacher",
    "ProcessTeacherCreate",
    "ProcessTeacherPublic",
    "ProcessTeacherUpdate",
    "ProcessTeachersPublic",
    "ProcessTransitionRequest",
    "SelectionTurn",
    "SelectionTurnAction",
    "SelectionTurnComplete",
    "SelectionTurnCreate",
    "SelectionTurnPublic",
    "SelectionTurnsPublic",
    "School",
    "SchoolCreate",
    "SchoolPublic",
    "SchoolUpdate",
    "SchoolsPublic",
    "Subject",
    "SubjectCreate",
    "SubjectPublic",
    "SubjectUpdate",
    "SubjectsPublic",
    "TeacherProfile",
    "TeacherProfileCreate",
    "TeacherProfilePublic",
    "TeacherProfileUpdate",
    "TeacherProfilesPublic",
    "TeachingGroup",
    "TeachingGroupCreate",
    "TeachingGroupPublic",
    "TeachingGroupUpdate",
    "TeachingGroupsPublic",
]
