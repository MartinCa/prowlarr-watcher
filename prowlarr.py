"""Prowlarr API helpers and result utilities."""

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

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


# Allowance for clock differences between this host and Prowlarr when comparing timestamps.
_STATUS_CLOCK_SKEW = timedelta(seconds=30)


def unhealthy_indexer_names(since: datetime) -> set[str]:
    """Names of indexers whose results can't be trusted for a search that started at `since`.

    Prowlarr's search response doesn't say which indexers failed, but it records every
    indexer failure (timeout, rate limit, ...) in its indexer status. An indexer counts as
    unhealthy if it is disabled, backed off (``disabledTill`` in the future) or has a
    failure at or after `since`. Raises on any Prowlarr/request error so callers can fail
    safe rather than treat missing results as removed.
    """
    base = get_setting("prowlarr_url").rstrip("/")
    api_key = get_setting("prowlarr_api_key")
    if not base or not api_key:
        raise ValueError("Prowlarr URL and API key must be configured in Settings")

    timeout = int(get_setting("prowlarr_timeout", "200"))
    resp = requests.get(
        f"{base}/api/v1/indexerstatus", headers={"X-Api-Key": api_key}, timeout=timeout
    )
    resp.raise_for_status()
    indexers = list_indexers(force=True)
    names = {i["id"]: i["name"] for i in indexers}
    now = datetime.now(timezone.utc)
    cutoff = since - _STATUS_CLOCK_SKEW

    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    unhealthy = {i["name"] for i in indexers if not i["enable"]}
    for st in resp.json():
        name = names.get(st.get("indexerId"))
        if name is None:
            continue
        try:
            disabled_till = _parse(st.get("disabledTill"))
            last_failure = _parse(st.get("mostRecentFailure"))
        except ValueError:
            unhealthy.add(name)  # unparseable timestamp: assume the worst
            continue
        if (disabled_till and disabled_till > now) or (last_failure and last_failure >= cutoff):
            unhealthy.add(name)
    return unhealthy


def prowlarr_search_raw(
    query: str,
    categories: list[int] | None = None,
    excluded_indexer_ids: list[int] | None = None,
    indexer_ids: list[int] | None = None,
) -> list[dict]:
    base = get_setting("prowlarr_url").rstrip("/")
    api_key = get_setting("prowlarr_api_key")
    if not base or not api_key:
        raise ValueError("Prowlarr URL and API key must be configured in Settings")

    params: dict = {"query": query}
    if categories:
        params["categories"] = categories
    if indexer_ids:
        params["indexerIds"] = indexer_ids
    elif excluded_indexer_ids:
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


class GrabError(Exception):
    """A failed grab, with a message fit to show the user as-is."""


def _prowlarr_error_message(resp: requests.Response) -> str:
    """Best human-readable message from a failed Prowlarr response."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    if isinstance(data, list) and data:
        data = data[0]  # validation failures come back as a list of {errorMessage, ...}
    if isinstance(data, dict):
        for key in ("message", "errorMessage", "detail", "title"):
            if data.get(key):
                return str(data[key])
    return resp.text.strip()[:300] or f"HTTP {resp.status_code}"


def grab_release(query: str, guid: str, indexer_name: str | None) -> str:
    """Send a release to the download client via Prowlarr, as Prowlarr's own UI does.

    Prowlarr only grabs releases still in its short-lived search cache (that is also where
    the indexer's fresh download token/link comes from), so the query is searched again first
    and the release is picked from the fresh results.
    """
    base = get_setting("prowlarr_url").rstrip("/")
    api_key = get_setting("prowlarr_api_key")
    if not base or not api_key:
        raise GrabError("Prowlarr URL and API key must be configured in Settings")

    try:
        indexer_ids = [i["id"] for i in list_indexers() if i["name"] == indexer_name]
        if not indexer_ids:
            raise GrabError(f"Indexer {indexer_name!r} is no longer configured in Prowlarr")
        fresh = prowlarr_search_raw(query, indexer_ids=indexer_ids)
    except requests.exceptions.RequestException as exc:
        raise GrabError(f"Could not refresh the release from Prowlarr: {exc}") from exc

    match = next(
        (r for r in fresh if r.get("guid") == guid and r.get("indexer") == indexer_name), None
    )
    if not match or match.get("indexerId") is None:
        raise GrabError("The indexer no longer returns this release — it may have been removed")

    timeout = int(get_setting("prowlarr_timeout", "200"))
    try:
        resp = requests.post(
            f"{base}/api/v1/search",
            headers={"X-Api-Key": api_key},
            json={"guid": guid, "indexerId": match["indexerId"]},
            timeout=timeout,
        )
    except requests.exceptions.RequestException as exc:
        raise GrabError(f"Could not reach Prowlarr: {exc}") from exc
    if not resp.ok:
        message = _prowlarr_error_message(resp)
        if "failed to connect to qbittorrent" in message.lower():
            # qBittorrent answers "Fails." for a torrent it already has, and Prowlarr reports
            # that as a connection failure, so the real cause is ambiguous.
            message += " (This also appears when the torrent is already in qBittorrent.)"
        raise GrabError(message)
    return "Sent to the download client via Prowlarr"


def hash_result(r: dict) -> str:
    """Identity of a result: the indexer plus its guid (or title|size as fallback).

    The indexer is part of the key so the same release appearing on a second indexer
    (e.g. a freeleech-filtered duplicate of a tracker) counts as a new result.
    """
    base = r.get("guid") or f"{r.get('title', '')}|{r.get('size', '')}"
    key = f"{r.get('indexer') or ''}|{base}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def format_size(size_bytes: int | None) -> str:
    if not size_bytes:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def sanitize_url(url: str | None) -> str | None:
    """Sanitize URLs from external indexers; only allow http, https, and magnet."""
    if not url:
        return None
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() in ("http", "https", "magnet"):
            return url.strip()
    except Exception:
        pass
    return None
