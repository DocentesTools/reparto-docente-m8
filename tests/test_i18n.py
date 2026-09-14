"""C10 locale negotiation, isolation, catalog, and boundary tests."""

from __future__ import annotations

import asyncio
import gettext
import json
import uuid
from pathlib import Path
from string import Formatter
from typing import cast

import httpx
import pytest
from babel.messages import mofile, pofile
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.types import HTTPExceptionHandler

from reparto_service.core import i18n
from reparto_service.core.errors import DomainHTTPException
from reparto_service.core.i18n import (
    GETTEXT_DOMAIN,
    LOCALE_DIR,
    MAX_ACCEPT_LANGUAGE_BYTES,
    PLURAL_COUNT_PARAMS,
    LocaleMiddleware,
    current_locale,
    domain_http_exception_handler,
    negotiate_locale,
    reset_current_locale,
    set_current_locale,
    translate_domain_message,
)


ROOT = Path(__file__).parents[1]
CATALOG_PATH = ROOT / "docs" / "error-taxonomy.json"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "en"),
        ("", "en"),
        ("es-ES", "es"),
        ("fr-CA, es;q=0.9", "fr"),
        ("fr;q=0.2, es-MX;q=0.8, en;q=0.5", "es"),
        ("de, *;q=0.4", "en"),
        ("*;q=0.8, en;q=0", "es"),
        ("fr;q=0, es;q=0", "en"),
        ("not_a_locale", "en"),
        ("es;q=2", "en"),
        ("es;q=0.1234", "en"),
        ("es;q=0.5;level=1", "en"),
        ("es;level=1", "en"),
        ("invalid;q=broken, fr-FR;q=0.7", "fr"),
    ],
)
def test_accept_language_negotiation(header: str | None, expected: str) -> None:
    assert negotiate_locale(header) == expected


def test_accept_language_input_is_bounded() -> None:
    oversized = "es," + ("x" * MAX_ACCEPT_LANGUAGE_BYTES)
    assert negotiate_locale(oversized) == "en"


def test_context_token_resets_even_after_failure() -> None:
    outer = set_current_locale("fr")
    try:
        inner = set_current_locale("es")
        try:
            assert current_locale() == "es"
            raise RuntimeError("probe")
        except RuntimeError:
            pass
        finally:
            reset_current_locale(inner)
        assert current_locale() == "fr"
    finally:
        reset_current_locale(outer)
    assert current_locale() == "en"


def test_invalid_context_and_english_translation_use_the_default() -> None:
    token = set_current_locale("de")
    try:
        assert current_locale() == "en"
        assert translate_domain_message("code", "fallback", {}) == "fallback"
        assert (
            translate_domain_message("code", "fallback", {}, locale="de") == "fallback"
        )
    finally:
        reset_current_locale(token)


def _probe_app() -> FastAPI:
    probe = FastAPI()
    probe.add_middleware(LocaleMiddleware)
    probe.add_exception_handler(
        DomainHTTPException,
        cast(HTTPExceptionHandler, domain_http_exception_handler),
    )

    def reject_before_route() -> None:
        raise DomainHTTPException(
            status_code=409,
            code="base.no_teacher_profile_is_linked_auth_user",
            message="No teacher profile is linked to this auth user.",
            params={},
        )

    @probe.get("/dependency-error", dependencies=[Depends(reject_before_route)])
    async def dependency_error() -> None:
        raise AssertionError("dependency should have rejected the request")

    @probe.get("/locale")
    async def locale() -> dict[str, str]:
        await asyncio.sleep(0)
        return {"locale": current_locale()}

    return probe


def test_locale_is_available_before_dependencies_and_shapes_cache_headers() -> None:
    with TestClient(_probe_app()) as client:
        response = client.get("/dependency-error", headers={"Accept-Language": "es-ES"})

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "base.no_teacher_profile_is_linked_auth_user",
            "message": "No hay ningún perfil docente vinculado a este usuario.",
            "params": {},
        }
    }
    assert response.headers["content-language"] == "es"
    assert response.headers["vary"] == "accept-language"


@pytest.mark.anyio
async def test_concurrent_requests_do_not_leak_locale() -> None:
    app = _probe_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        spanish, french = await asyncio.gather(
            client.get("/locale", headers={"Accept-Language": "es"}),
            client.get("/locale", headers={"Accept-Language": "fr"}),
        )

    assert spanish.json() == {"locale": "es"}
    assert french.json() == {"locale": "fr"}
    assert current_locale() == "en"


def test_missing_catalog_msgid_and_parameter_drift_fall_back() -> None:
    default = "Assignment A not found in process P."
    code = "assignments.assignment_not_found_process"
    params = {"assignment_id": "A", "process_id": "P"}

    assert (
        translate_domain_message(
            code,
            default,
            params,
            locale="fr",
            localedir=ROOT / "tests" / "missing-catalogs",
        )
        == default
    )
    assert (
        translate_domain_message("future.unknown", default, {}, locale="fr") == default
    )
    assert (
        translate_domain_message(code, default, {"assignment_id": "A"}, locale="fr")
        == default
    )
    assert (
        translate_domain_message(
            code,
            default,
            {**params, "unexpected": "value"},
            locale="fr",
        )
        == default
    )
    assert (
        translate_domain_message(
            "hour_requirements.assigned_requirement_slot_s_would_change_resolve_them",
            default,
            {"generation_conflicts_count": "one"},
            locale="fr",
        )
        == default
    )


