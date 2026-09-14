"""Stable code and parameter contracts for service prose outside exceptions."""

from typing import Final


ServiceProseParam = str | int


CODE_GROUP_SUBJECT_INVERTED_GRADE_RANGE: Final = "group_subject.inverted_grade_range"
CODE_GROUP_SUBJECT_NO_ROW_TO_UPDATE: Final = "group_subject.no_row_to_update"

NON_EXCEPTION_PROSE_PARAM_CONTRACT: Final[dict[str, frozenset[str]]] = {
    CODE_GROUP_SUBJECT_INVERTED_GRADE_RANGE: frozenset(),
    CODE_GROUP_SUBJECT_NO_ROW_TO_UPDATE: frozenset(),
}


__all__ = [
    "CODE_GROUP_SUBJECT_INVERTED_GRADE_RANGE",
    "CODE_GROUP_SUBJECT_NO_ROW_TO_UPDATE",
    "NON_EXCEPTION_PROSE_PARAM_CONTRACT",
    "ServiceProseParam",
]
