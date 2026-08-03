from pathlib import Path
import base64

import pytest

from clashsub.config import ConfigError, Settings


def _write(path: Path, value: str) -> str:
    path.write_text(value, encoding="utf-8")
    return str(path)


def _set_admin_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADMIN_USERNAME_FILE", _write(tmp_path / "admin", "admin"))
    monkeypatch.setenv("ADMIN_PASSWORD_FILE", _write(tmp_path / "admin-pass", "admin-pass"))


def test_settings_read_secrets_without_exposing_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name, value in {
        "upstream": "https://provider.invalid/sub?token=secret-token",
        "username": "admin-name",
        "password": "admin-password",
    }.items():
        (tmp_path / name).write_text(value, encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("UPSTREAM_URL_FILE", str(tmp_path / "upstream"))
    monkeypatch.setenv("ADMIN_USERNAME_FILE", str(tmp_path / "username"))
    monkeypatch.setenv("ADMIN_PASSWORD_FILE", str(tmp_path / "password"))
    monkeypatch.setenv("TRUSTED_PROXY_CIDRS", "172.18.0.0/16,127.0.0.1/32")
    monkeypatch.setenv("DOWNLOAD_ALLOWED_CIDRS", "198.18.0.0/15")

    settings = Settings.from_env()

    assert settings.upstream_url.get_secret_value().endswith("secret-token")
    assert settings.initial_username.get_secret_value() == "admin-name"
    assert settings.initial_password.get_secret_value() == "admin-password"
    assert "secret-token" not in repr(settings)
    assert settings.trusted_proxy_cidrs == ("172.18.0.0/16", "127.0.0.1/32")
    assert settings.download_allowed_cidrs == ("198.18.0.0/15",)


def test_encryption_key_file_is_configured_without_exposing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.setenv("UPSTREAM_URL_FILE", _write(tmp_path / "upstream", "https://provider.invalid/sub"))
    key_path = tmp_path / "encryption-key"
    key_path.write_text(base64.b64encode(b"k" * 32).decode(), encoding="utf-8")
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", str(key_path))

    settings = Settings.from_env()

    assert settings.encryption_key_file == key_path
    assert "k" * 32 not in repr(settings)


def test_converter_urls_are_read_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.setenv("UPSTREAM_URL_FILE", _write(tmp_path / "upstream", "https://provider.invalid/sub"))
    monkeypatch.setenv("CONVERTER_BASE_URL", "http://subconverter:25500")
    monkeypatch.setenv("CONVERTER_SOURCE_BASE_URL", "http://clashsub:8080")

    settings = Settings.from_env()

    assert settings.converter_base_url == "http://subconverter:25500"
    assert settings.converter_source_base_url == "http://clashsub:8080"


def test_converter_defaults_to_local_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.setenv("UPSTREAM_URL_FILE", _write(tmp_path / "upstream", "https://provider.invalid/sub"))
    monkeypatch.delenv("CONVERTER_BASE_URL", raising=False)

    assert Settings.from_env().converter_base_url == "http://subconverter:25500"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CONVERTER_BASE_URL", "ftp://subconverter:25500"),
        ("CONVERTER_BASE_URL", "http://user@subconverter:25500"),
        ("CONVERTER_SOURCE_BASE_URL", "http://clashsub:8080/raw?token=secret"),
        ("CONVERTER_SOURCE_BASE_URL", "http://clashsub:8080/#fragment"),
        ("CONVERTER_SOURCE_BASE_URL", "http://clash\tsub:8080"),
    ],
)
def test_converter_urls_reject_unsafe_values(
    name: str,
    value: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.setenv("UPSTREAM_URL_FILE", _write(tmp_path / "upstream", "https://provider.invalid/sub"))
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError, match=name):
        Settings.from_env()


def test_invalid_download_allowed_cidr_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.setenv("UPSTREAM_URL_FILE", _write(tmp_path / "upstream", "https://provider.invalid/sub"))
    monkeypatch.setenv("DOWNLOAD_ALLOWED_CIDRS", "not-a-network")

    with pytest.raises(ConfigError, match="DOWNLOAD_ALLOWED_CIDRS"):
        Settings.from_env()


