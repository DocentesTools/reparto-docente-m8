"""Stable metadata for service-authored teaching-plan stale reasons.

The public ``stale_reason`` string remains the compatibility fallback consumed
by existing clients and restorable snapshots.  New service-authored reasons
also persist one of the codes below plus language-neutral parameters so human
documents can translate them without recognizing historical English prose.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from reparto_service.core.errors import ScalarJsonValue


@dataclass(frozen=True)
class ServiceStaleReason:
    """A stable generated reason and its English compatibility fallback."""

    code: str
    message: str
    params: Mapping[str, ScalarJsonValue]


_NO_PARAMS: Mapping[str, ScalarJsonValue] = MappingProxyType({})

MAIN_GENERATED_ACTIVITY_OUT_OF_SYNC = ServiceStaleReason(
    code="document.stale_reason.main_generated_activity_out_of_sync",
    message="A MAIN_GENERATED activity is out of sync with its source.",
    params=_NO_PARAMS,
)
MAIN_GENERATED_ACTIVITY_VALUES_CHANGED = ServiceStaleReason(
    code="document.stale_reason.main_generated_activity_values_changed",
    message="MAIN_GENERATED activity values changed during source sync.",
    params=_NO_PARAMS,
)
TEACHING_ACTIVITY_RETIRED = ServiceStaleReason(
    code="document.stale_reason.teaching_activity_retired",
    message="A teaching activity was retired.",
    params=_NO_PARAMS,
)

SERVICE_STALE_REASONS: tuple[ServiceStaleReason, ...] = (
    MAIN_GENERATED_ACTIVITY_OUT_OF_SYNC,
    MAIN_GENERATED_ACTIVITY_VALUES_CHANGED,
    TEACHING_ACTIVITY_RETIRED,
)


__all__ = [
    "MAIN_GENERATED_ACTIVITY_OUT_OF_SYNC",
    "MAIN_GENERATED_ACTIVITY_VALUES_CHANGED",
    "SERVICE_STALE_REASONS",
    "ServiceStaleReason",
    "TEACHING_ACTIVITY_RETIRED",
]
