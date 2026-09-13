"""Contract tests for validation codes and their language-neutral params."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from reparto_service.enums import ValidationSeverity
from reparto_service.schemas.planning import PlanValidationMessage
from reparto_service.schemas.validation_findings import VALIDATION_PARAM_CONTRACT
from reparto_service.services import validations


def _message(code: str, params: dict[str, str | int] | None) -> PlanValidationMessage:
    return PlanValidationMessage(
        severity=ValidationSeverity.BLOCKING,
        code=code,
        message="Compatibility fallback",
        params=params,
        entity_type="plan",
        entity_id=uuid.uuid4(),
    )


def test_contract_covers_all_17_exported_codes() -> None:
    exported_codes = {
        getattr(validations, name)
        for name in validations.__all__
        if name.startswith("CODE_")
    }
    assert len(exported_codes) == 17
    assert set(VALIDATION_PARAM_CONTRACT) == exported_codes


def test_known_params_are_exact_and_strict() -> None:
    valid = _message(
        validations.CODE_PARTICIPANT_OVER_TARGET,
        {
            "teacher_label": "Ada Lovelace",
            "assigned_hours": "20.25",
            "target_hours": "18.00",
            "difference_hours": "+2.25",
        },
    )
    assert valid.params is not None
    assert valid.params["difference_hours"] == "+2.25"

    with pytest.raises(ValidationError, match="Invalid parameter names"):
        _message(
            validations.CODE_PARTICIPANT_OVER_TARGET,
            {
                "teacher_label": "Ada Lovelace",
                "assigned_hours": "20.25",
                "target_hours": "18.00",
                "difference_hours": "+2.25",
                "teacher_id": str(uuid.uuid4()),
            },
        )
    with pytest.raises(ValidationError):
        _message(validations.CODE_REQUIREMENTS_UNASSIGNED, {"count": "1"})
    with pytest.raises(ValidationError):
        _message(validations.CODE_REQUIREMENTS_UNASSIGNED, {"count": True})
    with pytest.raises(ValidationError):
        _message(validations.CODE_ACTIVITY_MISSING_GROUPS, {"activity_label": 1})


def test_older_and_unknown_messages_remain_compatible() -> None:
    assert _message(validations.CODE_PARTICIPANT_BELOW_TARGET, None).params is None
    assert _message("future.additive_code", {"future": 1}).code == (
        "future.additive_code"
    )


@pytest.mark.parametrize(
    ("count", "difference"),
    [(0, "0.00"), (1, "+1.25"), (4, "-2.50")],
)
def test_contract_accepts_zero_one_many_and_signed_decimal_strings(
    count: int, difference: str
) -> None:
    assert _message(validations.CODE_REQUIREMENTS_UNASSIGNED, {"count": count}).params
    assert _message(
        validations.CODE_GROUP_HOURS_IMBALANCED,
        {
            "group_hours": "118.75",
            "allocation_hours": "120.00",
            "difference_hours": difference,
        },
    ).params
