"""Request-locale negotiation and HTTP-boundary message translation."""

from __future__ import annotations

import gettext
import re
from collections.abc import Mapping
from contextvars import ContextVar, Token
from functools import lru_cache
from pathlib import Path
from string import Formatter

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from reparto_service.core.errors import DomainHTTPException, JsonValue


DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "es", "fr")
MAX_ACCEPT_LANGUAGE_BYTES = 1024
GETTEXT_DOMAIN = "reparto"
LOCALE_DIR = Path(__file__).resolve().parent.parent / "locales"

_LANGUAGE_RANGE_PATTERN = re.compile(r"^(?:\*|[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*)$")
_QUALITY_PATTERN = re.compile(r"^(?:0(?:\.\d{1,3})?|1(?:\.0{1,3})?)$")
_FIELD_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_FORMATTER = Formatter()
_current_locale: ContextVar[str] = ContextVar(
    "reparto_request_locale", default=DEFAULT_LOCALE
)

# These messages carry a count and therefore use the catalog's plural rules.
PLURAL_COUNT_PARAMS: Mapping[str, str] = {
    "hour_requirements.assigned_requirement_slot_s_would_change_resolve_them": (
        "generation_conflicts_count"
    ),
    "hour_requirements.expected_conflict_s_reconcile_but_plan_now_has": (
        "generation_conflicts_count"
    ),
    "planning_exchange.final_planning_export_is_blocked_by_blocking_validation": (
        "validations_blocking_count"
    ),
}


def _header_size(value: str) -> int:
    return len(value.encode("utf-8", errors="replace"))


def negotiate_locale(accept_language: str | None) -> str:
    """Select a supported locale from a bounded ``Accept-Language`` value."""
    if not accept_language or _header_size(accept_language) > MAX_ACCEPT_LANGUAGE_BYTES:
        return DEFAULT_LOCALE

    preferences: list[tuple[str, float, int]] = []
    for position, item in enumerate(accept_language.split(",")):
        parts = [part.strip() for part in item.split(";")]
        language_range = parts[0] if parts else ""
        if not _LANGUAGE_RANGE_PATTERN.fullmatch(language_range):
            continue
        quality = 1.0
        if len(parts) > 2:
            continue
        if len(parts) == 2:
            name, separator, raw_quality = parts[1].partition("=")
            if separator != "=" or name.strip().lower() != "q":
                continue
            raw_quality = raw_quality.strip()
            if not _QUALITY_PATTERN.fullmatch(raw_quality):
                continue
            quality = float(raw_quality)
        preferences.append((language_range.lower(), quality, position))

    wildcard = [item for item in preferences if item[0] == "*"]
    candidates: list[tuple[float, int, int, str]] = []
    for locale_position, locale in enumerate(SUPPORTED_LOCALES):
        explicit = [
            item
            for item in preferences
            if item[0] != "*" and item[0].split("-")[0] == locale
        ]
        matches = explicit or wildcard
        if not matches:
            continue
        best = min(matches, key=lambda item: (-item[1], item[2]))
        if best[1] > 0:
            candidates.append((-best[1], best[2], locale_position, locale))

    if not candidates:
        return DEFAULT_LOCALE
    return min(candidates)[3]


def current_locale() -> str:
    """Return the locale selected for the current request context."""
    return _current_locale.get()


def set_current_locale(locale: str) -> Token[str]:
    """Set a validated locale and return the token required for reset."""
    normalized = locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE
    return _current_locale.set(normalized)


def reset_current_locale(token: Token[str]) -> None:
    """Restore the request-locale context represented by ``token``."""
    _current_locale.reset(token)


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def _bundled_catalog(locale: str) -> gettext.NullTranslations:
    return gettext.translation(
        GETTEXT_DOMAIN,
        localedir=LOCALE_DIR,
        languages=[locale],
        fallback=True,
    )


def _catalog(locale: str, localedir: Path | None = None) -> gettext.NullTranslations:
    if localedir is None:
        return _bundled_catalog(locale)
    return gettext.translation(
        GETTEXT_DOMAIN,
        localedir=localedir,
        languages=[locale],
        fallback=True,
    )


def _template_fields(template: str) -> frozenset[str] | None:
    fields: set[str] = set()
    try:
        parsed = _FORMATTER.parse(template)
        for _, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            if (
                _FIELD_PATTERN.fullmatch(field_name) is None
                or format_spec
                or conversion
            ):
                return None
            fields.add(field_name)
    except ValueError:
        return None
    return frozenset(fields)


def _formatted_or_default(
    template: str,
    default: str,
    params: Mapping[str, JsonValue],
) -> str:
    fields = _template_fields(template)
    if fields is None or fields != frozenset(params):
        return default
    try:
        return template.format_map(params)
    except (KeyError, ValueError, TypeError):
        return default


def translate_domain_message(
    code: str,
    default: str,
    params: Mapping[str, JsonValue],
    *,
    locale: str | None = None,
    localedir: Path | None = None,
) -> str:
    """Translate one stable domain-error code, falling back without raising."""
    selected_locale = locale or current_locale()
    if selected_locale not in SUPPORTED_LOCALES or selected_locale == DEFAULT_LOCALE:
        return default

    catalog = _catalog(selected_locale, localedir)
    count_param = PLURAL_COUNT_PARAMS.get(code)
    if count_param is None:
        template = catalog.gettext(code)
    else:
        count = params.get(count_param)
        if not isinstance(count, int) or isinstance(count, bool):
            return default
        template = catalog.ngettext(code, f"{code}.plural", count)
    if template in {code, f"{code}.plural"}:
        return default
    return _formatted_or_default(template, default, params)


def translated_error_detail(error: DomainHTTPException) -> dict[str, JsonValue]:
    """Build the localized wire detail while preserving code and parameters."""
    return {
        "code": error.code,
        "message": translate_domain_message(
            error.code,
            error.message,
            error.params,
        ),
        "params": error.params,
    }


def _response_headers(error: DomainHTTPException, locale: str) -> dict[str, str]:
    headers = dict(error.headers or {})
    vary_key = next((key for key in headers if key.lower() == "vary"), "Vary")
    vary_values = {
        value.strip().lower()
        for value in headers.get(vary_key, "").split(",")
        if value.strip()
    }
    vary_values.add("accept-language")
    headers[vary_key] = ", ".join(sorted(vary_values))
    headers["Content-Language"] = locale
    return headers


async def domain_http_exception_handler(
    request: Request,
    error: DomainHTTPException,
) -> JSONResponse:
    """Translate service-owned exceptions at the HTTP boundary only."""
    del request
    locale = current_locale()
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": translated_error_detail(error)},
        headers=_response_headers(error, locale),
    )


class LocaleMiddleware:
    """Bind the negotiated locale before dependencies and reset it reliably."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_values = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"accept-language"
        ]
        header = b",".join(raw_values).decode("latin-1") if raw_values else None
        token = set_current_locale(negotiate_locale(header))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_locale(token)


__all__ = [
    "DEFAULT_LOCALE",
    "GETTEXT_DOMAIN",
    "LOCALE_DIR",
    "MAX_ACCEPT_LANGUAGE_BYTES",
    "PLURAL_COUNT_PARAMS",
    "SUPPORTED_LOCALES",
    "LocaleMiddleware",
    "current_locale",
    "domain_http_exception_handler",
    "negotiate_locale",
    "reset_current_locale",
    "set_current_locale",
    "translate_domain_message",
    "translated_error_detail",
]
