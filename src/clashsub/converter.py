from __future__ import annotations

import re
import time
import unicodedata
from urllib.parse import quote

import httpx
import yaml

from .cache_files import CacheFiles


RAW_URL_PLACEHOLDER = "__CLASHSUB_RAW_URL__"
RAW_URL_ENCODED_PLACEHOLDER = "__CLASHSUB_RAW_URL_ENCODED__"
SUPPORTED_FORMATS = {"clash", "surge", "loon"}
COUNTRY_CODE = re.compile(r"^[A-Za-z]{2}$")


def _surge_normalize_name(name: str) -> str:
    """Match node names stripped of emoji by the converter (e.g. flag prefixes)."""
    return "".join(ch for ch in name if unicodedata.category(ch) != "So").strip()


class ConverterService:
    def __init__(
        self,
        cache: CacheFiles,
        base_url: str,
        transport=None,
        cache_ttl: int = 3600,
        max_bytes: int = 8 * 1024 * 1024,
    ):
        self.cache, self.base_url, self.transport = cache, base_url.rstrip("/"), transport
        self.cache_ttl, self.max_bytes = cache_ttl, max_bytes

    def _validate_and_sanitize(
        self, text: str, raw_url: str, format: str, surge_params: dict | None = None
    ) -> str:
        if len(text.encode("utf-8")) > self.max_bytes:
            raise ValueError("converter response is too large")
        if format == "clash":
            document = yaml.safe_load(text)
            providers = document.get("proxy-providers") if isinstance(document, dict) else None
            proxies = document.get("proxies") if isinstance(document, dict) else None
            has_providers = isinstance(providers, dict) and bool(providers)
            has_proxies = isinstance(proxies, list) and bool(proxies)
            if not has_providers and not has_proxies:
                raise ValueError("converter response has no expected provider")
        elif format in {"surge", "loon"}:
            lines = text.splitlines(keepends=True)
            if any(line.lstrip().startswith("#!MANAGED-CONFIG") for line in lines):
                text = "".join(
                    line for line in lines if not line.lstrip().startswith("#!MANAGED-CONFIG")
                )
            if not self._has_valid_proxy_section(text):
                raise ValueError("converter response has no expected provider")
            if format == "surge":
                text = self._surge_compatible_proxies(text)
                text = self._surge_normalize_ws_headers(text)
                text = self._surge_inject_node_params(text, surge_params or {})
                text = self._surge_compatible_rules(text)
        else:
            raise ValueError("unsupported converter format")
        return text.replace(quote(raw_url, safe=""), RAW_URL_ENCODED_PLACEHOLDER).replace(
            raw_url, RAW_URL_PLACEHOLDER
        )

    @staticmethod
    def _surge_compatible_proxies(text: str) -> str:
        """Fill empty ws-path values (Surge requires a path when ws=true)."""
        section = None
        lines = text.splitlines(keepends=True)
        output = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
            elif section == "proxy" and "ws=true" in stripped and "=" in stripped:
                line = re.sub(r"(?i)ws-path=(\s*)(,|$)", r"ws-path=/\2", line)
            output.append(line)
        return "".join(output)

    @staticmethod
    def _surge_normalize_ws_headers(text: str) -> str:
        """Strip stray quotes from ws-headers values.

        Sub-Store emits values like ``ws-headers="Host:"example.com""`` (double
        quoting) for some airport configs; Surge expects ``ws-headers=Host:example.com``.
        """
        section = None
        lines = text.splitlines(keepends=True)
        output = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
            elif section == "proxy" and "ws-headers=" in line:
                head, _, tail = line.partition("ws-headers=")
                value, comma, rest = tail.partition(",")
                line = f"{head}ws-headers={value.replace('\"', '')}{comma}{rest}"
            output.append(line)
        return "".join(output)

    @staticmethod
    def _surge_nested(d: dict, key: str):
        for existing, value in d.items():
            if str(existing).lower() == key:
                return value
        return None

    def _surge_node_params(self, source_digest: str | None) -> dict[str, dict[str, str]]:
        """Read the source Clash config and map node name to Surge-required params."""
        if not source_digest:
            return {}
        try:
            snapshot = self.cache.read_raw(source_digest)
        except OSError:
            return {}
        try:
            document = yaml.safe_load(snapshot.payload)
        except yaml.YAMLError:
            return {}
        proxies = document.get("proxies") if isinstance(document, dict) else None
        if not isinstance(proxies, list):
            return {}
        params: dict[str, dict[str, str]] = {}
        for proxy in proxies:
            if not isinstance(proxy, dict) or not proxy.get("name"):
                continue
            entry: dict[str, str] = {}
            if proxy.get("network") == "ws":
                ws_opts = proxy.get("ws-opts") if isinstance(proxy.get("ws-opts"), dict) else {}
                path = self._surge_nested(ws_opts, "path")
                if path:
                    entry["ws_path"] = str(path)
                headers = self._surge_nested(ws_opts, "headers") or proxy.get("ws-headers")
                if isinstance(headers, dict) and headers.get("Host"):
                    entry["ws_host"] = str(headers["Host"])
            if proxy.get("skip-cert-verify"):
                entry["skip_cert_verify"] = "true"
            if entry:
                params[_surge_normalize_name(str(proxy["name"]))] = entry
        return params

    @staticmethod
    def _surge_inject_node_params(text: str, params: dict[str, dict[str, str]]) -> str:
        """Inject ws-path/ws-headers/skip-cert-verify from the source config."""
        if not params:
            return text
        section = None
        lines = text.splitlines(keepends=True)
        output = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
                output.append(line)
                continue
            if section == "proxy" and "=" in line:
                name = line.split("=", 1)[0].strip()
                entry = params.get(name) or params.get(_surge_normalize_name(name))
                if entry:
                    head, _, tail = line.partition("=")
                    kept = []
                    for part in tail.split(","):
                        key = part.strip().split("=", 1)[0].strip().lower()
                        if key in ("ws-path", "ws-headers", "skip-cert-verify"):
                            continue
                        kept.append(part)
                    extra = []
                    if "ws_path" in entry:
                        extra.append(f"ws-path={entry['ws_path']}")
                    if "ws_host" in entry:
                        extra.append(f"ws-headers=Host:{entry['ws_host']}")
                    if entry.get("skip_cert_verify"):
                        extra.append("skip-cert-verify=true")
                    for index, part in enumerate(kept):
                        key = part.strip().split("=", 1)[0].strip().lower()
                        if key == "udp-relay":
                            kept[index:index] = extra
                            break
                    else:
                        kept.extend(extra)
                    line = f"{head}={','.join(kept)}"
            output.append(line)
        return "".join(output)

    @staticmethod
    def _surge_compatible_rules(text: str) -> str:
        """Drop Clash-only rules and guarantee a CN direct fallback."""
        section = None
        lines = text.splitlines(keepends=True)
        output = []
        has_cn_direct = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
                output.append(line)
                continue
            if section == "rule":
                if stripped.upper().startswith("GEOSITE,"):
                    continue
                if stripped.upper().startswith("GEOIP,"):
                    parts = stripped.split(",")
                    if len(parts) < 2 or not COUNTRY_CODE.fullmatch(parts[1].strip()):
                        continue
                    if parts[1].strip().upper() == "CN" and "DIRECT" in stripped.upper():
                        has_cn_direct = True
            output.append(line)
        if section == "rule" and not has_cn_direct:
            for index, line in enumerate(output):
                if line.strip().upper().startswith("FINAL,"):
                    output.insert(index, "GEOIP,CN,DIRECT\n")
                    break
            else:
                output.append("GEOIP,CN,DIRECT\n")
        return "".join(output)

    @staticmethod
    def _has_valid_proxy_section(text: str) -> bool:
        section = None
        has_general = False
        has_proxy = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
                if section == "general":
                    has_general = True
                continue
            if section == "proxy" and stripped and not stripped.startswith(("#", ";")) and "=" in stripped:
                _, value = stripped.split("=", 1)
                fields = [field.strip() for field in value.split(",")]
                if len(fields) >= 3 and all(fields[:3]):
                    try:
                        port = int(fields[2])
                    except ValueError:
                        continue
                    has_proxy = has_proxy or 1 <= port <= 65535
        return has_general and has_proxy

    @staticmethod
    def _restore_raw_url(template: str, raw_url: str) -> str:
        return template.replace(RAW_URL_ENCODED_PLACEHOLDER, quote(raw_url, safe="")).replace(
            RAW_URL_PLACEHOLDER, raw_url
        )

    @staticmethod
    def _surge_managed_header(output_raw_url: str) -> str:
        """Managed-config header so Surge offers automatic/manual subscription updates."""
        if "/raw/" in output_raw_url:
            base, token = output_raw_url.rsplit("/raw/", 1)
            managed_url = f"{base}/surge/{token}"
        else:
            managed_url = output_raw_url
        return f"#!MANAGED-CONFIG {managed_url} interval=3600\n"

    def _finalize(self, body: str, format: str, output_raw_url: str) -> str:
        if format == "surge":
            body = self._surge_managed_header(output_raw_url) + body
        return body

    async def render(
        self,
        share_id: str,
        raw_url: str,
        format: str = "clash",
        public_raw_url: str | None = None,
        source_digest: str | None = None,
    ) -> str:
        if format not in SUPPORTED_FORMATS:
            raise ValueError("unsupported converter format")
        output_raw_url = public_raw_url or raw_url
        template = None
        try:
            template = self.cache.read_converter_template(share_id, format)
            if time.time() - self.cache.converter_mtime(share_id, format) <= self.cache_ttl:
                return self._finalize(
                    self._restore_raw_url(template, output_raw_url), format, output_raw_url
                )
        except OSError:
            template = None
        surge_params = self._surge_node_params(source_digest) if format == "surge" else {}
        try:
            params = {"target": format, "url": raw_url, "expand": "true"}
            if format == "surge":
                params["ver"] = "4"
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20, follow_redirects=True
            ) as client:
                response = await client.get(f"{self.base_url}/sub", params=params)
            response.raise_for_status()
            sanitized = self._validate_and_sanitize(response.text, raw_url, format, surge_params)
            self.cache.write_converter_template(share_id, sanitized, format)
            return self._finalize(
                self._restore_raw_url(sanitized, output_raw_url), format, output_raw_url
            )
        except (httpx.HTTPError, ValueError, yaml.YAMLError, OSError) as exc:
            if template is not None:
                return self._finalize(
                    self._restore_raw_url(template, output_raw_url), format, output_raw_url
                )
            raise RuntimeError("converter unavailable") from exc
