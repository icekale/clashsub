from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from .config import validate_http_origin
from .db import Database


@dataclass(frozen=True)
class RuntimeSettings:
    refresh_interval_minutes: int = 60
    access_mode: str = "lan"
    lan_base_url: str = ""
    public_base_url: str = ""
    converter_enabled: bool = False
    openclash_enabled: bool = False
    openclash_api_url: str = ""
    openclash_provider: str = ""
    health_enabled: bool = False
    health_interval_seconds: int = 600
    health_timeout_seconds: int = 5
    health_refresh_enabled: bool = False
    health_refresh_online_ratio: float = 0.5
    health_refresh_cooldown_minutes: int = 10
    health_night_enabled: bool = False
    health_night_interval_seconds: int = 600
    health_night_start_hour: int = 0
    health_night_end_hour: int = 8

    def validated(self):
        if not 1 <= self.refresh_interval_minutes <= 1440:
            raise ValueError("refresh interval must be between 1 and 1440 minutes")
        if self.access_mode not in {"lan", "public"}:
            raise ValueError("access mode must be lan or public")
        if self.lan_base_url:
            validate_http_origin(self.lan_base_url, "LAN base URL")
        if self.access_mode == "public":
            base = validate_http_origin(self.public_base_url, "public base URL")
            if urlsplit(base).scheme != "https":
                raise ValueError("public mode requires an HTTPS base URL")
        if self.openclash_enabled:
            if not self.openclash_api_url.strip():
                raise ValueError("OpenClash API URL is required")
            api_url = validate_http_origin(self.openclash_api_url, "OpenClash API URL")
            if not self.openclash_provider.strip():
                raise ValueError("OpenClash provider name is required")
            object.__setattr__(self, "openclash_api_url", api_url)
        if not 30 <= self.health_interval_seconds <= 86400:
            raise ValueError("health check interval must be between 30 and 86400 seconds")
        if not 1 <= self.health_timeout_seconds <= 30:
            raise ValueError("health check timeout must be between 1 and 30 seconds")
        if not 0.1 <= self.health_refresh_online_ratio <= 1.0:
            raise ValueError("health refresh online ratio must be between 0.1 and 1.0")
        if not 1 <= self.health_refresh_cooldown_minutes <= 1440:
            raise ValueError("health refresh cooldown must be between 1 and 1440 minutes")
        if not 30 <= self.health_night_interval_seconds <= 86400:
            raise ValueError("night health interval must be between 30 and 86400 seconds")
        if not 0 <= self.health_night_start_hour <= 23 or not 0 <= self.health_night_end_hour <= 23:
            raise ValueError("night window hours must be between 0 and 23")
        if self.health_night_start_hour == self.health_night_end_hour:
            raise ValueError("night window start and end hours must differ")
        return self

    def is_night(self, hour: int) -> bool:
        if not self.health_night_enabled:
            return False
        start, end = self.health_night_start_hour, self.health_night_end_hour
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def effective_health_interval(self, hour: int) -> int:
        return self.health_night_interval_seconds if self.is_night(hour) else self.health_interval_seconds

    def active_base_url(self) -> str:
        return (self.public_base_url if self.access_mode == "public" else self.lan_base_url).rstrip("/")


class SettingsStore:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _parse_int(values: dict[str, str], key: str, default: int) -> int:
        raw = values.get(key)
        try:
            return int(raw) if raw else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_float(values: dict[str, str], key: str, default: float) -> float:
        raw = values.get(key)
        try:
            return float(raw) if raw else default
        except (TypeError, ValueError):
            return default

    def get(self) -> RuntimeSettings:
        with self.db.connect() as conn:
            values = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM app_settings")}
        return RuntimeSettings(
            refresh_interval_minutes=self._parse_int(values, "refresh_interval_minutes", 60),
            access_mode=values.get("access_mode", "lan"),
            lan_base_url=values.get("lan_base_url", ""),
            public_base_url=values.get("public_base_url", ""),
            converter_enabled=values.get("converter_enabled", "false") == "true",
            openclash_enabled=values.get("openclash_enabled", "false") == "true",
            openclash_api_url=values.get("openclash_api_url", ""),
            openclash_provider=values.get("openclash_provider", ""),
            health_enabled=values.get("health_enabled", "false") == "true",
            health_interval_seconds=self._parse_int(values, "health_interval_seconds", 600),
            health_timeout_seconds=self._parse_int(values, "health_timeout_seconds", 5),
            health_refresh_enabled=values.get("health_refresh_enabled", "false") == "true",
            health_refresh_online_ratio=self._parse_float(values, "health_refresh_online_ratio", 0.5),
            health_refresh_cooldown_minutes=self._parse_int(values, "health_refresh_cooldown_minutes", 10),
            health_night_enabled=values.get("health_night_enabled", "false") == "true",
            health_night_interval_seconds=self._parse_int(values, "health_night_interval_seconds", 600),
            health_night_start_hour=self._parse_int(values, "health_night_start_hour", 0),
            health_night_end_hour=self._parse_int(values, "health_night_end_hour", 8),
        ).validated()

    def update(self, settings: RuntimeSettings) -> RuntimeSettings:
        settings.validated()
        values = asdict(settings)
        with self.db.transaction() as conn:
            conn.executemany(
                "INSERT INTO app_settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(key, str(value).lower() if isinstance(value, bool) else str(value)) for key, value in values.items()],
            )
        return settings
