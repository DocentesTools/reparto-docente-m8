"""Stable codes and substitution contracts for validation findings.

The HTTP message vocabulary stays open for additive service codes, while the
17 codes currently authored by this service have exact parameter names and
JSON scalar kinds. Decimal hours are canonical signed strings; counts are JSON
integers. Human labels are distinct from the machine ``entity_id`` field.
"""

from typing import Final, Literal

ValidationParamKind = Literal["integer", "string"]

CODE_MISSING_ALLOCATION: Final = "plan.missing_allocation"
CODE_GROUP_HOURS_IMBALANCED: Final = "plan.group_hours_imbalanced"
CODE_TEACHER_LOAD_IMBALANCED: Final = "plan.teacher_load_imbalanced"
CODE_MAIN_SUBJECT_NOT_MATERIALIZED: Final = "plan.main_subject_not_materialized"
CODE_ACTIVITY_MISSING_GROUPS: Final = "activity.missing_groups"
CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED: Final = (
    "activity.multiple_groups_not_allowed"
)
CODE_ACTIVITY_LINKED_SUBJECT_MISMATCH: Final = "activity.linked_subject_mismatch"
CODE_ACTIVITY_OUT_OF_SYNC: Final = "activity.out_of_sync"
CODE_REQUIREMENTS_NOT_GENERATED: Final = "plan.requirements_not_generated"
CODE_REQUIREMENTS_STALE: Final = "requirement.stale"
CODE_PLAN_STALE: Final = "plan.stale"
CODE_FEASIBILITY_NOT_CONFIRMED: Final = "plan.feasibility_not_confirmed"
CODE_PARTICIPANT_OVERLOADED: Final = "teacher.overloaded_authorized"
CODE_SECONDARY_ACTIVITIES_AVAILABLE: Final = "plan.secondary_activities_available"
CODE_REQUIREMENTS_UNASSIGNED: Final = "requirement.unassigned"
CODE_PARTICIPANT_OVER_TARGET: Final = "participant.over_target"
CODE_PARTICIPANT_BELOW_TARGET: Final = "participant.below_target"

VALIDATION_PARAM_CONTRACT: Final[dict[str, dict[str, ValidationParamKind]]] = {
    CODE_MISSING_ALLOCATION: {},
    CODE_GROUP_HOURS_IMBALANCED: {
        "group_hours": "string",
        "allocation_hours": "string",
        "difference_hours": "string",
    },
    CODE_TEACHER_LOAD_IMBALANCED: {
        "teacher_hours": "string",
        "target_hours": "string",
        "difference_hours": "string",
    },
    CODE_MAIN_SUBJECT_NOT_MATERIALIZED: {
        "group_label": "string",
        "subject_label": "string",
    },
    CODE_ACTIVITY_MISSING_GROUPS: {"activity_label": "string"},
    CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED: {
        "activity_label": "string",
        "group_count": "integer",
    },
    CODE_ACTIVITY_LINKED_SUBJECT_MISMATCH: {"activity_label": "string"},
    CODE_ACTIVITY_OUT_OF_SYNC: {"activity_label": "string"},
    CODE_REQUIREMENTS_NOT_GENERATED: {},
    CODE_REQUIREMENTS_STALE: {"count": "integer"},
    CODE_PLAN_STALE: {"status": "string"},
    CODE_FEASIBILITY_NOT_CONFIRMED: {"status": "string"},
    CODE_PARTICIPANT_OVERLOADED: {
        "teacher_label": "string",
        "extra_hours": "string",
    },
    CODE_SECONDARY_ACTIVITIES_AVAILABLE: {"count": "integer"},
    CODE_REQUIREMENTS_UNASSIGNED: {"count": "integer"},
    CODE_PARTICIPANT_OVER_TARGET: {
        "teacher_label": "string",
        "assigned_hours": "string",
        "target_hours": "string",
        "difference_hours": "string",
    },
    CODE_PARTICIPANT_BELOW_TARGET: {
        "teacher_label": "string",
        "assigned_hours": "string",
        "target_hours": "string",
        "difference_hours": "string",
    },
}


__all__ = [
    "CODE_ACTIVITY_LINKED_SUBJECT_MISMATCH",
    "CODE_ACTIVITY_MISSING_GROUPS",
    "CODE_ACTIVITY_MULTIPLE_GROUPS_NOT_ALLOWED",
    "CODE_ACTIVITY_OUT_OF_SYNC",
    "CODE_FEASIBILITY_NOT_CONFIRMED",
    "CODE_GROUP_HOURS_IMBALANCED",
    "CODE_MAIN_SUBJECT_NOT_MATERIALIZED",
    "CODE_MISSING_ALLOCATION",
    "CODE_PARTICIPANT_BELOW_TARGET",
    "CODE_PARTICIPANT_OVERLOADED",
    "CODE_PARTICIPANT_OVER_TARGET",
    "CODE_PLAN_STALE",
    "CODE_REQUIREMENTS_NOT_GENERATED",
    "CODE_REQUIREMENTS_STALE",
    "CODE_REQUIREMENTS_UNASSIGNED",
    "CODE_SECONDARY_ACTIVITIES_AVAILABLE",
    "CODE_TEACHER_LOAD_IMBALANCED",
    "VALIDATION_PARAM_CONTRACT",
    "ValidationParamKind",
]
