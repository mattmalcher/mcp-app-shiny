/**
 * shiny-mcp-host browser adapter.
 *
 * Defines the `<shiny-mcp-widget>` element. Each connected element is one
 * "mount": it announces itself to Python, receives render instructions, and
 * runs one official `AppBridge` per View inside a distinct-origin sandbox.
 * Python owns the MCP connection; every View request is relayed to Python
 * through a correlated Shiny RPC and authorised there.
 */
import {
  AppBridge,
  type McpUiHostCapabilities,
  type McpUiHostContext,
  type McpUiResourcePermissions,
} from "@modelcontextprotocol/ext-apps/app-bridge";
import type { CallToolResult } from "@modelcontextprotocol/client";
import { SandboxTransport, loadSandboxProxy } from "./sandbox";
import { CUSTOM_MESSAGE_TYPE, RpcRequestError, ShinyRpc, type ServerMessage, shiny, shinyConnected } from "./shiny-transport";
import { checkSandboxUrl, clampHeight, innerSandbox, randomToken, safeLinkUrl } from "./validation";

const HOST_INFO = { name: "shiny-mcp-host", version: "0.1.0" };
const PROXY_TIMEOUT_MS = 15_000;
const INIT_TIMEOUT_MS = 20_000;
const TEARDOWN_TIMEOUT_MS = 1_000;

interface RenderMessage {
  render_id: number;
  server: string;
  tool: string;
  resource: {
    uri: string;
    html: string;
    csp: Record<string, string[]>;
    permissions: McpUiResourcePermissions;
    prefers_border: boolean;
  };
  tool_input: { arguments: Record<string, unknown> };
  tool_result: CallToolResult | null;
  tool_cancelled: { reason: string } | null;
  sandbox: { url: string; view_same_origin: boolean };
  host_context: McpUiHostContext;
  capabilities: { open_links: boolean; resource_read: boolean };
}

const log = {
  info: (...a: unknown[]) => console.info("[shiny-mcp-host]", ...a),
  warn: (...a: unknown[]) => console.warn("[shiny-mcp-host]", ...a),
  error: (...a: unknown[]) => console.error("[shiny-mcp-host]", ...a),
};

function withTimeout<T>(p: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(message)), ms);
    p.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      (e) => {
        clearTimeout(t);
        reject(e);
      },
    );
  });
}

function errorMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** One running View (one render in one mount). */
class ViewSession {
  private bridge?: AppBridge;
  private iframe?: HTMLIFrameElement;
  private initialized = false;
  private closed = false;
  private readonly abort = new AbortController();

  constructor(
    private readonly el: McpWidgetElement,
    private readonly rpc: ShinyRpc,
    readonly msg: RenderMessage,
  ) {}

  private request(kind: Parameters<ShinyRpc["request"]>[0], payload: Record<string, unknown> = {}) {
    return this.rpc.request(kind, payload, this.msg.render_id);
  }

