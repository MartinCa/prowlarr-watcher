import cronstrue from "cronstrue";
import { formatDistanceToNow } from "date-fns";

export function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = bytes;
  for (const unit of units) {
    if (size < 1024) return `${size.toFixed(1)} ${unit}`;
    size /= 1024;
  }
  return `${size.toFixed(1)} PB`;
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  try {
    return formatDistanceToNow(new Date(iso), { addSuffix: true });
  } catch {
    return iso;
  }
}

export function describeCron(expr: string): string {
  try {
    return cronstrue.toString(expr, { use24HourTimeFormat: true });
  } catch {
    return "";
  }
}

export function cronGuruUrl(expr: string): string {
  return `https://crontab.guru/#${expr.trim().replace(/ /g, "_")}`;
}

/** Matches prowlarr.py's prowlarr_link_base(): external URL if set, else the base URL. */
export function prowlarrLinkBase(settings: { prowlarrUrl: string; prowlarrExternalUrl: string }) {
  return (settings.prowlarrExternalUrl || settings.prowlarrUrl).replace(/\/$/, "");
}

/**
 * Sanitizes URLs from external / untrusted sources.
 * Only allows http:, https:, and magnet: protocols.
 * Rejects javascript:, data:, relative URLs, or malformed strings.
 */
export function sanitizeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (
      parsed.protocol === "http:" ||
      parsed.protocol === "https:" ||
      parsed.protocol === "magnet:"
    ) {
      return url;
    }
  } catch {
    // Malformed URL
  }
  return null;
}
