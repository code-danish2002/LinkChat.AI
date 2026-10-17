"""URL validation and normalisation shared by the ingestion layer."""

from __future__ import annotations

import ipaddress
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.core.exceptions import ValidationError

_TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


def normalize_url(raw: str) -> str:
    """Validate a user-supplied URL and return a canonical form.

    - adds `https://` when the scheme is missing
    - lower-cases scheme and host, strips fragments and tracking parameters
    - rejects non-http(s) schemes and private/loopback hosts (SSRF guard)
    """
    candidate = (raw or "").strip()
    if not candidate:
        raise ValidationError("Please provide a URL.")
    if "://" not in candidate:
        candidate = "https://" + candidate

    parts = urlsplit(candidate)
    if parts.scheme not in {"http", "https"}:
        raise ValidationError("Only http and https URLs are supported.")
    if not parts.hostname:
        raise ValidationError("The URL has no host name.")

    host = parts.hostname.lower()
    if host in {"localhost"} or host.endswith(".local"):
        raise ValidationError("Local network addresses are not allowed.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        raise ValidationError("Private network addresses are not allowed.")

    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _TRACKING_PARAMS]
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme, netloc, path, urlencode(query), ""))