  async start(): Promise<void> {
    const { msg } = this;
    const check = checkSandboxUrl(msg.sandbox.url, window.location.origin, msg.sandbox.view_same_origin);
    if (!check.ok) throw new Error(`Unsafe sandbox configuration: ${check.reason}`);
    if (check.warning) log.warn(check.warning);

    const { iframe, origin } = await loadSandboxProxy(this.el.frameHost, {
      sandboxUrl: msg.sandbox.url,
      sandboxOrigin: check.origin,
      csp: msg.resource.csp,
      permissions: msg.resource.permissions,
      title: `MCP App: ${msg.tool}`,
      timeoutMs: PROXY_TIMEOUT_MS,
      signal: this.abort.signal,
    });
    if (this.closed) return;
    this.iframe = iframe;
    this.el.classList.toggle("shiny-mcp-widget-bordered", !!msg.resource.prefers_border);

    const bridge = this.createBridge();
    this.bridge = bridge;
    const initialized = new Promise<void>((resolve) => bridge.addEventListener("initialized", () => resolve()));
    await bridge.connect(new SandboxTransport(iframe.contentWindow!, origin));
    await bridge.sendSandboxResourceReady({
      html: msg.resource.html,
      csp: msg.resource.csp,
      permissions: msg.resource.permissions,
      sandbox: innerSandbox(msg.sandbox.view_same_origin),
    });
    await withTimeout(initialized, INIT_TIMEOUT_MS, "The MCP App did not complete initialization");
    if (this.closed) return;
    this.initialized = true;
    // Per spec the host sends nothing to the View before `initialized`; then
    // tool input exactly once, then the result (or cancellation) exactly once.
    await bridge.sendToolInput(msg.tool_input);
    if (msg.tool_result) {
      await bridge.sendToolResult(msg.tool_result);
    } else {
      await bridge.sendToolCancelled({ reason: msg.tool_cancelled?.reason ?? "Tool call failed" });
    }
    this.el.setStatus(null);
    this.el.dataset.state = "ready";
    this.request("initialized").catch(() => {});
  }

  private createBridge(): AppBridge {
    const { msg } = this;
    const capabilities: McpUiHostCapabilities = {
      serverTools: {},
      logging: {},
      updateModelContext: { text: {}, structuredContent: {} },
      message: { text: {} },
    };
    if (msg.capabilities.open_links) capabilities.openLinks = {};
    if (msg.capabilities.resource_read) capabilities.serverResources = {};
    capabilities.sandbox = { csp: msg.resource.csp, permissions: msg.resource.permissions };

    const bridge = new AppBridge(null, HOST_INFO, capabilities, {
      hostContext: {
        ...msg.host_context,
        containerDimensions: { maxHeight: 4000, width: this.el.clientWidth || undefined } as never,
        locale: navigator.language,
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        userAgent: "shiny-mcp-host",
      },
    });

    // Register every handler before connect(): the View may send requests
    // right after the initialization handshake.
    bridge.oncalltool = async (params) => {
      try {
        return (await this.request("tools/call", { params })) as CallToolResult;
      } catch (e) {
        throw new Error(errorMessage(e));
      }
    };
    if (msg.capabilities.resource_read) {
      bridge.onreadresource = async (params) => {
        try {
          return (await this.request("resources/read", { params })) as never;
        } catch (e) {
          throw new Error(errorMessage(e));
        }
      };
    }
    bridge.onupdatemodelcontext = async (params) => {
      await this.request("model-context", { params });
      return {};
    };
    bridge.onmessage = async (params) => {
      try {
        return (await this.request("message", { params })) as { isError?: boolean };
      } catch (e) {
        log.warn("ui/message rejected:", errorMessage(e));
        return { isError: true };
      }
    };
    bridge.onopenlink = async (params) => {
      const url = safeLinkUrl(params.url);
      if (!url || !msg.capabilities.open_links) {
        log.warn("Refusing to open link", params.url);
        return { isError: true };
      }
      try {
        const res = (await this.request("open-link", { params: { url } })) as { url?: string };
        const approved = safeLinkUrl(res?.url);
        if (!approved) return { isError: true };
        window.open(approved, "_blank", "noopener,noreferrer");
        return {};
      } catch (e) {
        log.warn("Open link rejected:", errorMessage(e));
        return { isError: true };
      }
    };
    bridge.onrequestdisplaymode = async () => ({ mode: "inline" });
    bridge.addEventListener("loggingmessage", (params) => {
      this.request("log", { params }).catch(() => {});
    });
    bridge.addEventListener("sizechange", ({ height }) => {
      const h = clampHeight(height);
      if (h !== null && this.iframe) this.iframe.style.height = `${h}px`;
    });
    bridge.addEventListener("requestteardown", () => {
      log.info("View requested teardown (ignored: lifecycle is host-controlled)");
    });
    bridge.onerror = (e) => log.warn("AppBridge error:", e);
    return bridge;
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.abort.abort();
    const bridge = this.bridge;
    if (bridge) {
      if (this.initialized) {
        try {
          await bridge.teardownResource({}, { timeout: TEARDOWN_TIMEOUT_MS });
        } catch {
          /* View did not answer in time; unmount anyway. */
        }
      }
      try {
        await bridge.close();
      } catch {
        /* ignore */
      }
    }
    this.iframe?.remove();
  }
}

