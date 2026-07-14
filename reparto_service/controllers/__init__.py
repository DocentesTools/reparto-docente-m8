"""reparto_service domain controllers.

Each domain resource (academic year, school, department, process,
process teacher, subject, teaching group, hour requirement, assignment,
teacher profile, dashboard) has a ``*Controller`` class with static
methods. Route handlers are thin wrappers that delegate here and
translate ``HTTPException`` into the right status code.
"""

from .academic_years import AcademicYearController
from .assignment_processes import AssignmentProcessController
from .assignments import AssignmentController
from .base import DomainController
from .dashboard import DashboardController
from .department_hour_allocation_revisions import (
    DepartmentHourAllocationRevisionController,
)
from .departments import DepartmentController
from .hour_requirements import HourRequirementController
from .process_teachers import ProcessTeacherController
from .schools import SchoolController
from .subjects import SubjectController
from .teacher_profiles import TeacherProfileController
from .teaching_groups import TeachingGroupController
from .teaching_plans import TeachingPlanController

__all__ = [
    "AcademicYearController",
    "AssignmentController",
    "AssignmentProcessController",
    "DashboardController",
    "DepartmentController",
    "DepartmentHourAllocationRevisionController",
    "DomainController",
    "HourRequirementController",
    "ProcessTeacherController",
    "SchoolController",
    "SubjectController",
    "TeacherProfileController",
    "TeachingGroupController",
    "TeachingPlanController",
]
