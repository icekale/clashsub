from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr


class ConfigError(ValueError):
    pass


def _cidrs(name: str) -> tuple[str, ...]:
    values = tuple(filter(None, (item.strip() for item in os.getenv(name, "").split(","))))
    try:
        for value in values:
            ipaddress.ip_network(value)
    except ValueError:
        raise ConfigError(f"{name} contains an invalid CIDR") from None
    return values


def _secret(name: str) -> SecretStr:
    raw_path = os.environ.get(name, "").strip()
    if not raw_path:
        raise ConfigError(f"{name} is required")
    path = Path(raw_path)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"{name} cannot be read") from exc
    if not value:
        raise ConfigError(f"{name} is empty")
    return SecretStr(value)


def _optional_secret(name: str) -> SecretStr | None:
    raw_path = os.environ.get(name, "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"{name} cannot be read") from exc
    return SecretStr(value) if value else None


def _api_base_url() -> str | None:
    value = os.environ.get("AIRPORT_API_BASE_URL", "").strip().rstrip("/")
    if not value:
        return None
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConfigError(
            "AIRPORT_API_BASE_URL must be an HTTPS origin/path without credentials, query, or fragment"
        )
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ConfigError(
            "AIRPORT_API_BASE_URL must be an HTTPS origin/path without credentials, query, or fragment"
        ) from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigError(
            "AIRPORT_API_BASE_URL must be an HTTPS origin/path without credentials, query, or fragment"
        )
    return value


def validate_http_origin(value: str, name: str = "URL") -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    error = f"{name} must be an HTTP(S) origin/path without credentials, query, or fragment"
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise ConfigError(error)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ConfigError(error) from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigError(error)
    return value


def _service_url(name: str, default: str = "") -> str:
    return validate_http_origin(os.environ.get(name, default), name)


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    frontend_dir: Path
    initial_username: SecretStr = field(repr=False)
    initial_password: SecretStr = field(repr=False)
    upstream_url: SecretStr | None = field(default=None, repr=False)
    airport_api_base_url: str | None = None
    airport_email: SecretStr | None = field(default=None, repr=False)
    airport_password: SecretStr | None = field(default=None, repr=False)
    trusted_proxy_cidrs: tuple[str, ...] = ()
    download_allowed_cidrs: tuple[str, ...] = ()
    converter_base_url: str = "http://subconverter:25500"
    converter_source_base_url: str = ""
    max_response_bytes: int = 8 * 1024 * 1024
    encryption_key_file: Path = Path("/run/secrets/encryption_key")

    @property
    def protocol_configured(self) -> bool:
        return bool(self.airport_api_base_url and self.airport_email and self.airport_password)

    @classmethod
    def from_env(cls) -> "Settings":
        upstream_url = _optional_secret("UPSTREAM_URL_FILE")
        airport_api_base_url = _api_base_url()
        airport_email = _optional_secret("AIRPORT_EMAIL_FILE")
        airport_password = _optional_secret("AIRPORT_PASSWORD_FILE")
        airport_values = (airport_api_base_url, airport_email, airport_password)
        if any(airport_values) and not all(airport_values):
            raise ConfigError(
                "AIRPORT_API_BASE_URL, AIRPORT_EMAIL_FILE, and AIRPORT_PASSWORD_FILE must be configured together"
            )
        if not all(airport_values) and upstream_url is None:
            raise ConfigError("at least one subscription source is required")
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", "/data")),
            frontend_dir=Path(os.getenv("FRONTEND_DIR", "/app/frontend/dist")),
            initial_username=_secret("ADMIN_USERNAME_FILE"),
            initial_password=_secret("ADMIN_PASSWORD_FILE"),
            upstream_url=upstream_url,
            airport_api_base_url=airport_api_base_url,
            airport_email=airport_email,
            airport_password=airport_password,
            trusted_proxy_cidrs=_cidrs("TRUSTED_PROXY_CIDRS"),
            download_allowed_cidrs=_cidrs("DOWNLOAD_ALLOWED_CIDRS"),
            converter_base_url=_service_url("CONVERTER_BASE_URL", "http://subconverter:25500"),
            converter_source_base_url=_service_url("CONVERTER_SOURCE_BASE_URL"),
            encryption_key_file=Path(os.getenv("ENCRYPTION_KEY_FILE", "/run/secrets/encryption_key")),
        )
