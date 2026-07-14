"""Domain router aggregator for reparto_service.

Wires the academic-year, school, department, teacher, requirement and
assignment routers under the consumer's API prefix declared in
``reparto_service.main`` (default ``/reparto``). Summary and dashboard
endpoints live on the parent assignment-processes router.
"""

from fastapi import APIRouter

from reparto_service.app.routes import (
    academic_years,
    assignment_processes,
    assignments,
    audit_events,
    classroom_stages,
    department_hour_allocation_revisions,
    departments,
    group_subjects,
    history,
    hour_requirements,
    meeting_sessions,
    process_teachers,
    selection_turns,
    schools,
    subjects,
    teacher_profiles,
    teaching_groups,
    teaching_plans,
)

api_router = APIRouter()
api_router.include_router(academic_years.router)
api_router.include_router(schools.router)
api_router.include_router(classroom_stages.router)
api_router.include_router(departments.router)
api_router.include_router(teacher_profiles.router)
api_router.include_router(assignment_processes.router)
api_router.include_router(department_hour_allocation_revisions.router)
api_router.include_router(audit_events.router)
api_router.include_router(process_teachers.router)
api_router.include_router(subjects.router)
api_router.include_router(teaching_groups.router)
api_router.include_router(group_subjects.router)
api_router.include_router(teaching_plans.router)
api_router.include_router(hour_requirements.router)
api_router.include_router(assignments.router)
api_router.include_router(meeting_sessions.router)
api_router.include_router(selection_turns.router)
api_router.include_router(history.router)
