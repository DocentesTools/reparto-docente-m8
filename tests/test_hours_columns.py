"""Every stored hour value is a ``NUMERIC(8, 2)`` column (plan §3.9).

The §3.9 sweep is the point at which ``HoursNumeric`` stopped being a utility
nothing used. What is proven here is that it stays that way: the hour columns
are enumerated from ``SQLModel.metadata`` rather than listed by hand, so a new
hour column declared as ``Float`` — or an existing one changed back — fails
here instead of quietly reintroducing binary floats into stored state.

The companion half (the canonical decimal-string API representation, and the
refusal of a binary float on input) is covered by the route tests and by
``test_core_decimals.py``.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Float
from sqlmodel import SQLModel

from reparto_service.core.decimals import (
    HOURS_DECIMAL_PLACES,
    HOURS_PRECISION,
    HoursNumeric,
)

#: Every hour-bearing column in the schema, as ``(table, column)`` pairs.
#: Listed explicitly *as well as* discovered below: the discovery proves no
#: hour column was missed, and this list proves the discovery still sees them.
EXPECTED_HOUR_COLUMNS = {
    ("reparto_assignment_process", "default_teacher_hours_reference"),
    ("reparto_department_hour_allocation_revision", "allocated_group_weekly_hours"),
    ("reparto_group_subject", "group_weekly_hours"),
    ("reparto_group_subject", "teacher_weekly_hours_per_position"),
    ("reparto_hour_requirement", "required_teacher_hours"),
    ("reparto_process_teacher", "base_weekly_hours"),
    ("reparto_process_teacher", "extra_weekly_hours"),
    ("reparto_subject", "default_group_weekly_hours"),
    ("reparto_subject", "default_teacher_weekly_hours_per_position"),
    ("reparto_teaching_activity", "group_weekly_hours_per_group"),
    ("reparto_teaching_activity", "teacher_weekly_hours_per_position"),
}


def _hours_columns() -> set[tuple[str, str]]:
    return {
        (table.name, column.name)
        for table in SQLModel.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, HoursNumeric)
    }


def test_every_hour_column_uses_the_canonical_numeric_type() -> None:
    assert _hours_columns() == EXPECTED_HOUR_COLUMNS


def test_hour_columns_are_numeric_8_2() -> None:
    for table_name, column_name in sorted(EXPECTED_HOUR_COLUMNS):
        column = SQLModel.metadata.tables[table_name].columns[column_name]
        assert column.type.impl.precision == HOURS_PRECISION, column_name
        assert column.type.impl.scale == HOURS_DECIMAL_PLACES, column_name
        assert column.type.impl.asdecimal is True, column_name


def test_no_hour_column_is_left_as_a_binary_float() -> None:
    """A `Float` column anywhere named for hours would be a regression."""
    offenders = [
        (table.name, column.name)
        for table in SQLModel.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Float) and "hours" in column.name
    ]
    assert offenders == []


def test_the_column_normalizes_whatever_the_caller_binds() -> None:
    """Storage is canonical regardless of how a value arrives.

    ``table=True`` models skip Pydantic validation by design, so the column is
    the last line of defence: an int, a string or a stray float all land as the
    same two-place ``Decimal``.
    """
    column = HoursNumeric()
    assert column.process_bind_param(3, None) == Decimal("3.00")
    assert column.process_bind_param("2.5", None) == Decimal("2.50")
    assert column.process_bind_param(2.5, None) == Decimal("2.50")
    assert column.process_bind_param(None, None) is None
