"""Prowlarr API helpers and result utilities."""

import hashlib
import logging

import requests

from db import get_setting

log = logging.getLogger("prowlarr-watcher")


def prowlarr_link_base() -> str:
    """Return the base URL to use for browser links (external URL if configured, else API URL)."""
    external = get_setting("prowlarr_external_url", "").rstrip("/")
    return external or get_setting("prowlarr_url", "").rstrip("/")


def prowlarr_search_raw(query: str, categories: list[int] | None = None) -> list[dict]:
    base = get_setting("prowlarr_url").rstrip("/")
    api_key = get_setting("prowlarr_api_key")
    if not base or not api_key:
        raise ValueError("Prowlarr URL and API key must be configured in Settings")

    params: dict = {"query": query}
    if categories:
        params["categories"] = categories

    timeout = int(get_setting("prowlarr_timeout", "200"))
    resp = requests.get(
        f"{base}/api/v1/search",
        headers={"X-Api-Key": api_key},
        params=params,
        timeout=timeout,
    )
    resp.raise_for_status()
    results = resp.json()
    log.info("Search %r → %d results", query, len(results))
    return results


def hash_result(r: dict) -> str:
    key = r.get("guid") or f"{r.get('title', '')}|{r.get('size', '')}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def format_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"