const registry = new Map<string, McpWidgetElement>();
const key = (hostId: string, widgetId: string) => `${hostId}\u0000${widgetId}`;

export class McpWidgetElement extends HTMLElement {
  frameHost!: HTMLDivElement;
  private statusEl!: HTMLDivElement;
  private rpc?: ShinyRpc;
  private view?: ViewSession;
  private viewChain: Promise<void> = Promise.resolve();

  get widgetId(): string {
    return this.dataset.widgetId ?? "";
  }

  get hostId(): string {
    return this.dataset.hostId ?? "";
  }

  connectedCallback(): void {
    if (this.rpc) return;
    if (!this.widgetId || !this.hostId) {
      log.error("shiny-mcp-widget requires data-widget-id and data-host-id");
      return;
    }
    if (!this.statusEl) {
      // Children may not be parsed yet when this runs, so never replace them:
      // the server-rendered placeholder is hidden by CSS once data-state is set.
      this.statusEl = document.createElement("div");
      this.statusEl.className = "shiny-mcp-widget-status";
      this.frameHost = document.createElement("div");
      this.frameHost.className = "shiny-mcp-widget-frame-host";
      this.append(this.statusEl, this.frameHost);
    }
    this.setStatus("Waiting for an MCP App…");
    const mountId = randomToken(8);
    const rpc = new ShinyRpc(this.hostId, this.widgetId, mountId);
    this.rpc = rpc;
    this.dataset.mountId = mountId;
    this.dataset.state = "mounting";
    const k = key(this.hostId, this.widgetId);
    const previous = registry.get(k);
    if (previous && previous !== this) log.warn(`Duplicate widget id ${this.widgetId}; the newest element wins`);
    registry.set(k, this);
    shinyConnected().then(() => {
      if (this.rpc !== rpc) return;
      rpc.request("mount").catch((e) => this.showError("mount_failed", errorMessage(e)));
    });
  }

  disconnectedCallback(): void {
    const rpc = this.rpc;
    if (!rpc) return;
    this.rpc = undefined;
    const k = key(this.hostId, this.widgetId);
    if (registry.get(k) === this) registry.delete(k);
    this.replaceView(null);
    rpc.request("unmount").catch(() => {});
    rpc.dispose();
    this.dataset.state = "unmounted";
  }

  setStatus(text: string | null, kind: "info" | "error" = "info"): void {
    if (!this.statusEl) return;
    this.statusEl.hidden = text === null;
    this.statusEl.textContent = text ?? "";
    this.statusEl.setAttribute("role", kind === "error" ? "alert" : "status");
    this.statusEl.classList.toggle("shiny-mcp-widget-error", kind === "error");
  }

  showError(code: string, message: string, fallbackText?: string): void {
    this.dataset.state = "error";
    this.dataset.errorCode = code;
    this.setStatus(`MCP App error: ${message}`, "error");
    if (fallbackText) {
      const pre = document.createElement("pre");
      pre.className = "shiny-mcp-widget-fallback";
      pre.textContent = fallbackText;
      this.frameHost.replaceChildren(pre);
    }
  }

