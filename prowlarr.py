"""Prowlarr API helpers and result utilities."""

import hashlib
import logging
import time

import requests

from db import get_setting

log = logging.getLogger("prowlarr-watcher")

_INDEXER_CACHE_TTL = 120.0
_indexer_cache: dict = {"time": None, "indexers": []}


def prowlarr_link_base() -> str:
    """Return the base URL to use for browser links (external URL if configured, else API URL)."""
    external = get_setting("prowlarr_external_url", "").rstrip("/")
    return external or get_setting("prowlarr_url", "").rstrip("/")


def list_indexers(force: bool = False) -> list[dict]:
    """Return configured Prowlarr indexers as [{id, name, enable}, ...], cached briefly."""
    now = time.monotonic()
    cached_at = _indexer_cache["time"]
    if not force and cached_at is not None and (now - cached_at) < _INDEXER_CACHE_TTL:
        return _indexer_cache["indexers"]

    base = get_setting("prowlarr_url").rstrip("/")
    api_key = get_setting("prowlarr_api_key")
    if not base or not api_key:
        raise ValueError("Prowlarr URL and API key must be configured in Settings")

    timeout = int(get_setting("prowlarr_timeout", "200"))
    resp = requests.get(
        f"{base}/api/v1/indexer",
        headers={"X-Api-Key": api_key},
        timeout=timeout,
    )
    resp.raise_for_status()
    indexers = [
        {"id": i["id"], "name": i["name"], "enable": i.get("enable", True)} for i in resp.json()
    ]
    _indexer_cache["time"] = now
    _indexer_cache["indexers"] = indexers
    return indexers


def parse_indexer_ids(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip()]


def format_indexer_ids(ids: list[int]) -> str:
    return ",".join(str(i) for i in ids)


def effective_excluded_indexers(override: str | None) -> list[int]:
    """Resolve a query's excluded-indexer override (None = inherit the default list)."""
    raw = override if override is not None else get_setting("default_excluded_indexers", "")
    return parse_indexer_ids(raw)


def prowlarr_search_raw(
    query: str,
    categories: list[int] | None = None,
    excluded_indexer_ids: list[int] | None = None,
) -> list[dict]:
    base = get_setting("prowlarr_url").rstrip("/")
    api_key = get_setting("prowlarr_api_key")
    if not base or not api_key:
        raise ValueError("Prowlarr URL and API key must be configured in Settings")

    params: dict = {"query": query}
    if categories:
        params["categories"] = categories
    if excluded_indexer_ids:
        excluded = set(excluded_indexer_ids)
        params["indexerIds"] = [i["id"] for i in list_indexers() if i["id"] not in excluded]

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
