"""Apprise notification helpers."""

import logging
from urllib.parse import quote

import apprise

from db import get_db, get_setting
from prowlarr import format_size

log = logging.getLogger("prowlarr-watcher")


def notify_new_results(name: str, query: str, new_items: list[dict]):
    raw_urls = get_setting("apprise_urls", "")
    urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]
    if not urls:
        log.info("No Apprise URLs configured, skipping notification")
        return

    count = len(new_items)
    title = f"[Prowlarr] {count} new result{'s' if count != 1 else ''} — {name}"
    lines = []
    for r in new_items[:20]:
        size_str = format_size(r.get("size"))
        lines.append(f"• {r.get('title', '?')}  [{r.get('indexer', '?')}] [{size_str}]")
    if count > 20:
        lines.append(f"  … and {count - 20} more")
    body = "\n".join(lines)
    prowlarr_base = get_setting("prowlarr_url", "").rstrip("/")
    if prowlarr_base:
        body = f"🔍 {prowlarr_base}/search?query={quote(query)}\n\n{body}"

    ap = apprise.Apprise()
    for u in urls:
        ap.add(u)
    ap.notify(title=title, body=body)
    log.info("Notification sent: %s", title)


def notify_error(qid: int, query_text: str | None, run_type: str, error: str):
    raw_urls = get_setting("apprise_urls", "")
    urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]
    if not urls:
        return

    if not query_text:
        with get_db() as conn:
            row = conn.execute("SELECT name, query FROM queries WHERE id=?", (qid,)).fetchone()
        name = row["name"] if row else f"Q{qid}"
        query_text = row["query"] if row else "?"
    else:
        name = query_text

    title = f"[Prowlarr] Search failed — {name}"
    body = f"Type: {run_type}\nQuery: {query_text}\nError: {error}"

    ap = apprise.Apprise()
    for u in urls:
        ap.add(u)
    ap.notify(title=title, body=body)
    log.info("Error notification sent for Q%d: %s", qid, error)
