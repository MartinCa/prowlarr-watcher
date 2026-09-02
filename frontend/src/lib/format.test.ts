import { describe, expect, it } from "vitest";

import { formatSize, sanitizeUrl } from "./format";

describe("formatSize", () => {
  it("formats bytes up to PB", () => {
    expect(formatSize(512)).toBe("512.0 B");
    expect(formatSize(1024)).toBe("1.0 KB");
    expect(formatSize(5.5 * 1024 * 1024)).toBe("5.5 MB");
    expect(formatSize(2 * 1024 ** 3)).toBe("2.0 GB");
  });

  it("handles falsy input", () => {
    expect(formatSize(null)).toBe("—");
    expect(formatSize(undefined)).toBe("—");
    expect(formatSize(0)).toBe("—");
  });
});

describe("sanitizeUrl", () => {
  it("allows http, https and magnet", () => {
    expect(sanitizeUrl("https://example.com/a")).toBe("https://example.com/a");
    expect(sanitizeUrl("http://example.com")).toBe("http://example.com");
    expect(sanitizeUrl("magnet:?xt=urn:btih:abc")).toBe("magnet:?xt=urn:btih:abc");
  });

  it("rejects dangerous and malformed URLs", () => {
    expect(sanitizeUrl("javascript:alert(1)")).toBeNull();
    expect(sanitizeUrl("data:text/html,<script>1</script>")).toBeNull();
    expect(sanitizeUrl("ftp://example.com")).toBeNull();
    expect(sanitizeUrl("not a url")).toBeNull();
    expect(sanitizeUrl(null)).toBeNull();
  });
});
