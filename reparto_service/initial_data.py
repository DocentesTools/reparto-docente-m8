"""The worked configuration example seeded into an empty development database.

``docs/ARCHITECTURE.md`` §3.2 resets development databases destructively rather
than migrating them forward, so a fresh Compose bootstrap comes up with an empty
domain. That is correct for a deployment and useless for a walk-through: the
three stages only mean something over a department's worth of groups, subjects
and teachers, and the two balances only mean something when they are exact.

This module inserts that department — **stage 1 only**. It configures the
process, the group/subject matrix, the participants and the leadership hour
allocation, and stops there. It creates no teaching plan, no activity, no
requirement and no assignment, because stages 2 and 3 are what an operator
walks: materialising the main activities, adding the secondary ones and locking
the plan is the demonstration, not the fixture.

Configured that way, the example lands on the plan §3.2 co-teaching numbers
exactly::

    116 h  main activities        (materialised, one per active main cell)
    +  2 h  two tutoring activities  (1 h each, two positions each)
    +  2 h  one co-teaching activity (2 h, two positions)
    ─────
    120 h  group load             = the allocated 120 h        → balanced
    124 h  teacher load           = the six participants' 124 h → balanced

Both totals are correct at once and are never summed (``docs/ARCHITECTURE.md``
§5). ``tests/test_initial_data.py`` completes the two stages over this seed and
asserts those numbers, so the arithmetic above is gated rather than claimed.

Seeding is opt-in (``SEED_EXAMPLE_DATA``) and runs only when the domain is
empty, so no deployment ever invents rows and a second Compose start is a no-op.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

from sqlmodel import Session, func, select

from reparto_service.core.config import settings
from reparto_service.core.deps import engine
from reparto_service.db_models.academic_years import AcademicYear
from reparto_service.db_models.assignment_processes import AssignmentProcess
from reparto_service.db_models.classroom_stages import ClassroomStage
from reparto_service.db_models.department_hour_allocation_revisions import (
    DepartmentHourAllocationRevision,
)
from reparto_service.db_models.departments import Department
from reparto_service.db_models.group_subjects import GroupSubject
from reparto_service.db_models.process_teachers import ProcessTeacher
from reparto_service.db_models.schools import School
from reparto_service.db_models.subjects import Subject
from reparto_service.db_models.teacher_profiles import TeacherProfile
from reparto_service.db_models.teaching_groups import TeachingGroup
from reparto_service.enums import (
    AcademicYearStatus,
    ActivityType,
    AssignmentProcessStatus,
    DepartmentHourAllocationSource,
    ProcessTeacherStatus,
    SubjectAllocationCategory,
)

logger = logging.getLogger(__name__)

# ── Identity ─────────────────────────────────────────────────────────────────

#: Author recorded on every seeded row. A UUID5 in the DNS namespace, so it is
#: stable across resets and recognisable as synthetic: the seed never claims to
#: be one of the issuer's real users, and ``TeacherProfile.user_id`` is left
#: unset so a profile stays unclaimed until a real account is linked to it.
SEED_USER_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "seed.reparto-docente-m8.example")

SCHOOL_NAME = "IES Ejemplo M8"
DEPARTMENT_NAME = "Matemáticas"
ACADEMIC_YEAR_LABEL = "2026/2027"

# ── Stage 1 configuration ────────────────────────────────────────────────────

#: ``(stage, label, min_grade, max_grade)`` — global, shared by every process.
CLASSROOM_STAGES: tuple[tuple[str, str, int, int], ...] = (
    ("Secundaria", "ESO", 1, 4),
    ("Bachillerato", "BAC", 1, 2),
)

#: ``(stage, grade, group_code)``. The two ``DIV`` groups are the diversification
#: programme: they take the single ``Ámbito`` subject instead of the ordinary
#: one, which is why the matrix below is not a full rectangle.
TEACHING_GROUPS: tuple[tuple[str, int, str], ...] = (
    ("Secundaria", 1, "A"),
    ("Secundaria", 1, "B"),
    ("Secundaria", 1, "C"),
    ("Secundaria", 2, "A"),
    ("Secundaria", 2, "B"),
    ("Secundaria", 2, "C"),
    ("Secundaria", 3, "A"),
    ("Secundaria", 3, "B"),
    ("Secundaria", 3, "C"),
    ("Secundaria", 3, "DIV"),
    ("Secundaria", 4, "A"),
    ("Secundaria", 4, "B"),
    ("Secundaria", 4, "DIV"),
    ("Bachillerato", 1, "A"),
    ("Bachillerato", 1, "B"),
    ("Bachillerato", 2, "A"),
    ("Bachillerato", 2, "B"),
)

#: ``(name, category, activity_type, default_group_hours, positions, groups)``.
#:
#: ``default_group_hours`` is also the per-position teacher default: an ordinary
#: subject costs a group and its teacher the same hours. The group-subject cells
#: are created **without** their own hour values so they resolve through these
#: defaults, which is the documented behaviour worth demonstrating; only the
#: position count is carried per cell, because it has no subject-level fallback.
#:
#: The three ``SECONDARY`` rows are what makes the two balances differ: each
#: costs its groups one lot of hours and its *two* teachers two lots.
SUBJECTS: tuple[
    tuple[str, SubjectAllocationCategory, ActivityType, float, int, tuple[str, ...]],
    ...,
] = (
    # ── Main: 116 group hours, and the same 116 teacher hours ────────────────
    (
        "Matemáticas",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        4.0,
        1,
        (
            "1º ESO A",
            "1º ESO B",
            "1º ESO C",
            "2º ESO A",
            "2º ESO B",
            "2º ESO C",
            "3º ESO A",
            "3º ESO B",
            "3º ESO C",
        ),
    ),
    (
        "Matemáticas B (Académicas)",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        4.0,
        1,
        ("4º ESO A",),
    ),
    (
        "Matemáticas A (Aplicadas)",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        4.0,
        1,
        ("4º ESO B",),
    ),
    (
        "Ámbito Científico-Matemático",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        8.0,
        1,
        ("3º ESO DIV", "4º ESO DIV"),
    ),
    (
        "Refuerzo de Matemáticas",
        SubjectAllocationCategory.MAIN,
        ActivityType.SUPPORT,
        2.0,
        1,
        ("1º ESO A", "1º ESO B", "2º ESO A", "2º ESO B"),
    ),
    (
        "Taller de Matemáticas",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        2.0,
        1,
        ("3º ESO A", "3º ESO B", "3º ESO C"),
    ),
    (
        "Ampliación de Matemáticas",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        2.0,
        1,
        ("4º ESO A",),
    ),
    (
        "Matemáticas I",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        4.0,
        1,
        ("1º BAC A", "1º BAC B"),
    ),
    (
        "Matemáticas Aplicadas a las CCSS I",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        4.0,
        1,
        ("1º BAC A", "1º BAC B"),
    ),
    (
        "Matemáticas II",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        4.0,
        1,
        ("2º BAC A", "2º BAC B"),
    ),
    (
        "Matemáticas Aplicadas a las CCSS II",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        4.0,
        1,
        ("2º BAC A", "2º BAC B"),
    ),
    (
        "Estadística",
        SubjectAllocationCategory.MAIN,
        ActivityType.ORDINARY,
        4.0,
        1,
        ("2º BAC A", "2º BAC B"),
    ),
    # ── Secondary: +4 group hours, +8 teacher hours ──────────────────────────
    (
        "Tutoría",
        SubjectAllocationCategory.SECONDARY,
        ActivityType.TUTORING,
        1.0,
        2,
        ("1º ESO A", "1º ESO B"),
    ),
    (
        "Docencia compartida",
        SubjectAllocationCategory.SECONDARY,
        ActivityType.CO_TEACHING,
        2.0,
        2,
        ("1º ESO C",),
    ),
)

#: ``(display_name, base_weekly_hours)``. Six participants at an ordinary
#: statutory load, summing to the 124 teacher hours the plan will cost. Nobody
#: carries authorized extra hours: raising a target is the audited
#: ``POST /teachers/{id}/extra-hours`` action, never a seeded value.
PARTICIPANTS: tuple[tuple[str, float], ...] = (
    ("Ana Ruiz Delgado", 21.0),
    ("Beto Salas Marín", 21.0),
    ("Carmen Ortiz Vela", 21.0),
    ("Diego Peña Lara", 21.0),
    ("Elena Nieto Cruz", 20.0),
    ("Fernando Gil Rojas", 20.0),
)

#: The hours leadership allocated to the department — the group-load target.
ALLOCATED_GROUP_WEEKLY_HOURS = 120.0

#: What the seeded configuration costs once both stages are completed. Asserted
#: by ``tests/test_initial_data.py`` against the calculation service.
EXPECTED_GROUP_LOAD = 120.0
EXPECTED_TEACHER_LOAD = 124.0


def _group_label(stage_label: str, grade: int, group_code: str) -> str:
    """Return the display label a teaching group is identified by."""
    return f"{grade}º {stage_label} {group_code}"


def _seed_classroom_stages(session: Session) -> dict[str, ClassroomStage]:
    """Insert the global stages, reusing any that already exist.

    Stages are global rather than process-scoped and carry a unique ``stage``,
    so a second seeded process must adopt the existing rows.
    """
    stages: dict[str, ClassroomStage] = {}
    for stage, label, min_grade, max_grade in CLASSROOM_STAGES:
        existing = session.exec(
            select(ClassroomStage).where(ClassroomStage.stage == stage)
        ).first()
        if existing is None:
            existing = ClassroomStage(
                stage=stage, label=label, min_grade=min_grade, max_grade=max_grade
            )
            session.add(existing)
            session.flush()
        stages[stage] = existing
    return stages


def _seed_process(session: Session) -> AssignmentProcess:
    """Insert the school, department, academic year and the process itself."""
    school = School(
        name=SCHOOL_NAME,
        locality="Sevilla",
        province="Sevilla",
        region="Andalucía",
        notes="Centro de ejemplo creado por reparto_service.initial_data.",
    )
    session.add(school)
    session.flush()

    department = Department(
        school_id=school.id,
        name=DEPARTMENT_NAME,
        slug="matematicas",
    )
    academic_year = AcademicYear(
        label=ACADEMIC_YEAR_LABEL,
        start_date=date(2026, 9, 1),
        end_date=date(2027, 6, 30),
        status=AcademicYearStatus.ACTIVE,
        school_id=school.id,
        created_by_user_id=SEED_USER_ID,
    )
    session.add(department)
    session.add(academic_year)
    session.flush()

    process = AssignmentProcess(
        academic_year_id=academic_year.id,
        school_id=school.id,
        department_id=department.id,
        status=AssignmentProcessStatus.DRAFT,
        created_by_user_id=SEED_USER_ID,
    )
    session.add(process)
    session.flush()
    return process


def _seed_matrix(
    session: Session,
    process: AssignmentProcess,
    stages: dict[str, ClassroomStage],
) -> None:
    """Insert the teaching groups, the subjects and the cells linking them."""
    stage_labels = {stage: label for stage, label, _, _ in CLASSROOM_STAGES}
    groups: dict[str, TeachingGroup] = {}
    for stage, grade, group_code in TEACHING_GROUPS:
        label = _group_label(stage_labels[stage], grade, group_code)
        group = TeachingGroup(
            assignment_process_id=process.id,
            classroom_stage_id=stages[stage].id,
            grade=grade,
            group_code=group_code,
            label=label,
        )
        session.add(group)
        groups[label] = group
    session.flush()

    for name, category, activity_type, hours, positions, labels in SUBJECTS:
        subject = Subject(
            assignment_process_id=process.id,
            name=name,
            allocation_category=category,
            activity_type=activity_type,
            default_group_weekly_hours=hours,
            default_teacher_weekly_hours_per_position=hours,
            default_required_teacher_count=positions,
        )
        session.add(subject)
        session.flush()
        for label in labels:
            session.add(
                GroupSubject(
                    assignment_process_id=process.id,
                    teaching_group_id=groups[label].id,
                    subject_id=subject.id,
                    required_teacher_count=positions,
                    active=True,
                )
            )
    session.flush()


def _seed_participants_and_allocation(
    session: Session, process: AssignmentProcess
) -> None:
    """Insert the six participants and the leadership hour allocation."""
    for display_name, base_weekly_hours in PARTICIPANTS:
        profile = TeacherProfile(display_name=display_name, active=True)
        session.add(profile)
        session.flush()
        session.add(
            ProcessTeacher(
                assignment_process_id=process.id,
                teacher_profile_id=profile.id,
                base_weekly_hours=base_weekly_hours,
                extra_weekly_hours=0.0,
                status=ProcessTeacherStatus.ACTIVE,
                participates_in_selection=True,
            )
        )

    session.add(
        DepartmentHourAllocationRevision(
            assignment_process_id=process.id,
            revision_number=1,
            allocated_group_weekly_hours=ALLOCATED_GROUP_WEEKLY_HOURS,
            reason="Reparto inicial de la jefatura de estudios (ejemplo).",
            source=DepartmentHourAllocationSource.MANUAL_TRANSCRIPTION,
            created_by_user_id=SEED_USER_ID,
        )
    )
    session.flush()


def domain_is_empty(session: Session) -> bool:
    """Return whether the domain holds no assignment process at all.

    The process is the root every seeded row hangs off, so its absence is the
    one condition under which inserting the example cannot collide with, or
    quietly extend, data somebody else put there.
    """
    return session.exec(select(func.count()).select_from(AssignmentProcess)).one() == 0


def seed_example_configuration(session: Session) -> AssignmentProcess | None:
    """Insert the stage-1 example, or return ``None`` if the domain is not empty.

    One transaction: a partially seeded process would be worse than none, since
    the matrix is only exact as a whole.
    """
    if not domain_is_empty(session):
        return None

    stages = _seed_classroom_stages(session)
    process = _seed_process(session)
    _seed_matrix(session, process, stages)
    _seed_participants_and_allocation(session, process)
    session.commit()
    session.refresh(process)
    return process


def main() -> None:
    """Entry point used by ``scripts/pre_start.sh`` after the migrations run."""
    if not settings.SEED_EXAMPLE_DATA:
        logger.info("SEED_EXAMPLE_DATA is off — leaving the domain untouched.")
        return

    with engine.session() as session:
        process = seed_example_configuration(session)

    if process is None:
        logger.info("Domain already holds a process — nothing seeded.")
    else:
        logger.info("Seeded the %s example process %s.", DEPARTMENT_NAME, process.id)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
