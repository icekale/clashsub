from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from urllib.parse import quote

import httpx
import yaml

from .cache_files import CacheFiles


RAW_URL_PLACEHOLDER = "__CLASHSUB_RAW_URL__"
RAW_URL_ENCODED_PLACEHOLDER = "__CLASHSUB_RAW_URL_ENCODED__"
COUNTRY_CODE = re.compile(r"^[A-Za-z]{2}$")

# 可选输出格式（同时也是 /sub 的 target、订阅路由名与分享里的 kind）。
# stash 未列入：v1.9.4 的默认规则集会被它自己判为 "a Stash ruleset contains an
# unsupported or invalid rule"，等上游修好再补。
CONVERTER_FORMATS = ("clash", "surge", "loon", "quanx", "surfboard", "singbox")
SUPPORTED_FORMATS = frozenset(CONVERTER_FORMATS)

# Surge/Surfboard 同为 INI 方言，允许服务端代写 #!MANAGED-CONFIG。
MANAGED_HEADER_FORMATS = frozenset({"surge", "surfboard"})

# 客户端可通过订阅地址覆盖的上游参数白名单。只收布尔、少量整数、以及我们自己的
# template 别名：正则类的 rename/include/exclude/filter 会在转换进程里编译客户
# 端提供的正则（ReDoS），config 能读容器内任意文件，upload 会把订阅推到第三方。
BOOLEAN_PARAMS = (
    "emoji",
    "list",
    "sort",
    "fdn",
    "udp",
    "tfo",
    "scv",
    "new_name",
    "append_type",
)
BOOLEAN_VALUES = {
    "true": "true",
    "1": "true",
    "yes": "true",
    "false": "false",
    "0": "false",
    "no": "false",
}

# ponytail: 远程 jsdelivr，跟上游 default_external_config 同一面镜像；规则集体积大了再打进镜像。
_AETHER_CFG = (
    "https://testingcf.jsdelivr.net/gh/Aethersailor/Custom_OpenClash_Rules"
    "@refs/heads/main/cfg"
)
TEMPLATES = {
    "standard": f"{_AETHER_CFG}/Custom_Clash.ini",
    "standard-fallback": f"{_AETHER_CFG}/Custom_Clash_Fallback.ini",
    "lite": f"{_AETHER_CFG}/Custom_Clash_Lite.ini",
    "lite-fallback": f"{_AETHER_CFG}/Custom_Clash_Lite_Fallback.ini",
    "gfw": f"{_AETHER_CFG}/Custom_Clash_GFW.ini",
    "gfw-fallback": f"{_AETHER_CFG}/Custom_Clash_GFW_Fallback.ini",
    "full": f"{_AETHER_CFG}/Custom_Clash_Full.ini",
    "full-fallback": f"{_AETHER_CFG}/Custom_Clash_Full_Fallback.ini",
}


def client_params(query) -> dict[str, str]:
    """从订阅请求的查询串里挑出白名单参数，其余一律忽略。"""
    params: dict[str, str] = {}
    for key in BOOLEAN_PARAMS:
        value = BOOLEAN_VALUES.get(str(query.get(key, "")).strip().lower())
        if value is not None:
            params[key] = value
    version = str(query.get("ver", "")).strip()
    if version.isdigit() and 2 <= int(version) <= 4:
        params["ver"] = version
    template = str(query.get("template", "")).strip()
    if template in TEMPLATES:
        params["config"] = TEMPLATES[template]
    return params


# /version 页面里的版本号与提交号；管理端诊断卡片用，解析失败就当未知。
VERSION_PATTERN = re.compile(r"\bv(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.]+)?)")
COMMIT_PATTERN = re.compile(r"/commit/([0-9a-f]{7,40})")


