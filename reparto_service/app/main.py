"""Domain router aggregator for reparto_service.

Wires the academic-year, school, department, teacher, requirement and
assignment routers under the consumer's API prefix declared in
``reparto_service.main`` (default ``/reparto``). Summary and dashboard
endpoints live on the parent assignment-processes router.

Authorization floor (plan §21.1/§21.4)
--------------------------------------
``require_reader`` is mounted here, on the aggregator, rather than repeated on
each router: a minimum-role floor that has to be remembered 21 times is a floor
with 21 ways to forget it. Every domain route — list, get, export and mutation
alike — therefore rejects an unauthenticated caller (401) and a ``USER``-role
caller (403) before its handler runs, and a router added later inherits the
floor by construction. Mutation routes add their own writer/department-head
gate on top; nothing below removes this one. The framework's ``/health``,
``/meta``, ``/ping`` and ``/metrics`` endpoints are mounted outside this
aggregator and keep their own visibility.
"""

from fastapi import APIRouter, Depends

from reparto_service.app.deps import require_reader
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
    planning_exchange,
    process_events,
    process_teachers,
    schools,
    selection_turns,
    subjects,
    teacher_profiles,
    teaching_activities,
    teaching_groups,
    teaching_plans,
)

api_router = APIRouter(dependencies=[Depends(require_reader)])
api_router.include_router(academic_years.router)
api_router.include_router(schools.router)
api_router.include_router(classroom_stages.router)
api_router.include_router(departments.router)
api_router.include_router(teacher_profiles.router)
api_router.include_router(assignment_processes.router)
api_router.include_router(process_events.router)
api_router.include_router(department_hour_allocation_revisions.router)
api_router.include_router(audit_events.router)
api_router.include_router(process_teachers.router)
api_router.include_router(subjects.router)
api_router.include_router(teaching_groups.router)
api_router.include_router(group_subjects.router)
api_router.include_router(teaching_plans.router)
api_router.include_router(teaching_activities.router)
api_router.include_router(hour_requirements.router)
api_router.include_router(planning_exchange.router)
api_router.include_router(assignments.router)
api_router.include_router(meeting_sessions.router)
api_router.include_router(selection_turns.router)
api_router.include_router(history.router)
