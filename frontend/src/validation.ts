/**
 * Pure validation helpers shared by the host adapter and the sandbox proxy.
 * Kept free of DOM/Shiny dependencies so they can be unit tested.
 */

/** Sandbox tokens the inner View iframe may ever receive. */
export const INNER_SANDBOX_ALLOWED = ["allow-scripts", "allow-forms", "allow-same-origin"] as const;

/** Sandbox attribute of the outer proxy iframe (spec: allow-scripts + allow-same-origin). */
export const OUTER_SANDBOX = "allow-scripts allow-same-origin allow-forms";

/** Sandbox attribute of the single View iframe in trusted mode: opaque origin, no escapes. */
export const TRUSTED_VIEW_SANDBOX = "allow-scripts allow-forms";

export const SANDBOX_METHOD_PREFIX = "ui/notifications/sandbox-";
export const PROXY_READY = "ui/notifications/sandbox-proxy-ready";
export const RESOURCE_READY = "ui/notifications/sandbox-resource-ready";

/** Returns the URL if it is an absolute http(s) URL, otherwise null. */
export function safeLinkUrl(raw: unknown): string | null {
  if (typeof raw !== "string" || raw.length > 4096) return null;
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (!url.host) return null;
  return url.href;
}

/** The inner iframe sandbox string; never includes navigation/popup escapes. */
export function innerSandbox(viewSameOrigin: boolean): string {
  return viewSameOrigin ? "allow-scripts allow-forms allow-same-origin" : "allow-scripts allow-forms";
}

/** Filter an arbitrary sandbox string down to the allowed inner tokens. */
export function sanitizeInnerSandbox(value: unknown): string {
  if (typeof value !== "string") return innerSandbox(false);
  const tokens = value.split(/\s+/).filter((t) => (INNER_SANDBOX_ALLOWED as readonly string[]).includes(t));
  if (!tokens.includes("allow-scripts")) tokens.unshift("allow-scripts");
  return Array.from(new Set(tokens)).join(" ");
}

export type SandboxCheck =
  | { ok: true; origin: string; warning?: string }
  | { ok: false; reason: string };

/**
 * Validate the configured sandbox URL against the page origin. Fails closed:
 * the sandbox must be on a different origin, and when the View is allowed
 * same-origin access it must also be on a different hostname (cookies are
 * not isolated by port).
 */
export function checkSandboxUrl(sandboxUrl: unknown, pageOrigin: string, viewSameOrigin: boolean): SandboxCheck {
  if (typeof sandboxUrl !== "string" || !sandboxUrl) {
    return { ok: false, reason: "No sandbox URL configured" };
  }
  let url: URL;
  let page: URL;
  try {
    url = new URL(sandboxUrl);
    page = new URL(pageOrigin);
  } catch {
    return { ok: false, reason: "Invalid sandbox URL" };
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return { ok: false, reason: "Sandbox URL must be http(s)" };
  }
  if (url.origin === page.origin) {
    return { ok: false, reason: `Sandbox origin ${url.origin} must differ from the app origin` };
  }
  if (page.protocol === "https:" && url.protocol !== "https:") {
    return { ok: false, reason: "An https app requires an https sandbox" };
  }
  if (url.hostname === page.hostname) {
    if (viewSameOrigin) {
      return {
        ok: false,
        reason: "view_same_origin requires the sandbox on a different hostname (cookies are shared across ports)",
      };
    }
    return {
      ok: true,
      origin: url.origin,
      warning: "Sandbox shares the app hostname; use a different hostname to isolate cookies",
    };
  }
  return { ok: true, origin: url.origin };
}

/**
 * Prefix the View's HTML with a document start the host controls: a doctype
 * and a `<meta>` Content-Security-Policy, which the parser places in the
 * head before it reads a single byte of the View. Used in trusted mode,
 * where there is no sandbox server to send the policy as a header.
 *
 * The View's HTML is never searched (a `<head>` inside a comment or a
 * script, or a script before it, would defeat that). Its own doctype and
 * `<html>`/`<head>` tags arrive after the head has started, where HTML
 * parsing ignores them (attributes on `<html>` are merged), so the View's
 * head content still ends up in the head. A View without a doctype renders
 * in standards mode rather than quirks mode.
 */
export function withMetaCsp(html: string, policy: string): string {
  const escaped = policy.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  return `<!doctype html><meta http-equiv="Content-Security-Policy" content="${escaped}">${html}`;
}

/** Build the sandbox page URL for one mount. */
export function sandboxPageUrl(sandboxUrl: string, hostOrigin: string, nonce: string, csp: unknown): string {
  const url = new URL(sandboxUrl);
  url.searchParams.set("host", hostOrigin);
  url.searchParams.set("nonce", nonce);
  if (csp && typeof csp === "object" && Object.keys(csp as object).length > 0) {
    url.searchParams.set("csp", JSON.stringify(csp));
  }
  return url.href;
}

/** Cryptographically random URL-safe token. */
export function randomToken(bytes = 16): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return Array.from(buf, (b) => b.toString(16).padStart(2, "0")).join("");
}

export interface ProxyReadyCheck {
  source: unknown;
  origin: string;
  data: unknown;
}

/** True if a message is the expected sandbox-proxy-ready for this frame/nonce. */
export function isExpectedProxyReady(
  ev: ProxyReadyCheck,
  expectedSource: unknown,
  expectedOrigin: string,
  nonce: string,
): boolean {
  if (ev.source !== expectedSource || ev.origin !== expectedOrigin) return false;
  const data = ev.data as { jsonrpc?: string; method?: string; params?: { nonce?: unknown } } | null;
  return !!data && data.jsonrpc === "2.0" && data.method === PROXY_READY && data.params?.nonce === nonce;
}

/** Clamp a View-requested height to sane bounds. */
export function clampHeight(height: unknown, max = 4000): number | null {
  if (typeof height !== "number" || !Number.isFinite(height)) return null;
  return Math.max(0, Math.min(Math.round(height), max));
}
