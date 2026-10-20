/**
 * Outer sandbox proxy (served as sandbox.js from the sandbox origin).
 *
 * Host (Shiny page) <-> this proxy (distinct origin) <-> View (inner iframe).
 * Mirrors the official ext-apps basic-host proxy, with stricter defaults:
 * - the expected host origin and a per-mount nonce come from the URL (the
 *   sandbox server only serves allowed host origins and sets
 *   `frame-ancestors` accordingly);
 * - the inner View runs without `allow-same-origin` (opaque origin) unless
 *   the host explicitly opts in;
 * - the View can never send or receive `ui/notifications/sandbox-*` messages.
 */
import { buildAllowAttribute } from "@modelcontextprotocol/ext-apps/app-bridge";
import { PROXY_READY, RESOURCE_READY, SANDBOX_METHOD_PREFIX, sanitizeInnerSandbox } from "./validation";

function fail(message: string): never {
  console.error("[shiny-mcp sandbox]", message);
  throw new Error(message);
}

const params = new URLSearchParams(window.location.search);
const HOST_ORIGIN = params.get("host") ?? "";
const NONCE = params.get("nonce") ?? "";
const OWN_ORIGIN = window.location.origin;

if (window.self === window.top) fail("The sandbox must be embedded in an iframe.");
if (!HOST_ORIGIN || !NONCE) fail("Missing host origin or nonce.");
if (HOST_ORIGIN === OWN_ORIGIN) fail("Sandbox origin must differ from the host origin.");
if (document.referrer && new URL(document.referrer).origin !== HOST_ORIGIN) {
  fail(`Unexpected embedding page ${new URL(document.referrer).origin}`);
}

// Self-test: the host document must not be reachable from here.
try {
  void window.top!.document.cookie;
  throw "FAIL";
} catch (e) {
  if (e === "FAIL") fail("Sandbox is not isolated from the host: refusing to run.");
}

const inner = document.createElement("iframe");
inner.setAttribute("title", "MCP App view");
inner.setAttribute("sandbox", "allow-scripts allow-forms");
document.body.appendChild(inner);

let loaded = false;
let innerSameOrigin = false;

function loadView(data: { params?: Record<string, unknown> }) {
  if (loaded) {
    console.warn("[shiny-mcp sandbox] Ignoring repeated resource-ready");
    return;
  }
  const p = data.params ?? {};
  if (typeof p.html !== "string") return;
  loaded = true;
  const sandbox = sanitizeInnerSandbox(p.sandbox);
  innerSameOrigin = sandbox.split(" ").includes("allow-same-origin");
  inner.setAttribute("sandbox", sandbox);
  const allow = buildAllowAttribute(p.permissions as never);
  if (allow) inner.setAttribute("allow", allow);
  if (innerSameOrigin) {
    // Same strategy as upstream basic-host (some Views break under srcdoc).
    const doc = inner.contentDocument;
    if (doc) {
      doc.open();
      doc.write(p.html);
      doc.close();
      return;
    }
  }
  // Opaque-origin View; srcdoc documents inherit this page's CSP.
  inner.srcdoc = p.html;
}

function isSandboxMethod(data: unknown): boolean {
  const method = (data as { method?: unknown } | null)?.method;
  return typeof method === "string" && method.startsWith(SANDBOX_METHOD_PREFIX);
}

window.addEventListener("message", (event) => {
  if (event.source === window.parent) {
    if (event.origin !== HOST_ORIGIN) {
      console.error("[shiny-mcp sandbox] Rejecting message from", event.origin);
      return;
    }
    if (isSandboxMethod(event.data)) {
      if ((event.data as { method: string }).method === RESOURCE_READY) loadView(event.data);
      return;
    }
    if (loaded) inner.contentWindow?.postMessage(event.data, "*");
  } else if (event.source === inner.contentWindow && event.source !== null) {
    const expected = innerSameOrigin ? OWN_ORIGIN : "null";
    if (event.origin !== expected) {
      console.error("[shiny-mcp sandbox] Rejecting View message from", event.origin);
      return;
    }
    if (isSandboxMethod(event.data)) return;
    window.parent.postMessage(event.data, HOST_ORIGIN);
  }
});

window.parent.postMessage({ jsonrpc: "2.0", method: PROXY_READY, params: { nonce: NONCE } }, HOST_ORIGIN);
