"""Institutional proxy and WebVPN / EZProxy URL transformation for academic literature.

Converts public publisher and DOI URLs into institutional access endpoints (e.g.,
campus WebVPN path proxy, EZProxy login prefix, or custom URI templates) without
storing or handling private credentials.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit

SENSITIVE_PARAMS = {
    "api_key", "apikey", "key", "token", "access_token", "email", "mailto",
    "signature", "credential", "x-amz-signature", "x-amz-credential", "x-amz-security-token",
    "password", "secret"
}


def sanitize_url(url: str) -> str:
    """Strip embedded credentials and sensitive tokens from target URL."""
    if not url:
        return ""
    parts = urlsplit(url)
    clean_query = [
        (k, "[REDACTED]" if k.lower() in SENSITIVE_PARAMS else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    ]
    query_str = urlencode(clean_query)
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, query_str, ""))


def detect_proxy_type(proxy_pattern: str) -> str:
    """Detect institutional proxy strategy from configuration string."""
    if not proxy_pattern:
        return "none"
    p = proxy_pattern.strip()
    if "{host_dashed}" in p:
        return "webvpn_host"
    if "{url}" in p or "{host}" in p or "{doi}" in p:
        return "template"
    if "login?url=" in p.lower() or p.endswith(("url=", "url?")):
        return "ezproxy_prefix"
    if "/https/" in p.lower() or "webvpn" in p.lower():
        return "webvpn_path"
    return "ezproxy_prefix"


def transform_url(target_url: str, proxy_pattern: str | None, doi: str | None = None) -> dict:
    """Transform target publisher URL into an institutional access URL."""
    if not proxy_pattern or not proxy_pattern.strip():
        return {"status": "disabled", "institutional_url": None, "proxy_type": "none"}
    
    clean_target = sanitize_url(target_url.strip())
    parts = urlsplit(clean_target)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return {"status": "invalid_target_url", "institutional_url": None, "proxy_type": "none"}

    proxy = proxy_pattern.strip()
    ptype = detect_proxy_type(proxy)
    path_with_query = parts.path + (("?" + parts.query) if parts.query else "")

    if ptype == "template":
        norm_doi = doi.strip() if doi else ""
        res = proxy.replace("{url}", clean_target)
        res = res.replace("{host}", parts.hostname)
        res = res.replace("{host_dashed}", parts.hostname.replace(".", "-"))
        res = res.replace("{path}", path_with_query)
        res = res.replace("{doi}", norm_doi)
        inst_url = res
    elif ptype == "webvpn_host":
        # Sangfor host-rewrite proxy, e.g. https://www-sciencedirect-com-s.vpn.scau.edu.cn/path
        res = proxy.replace("{host}", parts.hostname)
        res = res.replace("{host_dashed}", parts.hostname.replace(".", "-"))
        res = res.replace("{path}", path_with_query)
        res = res.replace("{doi}", doi.strip() if doi else "")
        inst_url = res
    elif ptype == "webvpn_path":
        # Sangfor WebVPN format: https://webvpn.univ.edu.cn/https/{target_host}{path_query}
        base = re.sub(r"/https/?$", "", proxy.rstrip("/"))
        inst_url = f"{base}/https/{parts.hostname}{path_with_query}"
    else:  # ezproxy_prefix
        if proxy.endswith(("url=", "url?")):
            inst_url = f"{proxy}{clean_target}"
        elif "login?url=" in proxy.lower():
            inst_url = f"{proxy.rstrip('/')}/{clean_target}" if not proxy.endswith("=") else f"{proxy}{clean_target}"
        else:
            base = proxy.rstrip("/")
            inst_url = f"{base}/login?url={clean_target}"

    return {
        "status": "configured",
        "proxy_type": ptype,
        "proxy_pattern": proxy,
        "target_url": clean_target,
        "institutional_url": inst_url,
    }


def get_configured_proxy(cli_value: str | None = None) -> str | None:
    """Resolve configured institutional proxy from CLI argument or environment variables."""
    if cli_value and cli_value.strip():
        return cli_value.strip()
    for env in ("INSTITUTIONAL_PROXY_URL", "WEBVPN_URL", "EZPROXY_URL"):
        val = os.environ.get(env)
        if val and val.strip():
            return val.strip()
    return None
