/**
 * Host-side sandbox handling: create the outer proxy iframe, wait for its
 * nonce-bound ready signal, and provide a postMessage transport for AppBridge
 * that checks both the source window and the origin of every message.
 */
import { JSONRPCMessageSchema } from "@modelcontextprotocol/core";
import type { JSONRPCMessage, Transport } from "@modelcontextprotocol/client";
import { buildAllowAttribute, type McpUiResourcePermissions } from "@modelcontextprotocol/ext-apps/app-bridge";
import {
  OUTER_SANDBOX,
  SANDBOX_METHOD_PREFIX,
  TRUSTED_VIEW_SANDBOX,
  isExpectedProxyReady,
  randomToken,
  sandboxPageUrl,
} from "./validation";

/** A failure with a machine-readable code, shown as the widget's `data-error-code`. */
export class HostError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

/** The origin of messages from an opaque-origin (sandboxed, no allow-same-origin) frame. */
export const OPAQUE_ORIGIN = "null";

/**
 * postMessage transport bound to one sandbox window and origin.
 *
 * With `OPAQUE_ORIGIN` (trusted mode: the View itself, no proxy) messages are
 * posted with target "*", since an opaque origin cannot be named, and the
 * View may not send `ui/notifications/sandbox-*` (the proxy's job otherwise).
 */
export class SandboxTransport implements Transport {
  onclose?: () => void;
  onerror?: (error: Error) => void;
  onmessage?: (message: JSONRPCMessage) => void;
  private readonly listener: (event: MessageEvent) => void;
  private closed = false;

  constructor(
    private readonly target: Window,
    private readonly origin: string,
  ) {
    this.listener = (event: MessageEvent) => {
      if (event.source !== this.target || event.origin !== this.origin) return;
      if (this.origin === OPAQUE_ORIGIN && isSandboxMethod(event.data)) return;
      const parsed = JSONRPCMessageSchema.safeParse(event.data);
      if (parsed.success) {
        this.onmessage?.(parsed.data as JSONRPCMessage);
      } else if ((event.data as { jsonrpc?: unknown } | null)?.jsonrpc === "2.0") {
        this.onerror?.(new Error("Invalid JSON-RPC message from View"));
      }
    };
  }

  async start(): Promise<void> {
    window.addEventListener("message", this.listener);
  }

  async send(message: JSONRPCMessage): Promise<void> {
    if (this.closed) return;
    this.target.postMessage(message, this.origin === OPAQUE_ORIGIN ? "*" : this.origin);
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    window.removeEventListener("message", this.listener);
    this.onclose?.();
  }
}

function isSandboxMethod(data: unknown): boolean {
  const method = (data as { method?: unknown } | null)?.method;
  return typeof method === "string" && method.startsWith(SANDBOX_METHOD_PREFIX);
}

/**
 * Trusted mode: create the single View iframe inside `container` (opaque
 * origin, no proxy, no sandbox server). The caller connects its transport to
 * `iframe.contentWindow` and only then sets `srcdoc`, so the View's first
 * message cannot be missed.
 */
export function createTrustedViewFrame(
  container: HTMLElement,
  opts: { permissions?: McpUiResourcePermissions; title: string },
): HTMLIFrameElement {
  const iframe = document.createElement("iframe");
  iframe.setAttribute("sandbox", TRUSTED_VIEW_SANDBOX);
  iframe.setAttribute("title", opts.title);
  iframe.className = "shiny-mcp-widget-frame";
  const allow = buildAllowAttribute(opts.permissions);
  if (allow) iframe.setAttribute("allow", allow);
  container.replaceChildren(iframe);
  return iframe;
}

export interface ProxyFrame {
  iframe: HTMLIFrameElement;
  origin: string;
}

/**
 * Create the outer sandbox iframe inside `container` and resolve once the
 * proxy has signalled readiness with the expected nonce.
 */
export function loadSandboxProxy(
  container: HTMLElement,
  opts: {
    sandboxUrl: string;
    sandboxOrigin: string;
    csp: unknown;
    permissions?: McpUiResourcePermissions;
    title: string;
    timeoutMs: number;
    signal: AbortSignal;
  },
): Promise<ProxyFrame> {
  const nonce = randomToken(16);
  const iframe = document.createElement("iframe");
  iframe.setAttribute("sandbox", OUTER_SANDBOX);
  iframe.setAttribute("title", opts.title);
  iframe.className = "shiny-mcp-widget-frame";
  const allow = buildAllowAttribute(opts.permissions);
  if (allow) iframe.setAttribute("allow", allow);

  return new Promise<ProxyFrame>((resolve, reject) => {
    const cleanup = () => {
      window.removeEventListener("message", onMessage);
      clearTimeout(timer);
      opts.signal.removeEventListener("abort", onAbort);
    };
    const onMessage = (event: MessageEvent) => {
      if (isExpectedProxyReady(event, iframe.contentWindow, opts.sandboxOrigin, nonce)) {
        cleanup();
        resolve({ iframe, origin: opts.sandboxOrigin });
      }
    };
    const onAbort = () => {
      cleanup();
      reject(new Error("aborted"));
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(
        new HostError(
          "sandbox_unreachable",
          `Sandbox proxy at ${opts.sandboxOrigin} did not respond: the browser could not load or embed it. ` +
            "Check that it is reachable from this browser without a redirect or login page, and look in the " +
            "browser console for a blocked frame (X-Frame-Options / frame-ancestors) or TLS error. " +
            "Server-side check: shiny-mcp-sandbox --check <sandbox URL> --host-origin <app origin>.",
        ),
      );
    }, opts.timeoutMs);
    window.addEventListener("message", onMessage);
    opts.signal.addEventListener("abort", onAbort);
    iframe.src = sandboxPageUrl(opts.sandboxUrl, window.location.origin, nonce, opts.csp);
    container.replaceChildren(iframe);
  });
}