  /** Serialise view replacement so teardown always completes before the next start. */
  private replaceView(msg: RenderMessage | null): void {
    const rpc = this.rpc;
    this.viewChain = this.viewChain.then(async () => {
      const old = this.view;
      this.view = undefined;
      if (old) await old.close();
      if (!msg || !rpc || this.rpc !== rpc) {
        if (!msg) this.frameHost?.replaceChildren();
        return;
      }
      const view = new ViewSession(this, rpc, msg);
      this.view = view;
      this.dataset.state = "loading";
      this.dataset.renderId = String(msg.render_id);
      delete this.dataset.errorCode;
      this.setStatus(`Loading ${msg.tool}…`);
      try {
        await view.start();
      } catch (e) {
        if (this.view !== view) return;
        log.error("Failed to start View:", e);
        await view.close();
        this.view = undefined;
        this.showError("view_failed", errorMessage(e));
        rpc.request("view-error", { message: errorMessage(e) }).catch(() => {});
      }
    });
  }

  handleServerMessage(msg: ServerMessage): void {
    const rpc = this.rpc;
    if (!rpc) return;
    switch (msg.kind) {
      case "response":
        rpc.handleResponse(msg);
        return;
      case "render":
        if (msg.mount_id !== rpc.mountId) return;
        this.replaceView(msg.payload as unknown as RenderMessage);
        return;
      case "loading":
        this.replaceView(null);
        this.dataset.state = "loading";
        this.setStatus(String(msg.payload.label ?? "Loading…"));
        return;
      case "error":
        this.replaceView(null);
        this.viewChain = this.viewChain.then(() =>
          this.showError(
            String(msg.payload.code ?? "error"),
            String(msg.payload.message ?? "Unknown error"),
            typeof msg.payload.fallback_text === "string" ? msg.payload.fallback_text : undefined,
          ),
        );
        return;
      case "fallback":
        this.replaceView(null);
        this.viewChain = this.viewChain.then(() => {
          const pre = document.createElement("pre");
          pre.className = "shiny-mcp-widget-fallback";
          pre.textContent = String(msg.payload.text ?? "");
          this.frameHost.replaceChildren(pre);
          this.setStatus(msg.payload.is_error ? "Tool returned an error" : null, msg.payload.is_error ? "error" : "info");
          this.dataset.state = "fallback";
        });
        return;
      case "teardown":
        this.replaceView(null);
        this.dataset.state = "removed";
        this.setStatus("Removed");
        return;
      default:
        log.warn("Unknown message kind", msg.kind);
    }
  }

  /** Tear down after the Shiny session disconnects. */
  disconnectSession(): void {
    this.replaceView(null);
    this.rpc?.dispose("Shiny session disconnected");
    this.dataset.state = "disconnected";
    this.setStatus("Disconnected from the server", "error");
  }
}

function isServerMessage(m: unknown): m is ServerMessage {
  const x = m as Partial<ServerMessage> | null;
  return (
    !!x &&
    x.v === 1 &&
    typeof x.host_id === "string" &&
    typeof x.widget_id === "string" &&
    typeof x.kind === "string" &&
    typeof x.payload === "object"
  );
}

function install(): void {
  if (!customElements.get("shiny-mcp-widget")) {
    customElements.define("shiny-mcp-widget", McpWidgetElement);
  }
  const register = () => {
    const S = shiny();
    if (!S) return false;
    S.addCustomMessageHandler(CUSTOM_MESSAGE_TYPE, (m: unknown) => {
      if (!isServerMessage(m)) {
        log.warn("Ignoring malformed message", m);
        return;
      }
      registry.get(key(m.host_id, m.widget_id))?.handleServerMessage(m);
    });
    const $ = (window as unknown as { jQuery?: (d: Document) => { on(e: string, f: () => void): void } }).jQuery;
    $?.(document).on("shiny:disconnected", () => {
      for (const el of registry.values()) el.disconnectSession();
    });
    return true;
  };
  if (!register()) {
    // Script loaded before shiny.js; register as soon as Shiny exists.
    const timer = setInterval(() => {
      if (register()) clearInterval(timer);
    }, 10);
  }
}

install();
