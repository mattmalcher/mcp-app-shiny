import { describe, expect, it } from "vitest";
import {
  checkSandboxUrl,
  clampHeight,
  innerSandbox,
  isExpectedProxyReady,
  randomToken,
  safeLinkUrl,
  sandboxPageUrl,
  sanitizeInnerSandbox,
} from "../src/validation";

describe("safeLinkUrl", () => {
  it("accepts http(s)", () => {
    expect(safeLinkUrl("https://example.com/a?b=1")).toBe("https://example.com/a?b=1");
    expect(safeLinkUrl(" http://localhost:3000 ")).toBe("http://localhost:3000/");
  });
  it.each(["javascript:alert(1)", "JAVASCRIPT:alert(1)", "file:///etc/passwd", "data:text/html,x", "blob:x", "", "/relative", 42, null])(
    "rejects %s",
    (url) => expect(safeLinkUrl(url)).toBeNull(),
  );
});

describe("sandbox iframe attributes", () => {
  it("never grants escapes", () => {
    expect(innerSandbox(false)).toBe("allow-scripts allow-forms");
    expect(innerSandbox(true)).toContain("allow-same-origin");
    const s = sanitizeInnerSandbox("allow-top-navigation allow-popups allow-popups-to-escape-sandbox allow-modals allow-forms");
    expect(s).toBe("allow-scripts allow-forms");
    expect(sanitizeInnerSandbox(undefined)).toBe("allow-scripts allow-forms");
  });
});

describe("checkSandboxUrl", () => {
  const page = "http://127.0.0.1:8000";
  it("requires a distinct origin", () => {
    expect(checkSandboxUrl("http://127.0.0.1:8000/sandbox.html", page, false)).toMatchObject({ ok: false });
    expect(checkSandboxUrl("http://127.0.0.1:8000/other/path/sandbox.html", page, false)).toMatchObject({ ok: false });
  });
  it("fails closed when missing or invalid", () => {
    expect(checkSandboxUrl("", page, false).ok).toBe(false);
    expect(checkSandboxUrl(undefined, page, false).ok).toBe(false);
    expect(checkSandboxUrl("javascript:alert(1)", page, false).ok).toBe(false);
    expect(checkSandboxUrl("not a url", page, false).ok).toBe(false);
  });
  it("accepts a different hostname", () => {
    expect(checkSandboxUrl("http://localhost:8765/sandbox.html", page, true)).toEqual({ ok: true, origin: "http://localhost:8765" });
  });
  it("warns on same hostname, refuses it for same-origin Views", () => {
    const r = checkSandboxUrl("http://127.0.0.1:8765/sandbox.html", page, false);
    expect(r.ok && r.warning).toBeTruthy();
    expect(checkSandboxUrl("http://127.0.0.1:8765/sandbox.html", page, true).ok).toBe(false);
  });
  it("requires https sandbox for https apps", () => {
    expect(checkSandboxUrl("http://sandbox.example/sandbox.html", "https://app.example", false).ok).toBe(false);
  });
});

describe("sandboxPageUrl", () => {
  it("adds host, nonce and csp", () => {
    const u = new URL(sandboxPageUrl("http://localhost:8765/sandbox.html", "http://127.0.0.1:8000", "n".repeat(32), { connectDomains: ["https://a.example"] }));
    expect(u.searchParams.get("host")).toBe("http://127.0.0.1:8000");
    expect(JSON.parse(u.searchParams.get("csp")!)).toEqual({ connectDomains: ["https://a.example"] });
  });
  it("omits empty csp", () => {
    expect(sandboxPageUrl("http://localhost:8765/sandbox.html", "http://x", "n", {})).not.toContain("csp=");
  });
});

describe("isExpectedProxyReady", () => {
  const src = {};
  const ok = { jsonrpc: "2.0", method: "ui/notifications/sandbox-proxy-ready", params: { nonce: "abc" } };
  it("checks source, origin and nonce", () => {
    expect(isExpectedProxyReady({ source: src, origin: "http://s", data: ok }, src, "http://s", "abc")).toBe(true);
    expect(isExpectedProxyReady({ source: {}, origin: "http://s", data: ok }, src, "http://s", "abc")).toBe(false);
    expect(isExpectedProxyReady({ source: src, origin: "http://evil", data: ok }, src, "http://s", "abc")).toBe(false);
    expect(isExpectedProxyReady({ source: src, origin: "http://s", data: { ...ok, params: { nonce: "x" } } }, src, "http://s", "abc")).toBe(false);
    expect(isExpectedProxyReady({ source: src, origin: "http://s", data: null }, src, "http://s", "abc")).toBe(false);
  });
});

it("randomToken / clampHeight", () => {
  expect(randomToken(16)).toMatch(/^[0-9a-f]{32}$/);
  expect(randomToken()).not.toBe(randomToken());
  expect(clampHeight(123.4)).toBe(123);
  expect(clampHeight(1e9)).toBe(4000);
  expect(clampHeight(-5)).toBe(0);
  expect(clampHeight("100")).toBeNull();
  expect(clampHeight(Number.NaN)).toBeNull();
});