class _BrokenFormatString(str):
    def __format__(self, format_spec: str) -> str:
        del format_spec
        raise TypeError("broken format probe")


def test_malformed_templates_and_format_failures_are_safe() -> None:
    assert i18n._template_fields("{") is None
    assert i18n._template_fields("{value!r}") is None
    assert (
        i18n._formatted_or_default(
            "{value}",
            "fallback",
            {"value": _BrokenFormatString("value")},
        )
        == "fallback"
    )


@pytest.mark.parametrize(
    ("locale", "count", "expected"),
    [
        ("es", 0, "0 franjas de requisito asignadas cambiarían"),
        ("es", 1, "1 franja de requisito asignada cambiaría"),
        ("es", 3, "3 franjas de requisito asignadas cambiarían"),
        ("fr", 0, "0 créneau d'exigence attribué serait modifié"),
        ("fr", 1, "1 créneau d'exigence attribué serait modifié"),
        ("fr", 3, "3 créneaux d'exigence attribués seraient modifiés"),
    ],
)
def test_ngettext_plural_forms(locale: str, count: int, expected: str) -> None:
    translated = translate_domain_message(
        "hour_requirements.assigned_requirement_slot_s_would_change_resolve_them",
        "fallback",
        {"generation_conflicts_count": count},
        locale=locale,
    )
    assert translated.startswith(expected)


def _fields(template: str) -> frozenset[str]:
    return frozenset(
        field for _, field, _, _ in Formatter().parse(template) if field is not None
    )


@pytest.mark.parametrize("locale", ["es", "fr"])
def test_po_and_mo_catalogs_cover_the_complete_c7_contract(locale: str) -> None:
    taxonomy = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    expected = {record["code"]: record for record in taxonomy["codes"]}
    po_path = LOCALE_DIR / locale / "LC_MESSAGES" / f"{GETTEXT_DOMAIN}.po"
    mo_path = po_path.with_suffix(".mo")

    with po_path.open("r", encoding="utf-8") as source:
        catalog = pofile.read_po(source, locale=locale)
    with mo_path.open("rb") as compiled:
        translations = mofile.read_mo(compiled)

    actual_codes = {
        message.id[0] if isinstance(message.id, tuple) else message.id
        for message in catalog
        if message.id
    }
    assert actual_codes == set(expected)
    assert mo_path.is_file()

    for code, record in expected.items():
        message = catalog.get(code)
        assert message is not None
        assert message.string
        expected_fields = frozenset(record["param_names"])
        strings = (
            tuple(message.string)
            if isinstance(message.string, (list, tuple))
            else (message.string,)
        )
        assert all(_fields(text) == expected_fields for text in strings)
        if code in PLURAL_COUNT_PARAMS:
            assert isinstance(message.id, tuple)
        else:
            assert isinstance(message.id, str)
        compiled_message = translations.get(code)
        assert compiled_message is not None
        compiled_strings = (
            tuple(compiled_message.string)
            if isinstance(compiled_message.string, (list, tuple))
            else compiled_message.string
        )
        source_strings = (
            tuple(message.string)
            if isinstance(message.string, (list, tuple))
            else message.string
        )
        assert compiled_strings == source_strings


def test_babel_is_build_only_and_production_checks_compiled_catalogs() -> None:
    build_requirements = (
        ROOT / "reparto_service" / "requirements_build.txt"
    ).read_text(encoding="utf-8")
    dev_requirements = (ROOT / "reparto_service" / "requirements_dev.txt").read_text(
        encoding="utf-8"
    )
    production_requirements = (
        ROOT / "reparto_service" / "requirements_prod.txt"
    ).read_text(encoding="utf-8")
    base_requirements = (ROOT / "reparto_service" / "requirements_base.txt").read_text(
        encoding="utf-8"
    )
    dockerfile = (ROOT / "reparto_service" / "Dockerfile").read_text(encoding="utf-8")

    assert "Babel>=" in build_requirements
    assert "-r requirements_build.txt" in dev_requirements
    assert "Babel" not in production_requirements
    assert "Babel" not in base_requirements
    assert "gettext.translation('reparto'" in dockerfile


def test_real_app_translates_domain_errors_at_the_boundary(
    client: TestClient,
) -> None:
    department_id = uuid.uuid4()
    response = client.get(
        f"/reparto/departments/{department_id}",
        headers={"Accept-Language": "fr-FR"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "base.not_found",
        "message": f"Department {department_id} est introuvable.",
        "params": {
            "item_id": str(department_id),
            "model___name__": "Department",
        },
    }
    assert response.headers["content-language"] == "fr"
    assert response.headers["vary"] == "accept-language"


def test_stdlib_gettext_reads_the_committed_catalogs() -> None:
    for locale in ("es", "fr"):
        translation = gettext.translation(
            GETTEXT_DOMAIN,
            localedir=LOCALE_DIR,
            languages=[locale],
        )
        assert (
            translation.gettext("base.no_teacher_profile_is_linked_auth_user")
            != "base.no_teacher_profile_is_linked_auth_user"
        )
