"""Edge-case tests for the decimal-hour normalization utilities (plan §3.9).

Exercises every layer of :mod:`reparto_service.core.decimals`: the strict
input validator, the rounding quantizer, the canonical string form, the
Pydantic annotated types (validation + JSON serialisation) and the SQLAlchemy
``HoursNumeric`` column, including a real SQLite round trip.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, select

from reparto_service.core.decimals import (
    HOURS_DECIMAL_PLACES,
    HOURS_PRECISION,
    HOURS_QUANTUM,
    HoursDecimal,
    HoursNumeric,
    InvalidHoursError,
    OptionalHoursDecimal,
    hours_from_str,
    hours_to_str,
    normalize_hours,
    quantize_hours,
)


# ── Constants ─────────────────────────────────────────────────────────────────


def test_constants() -> None:
    assert HOURS_DECIMAL_PLACES == 2
    assert HOURS_PRECISION == 8
    assert HOURS_QUANTUM == Decimal("0.01")


# ── normalize_hours: success cases ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2.5", "2.50"),
        ("2.50", "2.50"),
        ("2", "2.00"),
        (2, "2.00"),
        (0, "0.00"),
        ("0", "0.00"),
        (Decimal("2.5"), "2.50"),
        (Decimal("1.230"), "1.23"),  # trailing zero not counted as a 3rd place
        ("1234.56", "1234.56"),
        ("100", "100.00"),
        (Decimal("-0"), "0.00"),  # negative zero collapses to canonical zero
    ],
)
def test_normalize_hours_success(value: Decimal | int | str, expected: str) -> None:
    result = normalize_hours(value)
    assert isinstance(result, Decimal)
    assert str(result) == expected


# ── normalize_hours: rejections ───────────────────────────────────────────────


def test_normalize_hours_rejects_bool() -> None:
    with pytest.raises(InvalidHoursError, match="bool"):
        normalize_hours(True)  # type: ignore[arg-type]


def test_normalize_hours_rejects_float() -> None:
    with pytest.raises(InvalidHoursError, match="binary"):
        normalize_hours(2.5)  # type: ignore[arg-type]


def test_normalize_hours_rejects_unsupported_type() -> None:
    with pytest.raises(InvalidHoursError, match="Unsupported"):
        normalize_hours([2])  # type: ignore[arg-type]


def test_normalize_hours_rejects_garbage_string() -> None:
    with pytest.raises(InvalidHoursError, match="valid decimal"):
        normalize_hours("abc")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_normalize_hours_rejects_non_finite(value: str) -> None:
    with pytest.raises(InvalidHoursError, match="finite"):
        normalize_hours(value)


@pytest.mark.parametrize("value", ["-1", "-0.01", Decimal("-2.5")])
def test_normalize_hours_rejects_negative(value: Decimal | str) -> None:
    with pytest.raises(InvalidHoursError, match="non-negative"):
        normalize_hours(value)


@pytest.mark.parametrize("value", ["2.505", "0.001", Decimal("1.234")])
def test_normalize_hours_rejects_excess_places(value: Decimal | str) -> None:
    with pytest.raises(InvalidHoursError, match="at most 2 decimal"):
        normalize_hours(value)


# ── quantize_hours: rounding (never rejects) ──────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("2.005"), "2.01"),  # ROUND_HALF_UP
        (Decimal("2.994"), "2.99"),
        (Decimal("2.5"), "2.50"),
        (Decimal("-0.004"), "0.00"),  # negative-zero collapse
        (Decimal("0.000"), "0.00"),
    ],
)
def test_quantize_hours(value: Decimal, expected: str) -> None:
    assert str(quantize_hours(value)) == expected


# ── Canonical string helpers ──────────────────────────────────────────────────


def test_hours_to_str() -> None:
    assert hours_to_str(Decimal("2.5")) == "2.50"
    assert hours_to_str(3) == "3.00"


def test_hours_from_str() -> None:
    assert hours_from_str("2.50") == Decimal("2.50")
    assert isinstance(hours_from_str("2.50"), Decimal)


# ── Pydantic HoursDecimal ─────────────────────────────────────────────────────


class _RequiredModel(BaseModel):
    hours: HoursDecimal


class _OptionalModel(BaseModel):
    hours: OptionalHoursDecimal = None


def test_hours_decimal_coerces_and_normalises() -> None:
    model = _RequiredModel(hours="2.5")  # type: ignore[arg-type]
    assert model.hours == Decimal("2.50")
    # Python mode keeps a real Decimal for DB binding/arithmetic.
    assert model.model_dump()["hours"] == Decimal("2.50")
    # JSON mode emits the canonical decimal string.
    assert model.model_dump(mode="json")["hours"] == "2.50"
    assert '"hours":"2.50"' in model.model_dump_json()


def test_hours_decimal_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        _RequiredModel(hours="2.505")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _RequiredModel(hours=-1)  # type: ignore[arg-type]


def test_optional_hours_decimal() -> None:
    none_model = _OptionalModel()
    assert none_model.hours is None
    assert none_model.model_dump()["hours"] is None
    assert none_model.model_dump(mode="json")["hours"] is None

    # An explicitly-supplied None runs the validator (defaults are not validated).
    explicit_none = _OptionalModel(hours=None)
    assert explicit_none.hours is None

    value_model = _OptionalModel(hours="4")  # type: ignore[arg-type]
    assert value_model.hours == Decimal("4.00")
    assert value_model.model_dump(mode="json")["hours"] == "4.00"


# ── HoursNumeric column type ──────────────────────────────────────────────────


def test_hours_numeric_impl() -> None:
    col = HoursNumeric()
    assert col.impl.precision == HOURS_PRECISION
    assert col.impl.scale == HOURS_DECIMAL_PLACES


def test_hours_numeric_bind() -> None:
    col = HoursNumeric()
    assert col.process_bind_param(None, object()) is None
    assert col.process_bind_param(Decimal("2.005"), object()) == Decimal("2.01")
    # Non-Decimal binds go through the strict validator.
    assert col.process_bind_param(3, object()) == Decimal("3.00")
    assert col.process_bind_param("4.5", object()) == Decimal("4.50")


def test_hours_numeric_result() -> None:
    col = HoursNumeric()
    assert col.process_result_value(None, object()) is None
    # SQLite may hand back a float or a string; both coerce to a two-place Decimal.
    assert col.process_result_value(2.5, object()) == Decimal("2.50")
    assert col.process_result_value("2.5", object()) == Decimal("2.50")
    assert col.process_result_value(Decimal("2.5"), object()) == Decimal("2.50")


def test_hours_numeric_sqlite_round_trip() -> None:
    engine = create_engine("sqlite://")
    metadata = MetaData()
    table = Table(
        "hours_probe",
        metadata,
        Column("id", String, primary_key=True),
        Column("hours", HoursNumeric()),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(insert(table), {"id": "a", "hours": Decimal("2.5")})
        conn.execute(insert(table), {"id": "b", "hours": "1.239"})  # rounds to 1.24
        conn.execute(insert(table), {"id": "c", "hours": None})

    with engine.connect() as conn:
        rows = {
            row.id: row.hours
            for row in conn.execute(select(table).order_by(table.c.id))
        }

    assert rows["a"] == Decimal("2.50")
    assert isinstance(rows["a"], Decimal)
    assert rows["b"] == Decimal("1.24")
    assert rows["c"] is None
