"""Strict URL validation shared by local-only generation configuration."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit, urlunsplit


def normalize_loopback_http_origin(value: str) -> str:
    """Return one credential-free numeric loopback HTTP origin.

    Hostnames are deliberately rejected so runtime DNS cannot widen the approved egress
    boundary.  Paths, queries, fragments, and user information are likewise forbidden.
    """

    raw = value.strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
        host = parts.hostname
        address = ipaddress.ip_address(host) if host is not None else None
    except (ValueError, TypeError):
        raise ValueError(
            "local provider endpoint must be one numeric loopback HTTP origin"
        ) from None
    if (
        parts.scheme != "http"
        or address is None
        or not address.is_loopback
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("local provider endpoint must be one numeric loopback HTTP origin")
    canonical_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    netloc = canonical_host if port is None else f"{canonical_host}:{port}"
    return urlunsplit(("http", netloc, "", "", ""))


__all__ = ["normalize_loopback_http_origin"]
