import { afterEach, describe, expect, it, vi } from "vitest";

import { api, request } from "./api";

function fetchInit(): RequestInit {
  const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
  expect(url).toBeDefined();
  return init;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("header merging", () => {
  it("merges a Headers instance passed as options.headers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));

    await api.get("/ping", { headers: new Headers({ "X-Custom": "from-instance" }) });

    const headers = new Headers(fetchInit().headers);
    expect(headers.get("X-Custom")).toBe("from-instance");
    expect(headers.get("Accept")).toBe("application/json");
  });

  it("merges an array-of-pairs HeadersInit", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));

    await api.get("/ping", { headers: [["X-Custom", "from-array"]] });

    const headers = new Headers(fetchInit().headers);
    expect(headers.get("X-Custom")).toBe("from-array");
  });

  it("lets caller-supplied headers override the defaults", async () => {
    vi.stubGlobal("document", { cookie: "csrf_token=x" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));

    await api.post("/ping", { ok: true }, { headers: { Accept: "text/plain" } });

    const headers = new Headers(fetchInit().headers);
    expect(headers.get("Accept")).toBe("text/plain");
    expect(headers.get("Content-Type")).toBe("application/json");
  });
});

describe("CSRF behavior", () => {
  it("attaches X-CSRF-Token from the cookie on mutating requests", async () => {
    vi.stubGlobal("document", { cookie: "csrf_token=abc%2Bdef" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));

    await api.post("/queries", { name: "test" });

    const headers = new Headers(fetchInit().headers);
    expect(headers.get("X-CSRF-Token")).toBe("abc+def");
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("omits the CSRF header on safe methods and keeps caller headers", async () => {
    vi.stubGlobal("document", { cookie: "csrf_token=secret" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));

    await api.get("/queries", { headers: { "X-Custom": "kept" } });

    const headers = new Headers(fetchInit().headers);
    expect(headers.get("X-CSRF-Token")).toBeNull();
    expect(headers.get("X-Custom")).toBe("kept");
  });

  it("skips the CSRF header when no cookie is present", async () => {
    vi.stubGlobal("document", { cookie: "other=value" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));

    await request("/queries", { method: "POST", body: { name: "test" } });

    const headers = new Headers(fetchInit().headers);
    expect(headers.get("X-CSRF-Token")).toBeNull();
  });
});

describe("bodiless and empty responses", () => {
  it("treats a 204 as undefined", async () => {
    vi.stubGlobal("document", { cookie: "csrf_token=x" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(api.delete<undefined>("/queries/1")).resolves.toBeUndefined();
  });

  it("treats a 205 as undefined", async () => {
    vi.stubGlobal("document", { cookie: "csrf_token=x" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 205 })));

    await expect(api.post<undefined>("/reset")).resolves.toBeUndefined();
  });

  it("treats a 200 with an empty body as undefined instead of throwing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 200 })));

    await expect(api.get<undefined>("/empty")).resolves.toBeUndefined();
  });

  it("parses a non-empty JSON body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response('{"ok":true}', {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api.get<{ ok: boolean }>("/ping")).resolves.toEqual({ ok: true });
  });

  it("still throws ApiError for error responses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("", {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api.get("/broken")).rejects.toMatchObject({ name: "ApiError", status: 500 });
  });
});
