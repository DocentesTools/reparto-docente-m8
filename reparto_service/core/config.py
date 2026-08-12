"""Service settings for reparto_service.

Extends ConsumerServiceSettings with service-specific fields only.
ConsumerAuthMixin, ObservabilitySettingsMixin, and CommonSettings are
all inherited via the base class.
"""

from pathlib import Path
from typing import ClassVar

from auth_sdk_m8.utils.paths import find_dotenv
from fastapi_m8 import ConsumerServiceSettings
from pydantic_settings import SettingsConfigDict

from .. import __version__


class Settings(ConsumerServiceSettings):
    """reparto_service settings — inherits all consumer fields from fastapi-m8.

    fastapi-m8 >= 2.0.0 requires every consumer to declare its service/contract
    metadata (served at ``{API_PREFIX}/meta``, fail-closed at boot). This example
    tracks its own package ``__version__`` (kept in step with the fa-auth-m8 repo)
    rather than a placeholder; a real standalone service sets these from its own
    package/env.
    """

    ENV_FILE_DIR: ClassVar[Path] = Path(__file__).resolve().parent.parent

    SERVICE_VERSION: str = __version__
    CONTRACT_NAME: str = "reparto-docente-m8"
    CONTRACT_VERSION: str = "2.0.0"
    CONTRACT_RANGE: str = ">=2.0.0 <3.0.0"

    #: Insert the worked configuration example (``reparto_service.initial_data``)
    #: when the domain is empty. Off by default: a fresh Compose database is
    #: expected to come up empty, and a deployment must never invent rows.
    SEED_EXAMPLE_DATA: bool = False

    # Vault/`_FILE` source ordering is handled by the inherited
    # CommonSettings.settings_customise_sources classmethod — no override needed.
    model_config = SettingsConfigDict(
        env_file=find_dotenv(Path(__file__).resolve().parent.parent),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="forbid",
    )


try:
    settings = Settings()
except Exception as exc:  # pragma: no cover
    raise RuntimeError(f"Configuration validation error:\n {exc}") from exc
