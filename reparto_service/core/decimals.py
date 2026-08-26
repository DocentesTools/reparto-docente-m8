"""Decimal-hour normalization utilities (plan §3.9).

Single source of truth for how the teaching-allocation domain represents weekly
hour values. Plan §3.9 mandates that every hour value:

* uses :class:`~decimal.Decimal` in Python (never binary ``float`` for a domain
  decision);
* uses ``NUMERIC(..., 2)`` in SQL;
* accepts any non-negative value with **at most two decimal places**;
* is exchanged over the API as a *canonical* decimal string such as ``"2.50"``;
* is normalized to two decimal places before any comparison.

This module provides four cooperating layers:

* :func:`normalize_hours` — parse-and-**validate** an input value (rejects
  ``float``/``bool``, negatives and >2-place inputs) into the canonical
  two-place :class:`~decimal.Decimal`.
* :func:`quantize_hours` — **round** an already-numeric ``Decimal`` (e.g. a
  computed load) to two places, used before comparison; unlike
  :func:`normalize_hours` it never rejects extra places, it rounds them.
* :class:`HoursNumeric` — the SQLAlchemy column type storing hours as
  ``NUMERIC(HOURS_PRECISION, 2)`` and returning a two-place ``Decimal`` on load.
* :data:`HoursDecimal` / :data:`OptionalHoursDecimal` — the Pydantic annotated
  types that validate request input and serialise the canonical decimal string
  in JSON responses while keeping a real ``Decimal`` in Python.

The fleet-wide switch of the existing ``float`` hour columns onto these helpers
is carried out by the individual model tasks; this module is the shared
foundation they build on.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Optional

from pydantic import BeforeValidator, PlainSerializer
from sqlalchemy import Numeric, TypeDecorator

# ── Canonical precision constants ─────────────────────────────────────────────

#: Number of decimal places every hour value is normalized to (plan §3.9).
HOURS_DECIMAL_PLACES: int = 2

#: Total number of significant digits for the SQL ``NUMERIC`` column. Weekly
#: hours and their department-level totals comfortably fit in ``999999.99``.
HOURS_PRECISION: int = 8

#: The two-place quantum every value is aligned to before comparison/storage.
HOURS_QUANTUM: Decimal = Decimal(1).scaleb(-HOURS_DECIMAL_PLACES)  # Decimal("0.01")


class InvalidHoursError(ValueError):
    """Raised when a value cannot be a canonical two-place non-negative hour."""


# ── Core normalization ────────────────────────────────────────────────────────


def _decimal_places(value: Decimal) -> int:
    """Return the count of *significant* decimal places in ``value``.

    Trailing zeros are not counted (``"1.230"`` has two places, not three) by
    normalising the value first; a value such as ``"2.505"`` keeps its third
    place and is therefore rejected upstream.
    """
    exponent = value.normalize().as_tuple().exponent
    # ``exponent`` is an ``int`` for finite decimals; special values (NaN/Inf)
    # yield a ``str`` sentinel but are rejected before this is ever called.
    if not isinstance(exponent, int):  # pragma: no cover - finite guaranteed
        return 0
    return max(0, -exponent)


def quantize_hours(value: Decimal) -> Decimal:
    """Round a ``Decimal`` to the canonical two decimal places (ROUND_HALF_UP).

    Use this for values produced by arithmetic (loads, totals, differences)
    before comparing or persisting them. Unlike :func:`normalize_hours` it does
    not validate the sign or the number of input places — it rounds. ``-0.00``
    is collapsed to ``0.00`` so the canonical string is never ``"-0.00"``.
    """
    result = value.quantize(HOURS_QUANTUM, rounding=ROUND_HALF_UP)
    if result == 0:
        # Avoid a negative-zero payload (e.g. from ``Decimal("-0.001")``).
        return Decimal("0").quantize(HOURS_QUANTUM)
    return result


def normalize_hours(value: Decimal | int | str) -> Decimal:
    """Validate and normalize an input hour value to a canonical two-place Decimal.

    Accepts a :class:`~decimal.Decimal`, an ``int`` or a decimal ``str`` (the
    canonical API form). ``float`` and ``bool`` are rejected outright: binary
    floats must never drive a domain decision (plan §3.9), and the API contract
    exchanges hours as decimal strings.

    Raises :class:`InvalidHoursError` when the value is not a finite number, is
    negative, or carries more than two decimal places (``"2.505"`` is rejected
    rather than silently rounded — use :func:`quantize_hours` to round).
    """
    # ``bool`` is an ``int`` subclass; reject it explicitly so ``True``/``False``
    # can never masquerade as ``1``/``0`` hours.
    if isinstance(value, bool):
        raise InvalidHoursError(f"Hour value must not be a bool: {value!r}.")
    if isinstance(value, float):
        raise InvalidHoursError(
            "Hour value must be a Decimal, int or decimal string, not a binary "
            f"float: {value!r}. Send hours as a canonical decimal string."
        )
    if not isinstance(value, (Decimal, int, str)):
        raise InvalidHoursError(f"Unsupported hour value type: {type(value).__name__}.")

    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(value)
    except InvalidOperation as exc:
        raise InvalidHoursError(f"Not a valid decimal hour value: {value!r}.") from exc

    if not decimal_value.is_finite():
        raise InvalidHoursError(f"Hour value must be finite: {value!r}.")
    if decimal_value < 0:
        raise InvalidHoursError(f"Hour value must be non-negative: {value!r}.")
    if _decimal_places(decimal_value) > HOURS_DECIMAL_PLACES:
        raise InvalidHoursError(
            f"Hour value must have at most {HOURS_DECIMAL_PLACES} decimal "
            f"places: {value!r}."
        )

    return quantize_hours(decimal_value)


# ── Canonical API string form ─────────────────────────────────────────────────


def hours_to_str(value: Decimal | int | str) -> str:
    """Render a value as the canonical two-place decimal string (``"2.50"``)."""
    return str(normalize_hours(value))


def hours_from_str(value: str) -> Decimal:
    """Parse a canonical decimal string into a normalized two-place ``Decimal``."""
    return normalize_hours(value)


# ── Optional-aware validators/serialisers for the Pydantic types ──────────────


def _normalize_optional(value: Decimal | int | str | None) -> Optional[Decimal]:
    if value is None:
        return None
    return normalize_hours(value)


def _to_str_optional(value: Decimal | None) -> Optional[str]:
    if value is None:
        return None
    return hours_to_str(value)


# ── Pydantic annotated types ──────────────────────────────────────────────────
#
# ``BeforeValidator`` runs the domain validation ahead of Pydantic's own Decimal
# coercion, so an out-of-contract value raises a ``ValidationError``. The
# ``PlainSerializer`` only fires in JSON mode: API responses carry the canonical
# string while ``model_dump()`` in Python mode keeps the real ``Decimal`` for
# database binding and arithmetic.

HoursDecimal = Annotated[
    Decimal,
    BeforeValidator(normalize_hours),
    PlainSerializer(hours_to_str, return_type=str, when_used="json"),
]
"""Required decimal-hour field: accepts ``"2.50"``/``2``/``Decimal``, emits ``"2.50"``."""

OptionalHoursDecimal = Annotated[
    Optional[Decimal],
    BeforeValidator(_normalize_optional),
    PlainSerializer(_to_str_optional, return_type=Optional[str], when_used="json"),
]
"""Optional decimal-hour field: ``None`` passes through untouched."""


# ── SQLAlchemy column type ────────────────────────────────────────────────────


class HoursNumeric(TypeDecorator[Decimal]):
    """``NUMERIC(HOURS_PRECISION, 2)`` column normalized to two-place ``Decimal``.

    Binds through :func:`quantize_hours` so any value written is rounded to the
    canonical two places, and loads back a two-place ``Decimal`` regardless of
    how the dialect returns the number (SQLite hands back a ``float``/``str``,
    which is coerced via its string form to avoid binary error). Binding never
    rejects — contract validation is the Pydantic boundary's job
    (:data:`HoursDecimal`); the column only guarantees canonical storage.
    """

    impl = Numeric(
        precision=HOURS_PRECISION, scale=HOURS_DECIMAL_PLACES, asdecimal=True
    )
    cache_ok = True

    def process_bind_param(
        self, value: Decimal | int | str | None, dialect: object
    ) -> Decimal | None:
        if value is None:
            return None
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
        return quantize_hours(decimal_value)

    def process_result_value(self, value: object, dialect: object) -> Decimal | None:
        if value is None:
            return None
        return quantize_hours(Decimal(str(value)))


__all__ = [
    "HOURS_DECIMAL_PLACES",
    "HOURS_PRECISION",
    "HOURS_QUANTUM",
    "HoursDecimal",
    "HoursNumeric",
    "InvalidHoursError",
    "OptionalHoursDecimal",
    "hours_from_str",
    "hours_to_str",
    "normalize_hours",
    "quantize_hours",
]