def test_protocol_only_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.delenv("UPSTREAM_URL_FILE", raising=False)
    monkeypatch.setenv("AIRPORT_EMAIL_FILE", _write(tmp_path / "email", "member@example.test"))
    monkeypatch.setenv("AIRPORT_PASSWORD_FILE", _write(tmp_path / "airport-pass", "airport-pass"))
    monkeypatch.setenv("AIRPORT_API_BASE_URL", "https://panel.example.test/api/v1")

    settings = Settings.from_env()

    assert settings.upstream_url is None
    assert settings.airport_api_base_url == "https://panel.example.test/api/v1"
    assert settings.protocol_configured is True
    assert "member@example.test" not in repr(settings)
    assert "airport-pass" not in repr(settings)


def test_empty_fallback_secret_is_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.setenv("UPSTREAM_URL_FILE", _write(tmp_path / "upstream", ""))
    monkeypatch.setenv("AIRPORT_EMAIL_FILE", _write(tmp_path / "email", "member@example.test"))
    monkeypatch.setenv("AIRPORT_PASSWORD_FILE", _write(tmp_path / "airport-pass", "airport-pass"))
    monkeypatch.setenv("AIRPORT_API_BASE_URL", "https://panel.example.test/api/v1")

    assert Settings.from_env().upstream_url is None


def test_partial_protocol_settings_are_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.delenv("UPSTREAM_URL_FILE", raising=False)
    monkeypatch.setenv("AIRPORT_EMAIL_FILE", _write(tmp_path / "email", "member@example.test"))
    monkeypatch.delenv("AIRPORT_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("AIRPORT_API_BASE_URL", raising=False)

    with pytest.raises(
        ConfigError,
        match="AIRPORT_API_BASE_URL, AIRPORT_EMAIL_FILE, and AIRPORT_PASSWORD_FILE",
    ):
        Settings.from_env()


@pytest.mark.parametrize(
    "value",
    [
        "http://panel.example.test/api/v1",
        "https://user@panel.example.test/api/v1",
        "https://@panel.example.test/api/v1",
        "https://:@panel.example.test/api/v1",
        "https://[broken/api/v1",
        "https://panel.example.test:bad/api/v1",
        "https://panel.exam\tple.test/api/v1",
        "https://panel.example.test/api\t/v1",
        "https://panel.example.test/api\n/v1",
        "https://panel.example.test/api/v1?x=1",
        "https://panel.example.test/api/v1#fragment",
    ],
)
def test_airport_api_base_url_must_be_safe_https(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.delenv("UPSTREAM_URL_FILE", raising=False)
    monkeypatch.setenv("AIRPORT_EMAIL_FILE", _write(tmp_path / "email", "member@example.test"))
    monkeypatch.setenv("AIRPORT_PASSWORD_FILE", _write(tmp_path / "airport-pass", "airport-pass"))
    monkeypatch.setenv("AIRPORT_API_BASE_URL", value)

    with pytest.raises(ConfigError, match="AIRPORT_API_BASE_URL"):
        Settings.from_env()


def test_at_least_one_subscription_source_is_required(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _set_admin_secrets(monkeypatch, tmp_path)
    monkeypatch.delenv("UPSTREAM_URL_FILE", raising=False)
    monkeypatch.delenv("AIRPORT_EMAIL_FILE", raising=False)
    monkeypatch.delenv("AIRPORT_PASSWORD_FILE", raising=False)
    monkeypatch.delenv("AIRPORT_API_BASE_URL", raising=False)

    with pytest.raises(ConfigError, match="subscription source"):
        Settings.from_env()


def test_missing_secret_file_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("UPSTREAM_URL_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("ADMIN_USERNAME_FILE", str(tmp_path / "missing-user"))
    monkeypatch.setenv("ADMIN_PASSWORD_FILE", str(tmp_path / "missing-password"))
    with pytest.raises(ConfigError, match="UPSTREAM_URL_FILE"):
        Settings.from_env()