def params_key(params: dict[str, str] | None) -> str:
    """参数指纹：空参数返回空串，保持默认订阅的缓存文件名不变。"""
    if not params:
        return ""
    canonical = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _statistics_summary(document) -> dict | None:
    """把上游 /dashboard/data 收敛成管理端要用的几个数，巨型 JSON 不往浏览器发。"""
    if not isinstance(document, dict) or not document.get("enabled"):
        return None
    windows = document.get("windows") or {}
    lifecycle = document.get("request_lifecycle") or {}
    terminal = lifecycle.get("terminal") or {}
    stages = lifecycle.get("stages") or {}
    runtime = document.get("runtime") or {}

    def window(name: str) -> dict[str, int]:
        source = windows.get(name) or {}
        return {
            "subscription_requests": source.get("subscription_requests", 0),
            "rule_conversions": source.get("rule_conversions", 0),
        }

    def p95_ms(stage: str) -> float | None:
        value = (stages.get(stage) or {}).get("p95_microseconds")
        return round(value / 1000, 1) if isinstance(value, (int, float)) else None

    return {
        "generated_at": document.get("generated_at"),
        "revision": document.get("revision"),
        "uptime_seconds": runtime.get("uptime_seconds"),
        "started_at": runtime.get("started_at"),
        "launch_count": runtime.get("launch_count"),
        "day": window("day"),
        "lifetime": window("lifetime"),
        "successful_responses": lifecycle.get("successful_responses", 0),
        "completed": terminal.get("completed", 0),
        "failed": terminal.get("failed", 0),
        "rejected": terminal.get("rejected", 0),
        "deadline_exceeded": terminal.get("deadline_exceeded", 0),
        "fetch_p95_ms": p95_ms("fetch"),
        "parse_p95_ms": p95_ms("parse"),
    }


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
        elif format == "singbox":
            self._validate_singbox(text)
        elif format == "quanx":
            if not self._has_valid_quanx_servers(text):
                raise ValueError("converter response has no expected provider")
        elif format in {"surge", "loon", "surfboard"}:
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
            elif format == "surfboard":
                # Surfboard 与 Surge 共用 INI 方言与 ws 参数要求，同样补齐节点参数；
                # 规则部分不代改（GEOSITE 在 Surfboard 上的支持情况未经验证）。
                text = self._surge_compatible_proxies(text)
                text = self._surge_normalize_ws_headers(text)
                text = self._surge_inject_node_params(text, surge_params or {})
        else:
            raise ValueError("unsupported converter format")
        return text.replace(quote(raw_url, safe=""), RAW_URL_ENCODED_PLACEHOLDER).replace(
            raw_url, RAW_URL_PLACEHOLDER
        )

    @staticmethod
    def _validate_singbox(text: str) -> None:
        """sing-box 输出为 JSON，至少要有一个真实节点（server 字段或 endpoints 条目）。"""
        document = json.loads(text)
        if not isinstance(document, dict):
            raise ValueError("converter response is not a sing-box config")
        outbounds = document.get("outbounds")
        endpoints = document.get("endpoints")
        has_outbound = isinstance(outbounds, list) and any(
            isinstance(item, dict) and item.get("server") for item in outbounds
        )
        has_endpoint = isinstance(endpoints, list) and bool(endpoints)
        if not has_outbound and not has_endpoint:
            raise ValueError("converter response has no expected provider")

    @staticmethod
    def _has_valid_quanx_servers(text: str) -> bool:
        """Quantumult X 的节点行是 ``类型 = 主机:端口, key=value``。"""
        section = None
        has_general = False
        has_server = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                section = stripped[1:-1].strip().lower()
                has_general = has_general or section == "general"
                continue
            if section == "server_local" and stripped and not stripped.startswith(("#", ";", "//")):
                _, separator, value = stripped.partition("=")
                if not separator:
                    continue
                host, _, port = value.split(",", 1)[0].strip().rpartition(":")
                if not host or not port.isdigit():
                    continue
                has_server = has_server or 1 <= int(port) <= 65535
        return has_general and has_server

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
    def _managed_header(format: str, output_raw_url: str, params: dict[str, str] | None = None) -> str:
        """Managed-config header so Surge/Surfboard offer automatic or manual updates."""
        if "/raw/" in output_raw_url:
            base, token = output_raw_url.rsplit("/raw/", 1)
            managed_url = f"{base}/{format}/{token}"
        else:
            managed_url = output_raw_url
        # 客户端参数要随 managed URL 一起下发，否则客户端下次自动更新会丢掉它们。
        if params:
            query = "&".join(f"{key}={params[key]}" for key in sorted(params))
            managed_url = f"{managed_url}{'&' if '?' in managed_url else '?'}{query}"
        return f"#!MANAGED-CONFIG {managed_url} interval=3600\n"

    def _finalize(
        self, body: str, format: str, output_raw_url: str, params: dict[str, str] | None = None
    ) -> str:
        if format in MANAGED_HEADER_FORMATS:
            body = self._managed_header(format, output_raw_url, params) + body
        return body

    async def render(
        self,
        share_id: str,
        raw_url: str,
        format: str = "clash",
        public_raw_url: str | None = None,
        source_digest: str | None = None,
        params: dict[str, str] | None = None,
    ) -> str:
        if format not in SUPPORTED_FORMATS:
            raise ValueError("unsupported converter format")
        output_raw_url = public_raw_url or raw_url
        key = params_key(params)
        template = None
        try:
            template = self.cache.read_converter_template(share_id, format, key)
            if time.time() - self.cache.converter_mtime(share_id, format, key) <= self.cache_ttl:
                return self._finalize(
                    self._restore_raw_url(template, output_raw_url), format, output_raw_url, params
                )
        except OSError:
            template = None
        surge_params = self._surge_node_params(source_digest) if format == "surge" else {}
        try:
            request_params = {"target": format, "url": raw_url, "expand": "true"}
            if format == "surge":
                request_params["ver"] = "4"
            request_params.update(params or {})
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20, follow_redirects=True
            ) as client:
                response = await client.get(f"{self.base_url}/sub", params=request_params)
            response.raise_for_status()
            sanitized = self._validate_and_sanitize(response.text, raw_url, format, surge_params)
            self.cache.write_converter_template(share_id, sanitized, format, key)
            return self._finalize(
                self._restore_raw_url(sanitized, output_raw_url), format, output_raw_url, params
            )
        except (httpx.HTTPError, ValueError, json.JSONDecodeError, yaml.YAMLError, OSError) as exc:
            if template is not None:
                return self._finalize(
                    self._restore_raw_url(template, output_raw_url), format, output_raw_url, params
                )
            raise RuntimeError("converter unavailable") from exc

    async def diagnostics(self) -> dict:
        """回环上游的版本与统计摘要；上游未响应时只报不可用，不让管理页也跟着报错。"""
        result: dict[str, object] = {
            "available": False,
            "version": None,
            "commit": None,
            "statistics": None,
        }
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=10, follow_redirects=True
            ) as client:
                page = await client.get(f"{self.base_url}/version")
                dashboard = await client.get(f"{self.base_url}/dashboard/data")
        except httpx.HTTPError:
            return result
        if page.status_code == 200:
            result["available"] = True
            version = VERSION_PATTERN.search(page.text)
            commit = COMMIT_PATTERN.search(page.text)
            result["version"] = version.group(1) if version else None
            result["commit"] = commit.group(1) if commit else None
        if dashboard.status_code == 200:
            try:
                result["statistics"] = _statistics_summary(dashboard.json())
            except (json.JSONDecodeError, ValueError):
                pass
        return result
