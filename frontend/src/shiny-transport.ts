/**
 * Correlated request/response channel between one widget mount and Python.
 *
 * Browser -> Python: `Shiny.shinyapp.makeRequest("shiny-mcp-host:<host>", [envelope])`.
 * Python acknowledges immediately with `{status: "done", ...}` or
 * `{status: "pending"}`; pending results arrive later as a `response`
 * custom message carrying the same `request_id` and `mount_id`.
 */

export const CUSTOM_MESSAGE_TYPE = "shiny-mcp-host";
export const ENVELOPE_VERSION = 1;

export type Kind =
  | "mount"
  | "unmount"
  | "initialized"
  | "tools/call"
  | "resources/read"
  | "model-context"
  | "message"
  | "open-link"
  | "log"
  | "view-error";

export interface RpcError {
  code: string;
  message: string;
}

export interface Ack {
  status: "done" | "pending";
  ok?: boolean;
  result?: unknown;
  error?: RpcError;
}

export interface ServerMessage {
  v: number;
  host_id: string;
  widget_id: string;
  kind: string;
  mount_id?: string;
  request_id?: string;
  payload: Record<string, unknown>;
}

interface ShinyApp {
  makeRequest(
    method: string,
    args: unknown[],
    onSuccess: (value: unknown) => void,
    onError: (err: unknown) => void,
    blobs: unknown,
  ): void;
  isConnected(): boolean;
}

interface ShinyGlobal {
  shinyapp?: ShinyApp;
  addCustomMessageHandler(type: string, handler: (message: unknown) => void): void;
}

export function shiny(): ShinyGlobal | undefined {
  return (window as unknown as { Shiny?: ShinyGlobal }).Shiny;
}

/** Resolves once Shiny's websocket is connected. */
export function shinyConnected(): Promise<void> {
  return new Promise((resolve) => {
    const check = () => {
      if (shiny()?.shinyapp?.isConnected()) {
        resolve();
        return true;
      }
      return false;
    };
    if (check()) return;
    const timer = setInterval(() => {
      if (check()) clearInterval(timer);
    }, 25);
  });
}

export class RpcRequestError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "RpcRequestError";
  }
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}

export class ShinyRpc {
  private seq = 0;
  private readonly pending = new Map<string, Pending>();
  private disposed = false;

  constructor(
    readonly hostId: string,
    readonly widgetId: string,
    readonly mountId: string,
    private readonly opts: { timeoutMs: number; maxPending: number } = { timeoutMs: 60_000, maxPending: 64 },
  ) {}

  get handlerName(): string {
    return `${CUSTOM_MESSAGE_TYPE}:${this.hostId}`;
  }

  get pendingCount(): number {
    return this.pending.size;
  }

  /** Send an envelope; resolves with the (possibly deferred) result. */
  request(kind: Kind, payload: Record<string, unknown> = {}, renderId?: number): Promise<unknown> {
    if (this.disposed) return Promise.reject(new RpcRequestError("disposed", "Widget is unmounted"));
    const app = shiny()?.shinyapp;
    if (!app || !app.isConnected()) {
      return Promise.reject(new RpcRequestError("disconnected", "Shiny is not connected"));
    }
    if (this.pending.size >= this.opts.maxPending) {
      return Promise.reject(new RpcRequestError("too_many_requests", "Too many pending requests"));
    }
    const requestId = `${this.mountId}.${++this.seq}`;
    const envelope = {
      v: ENVELOPE_VERSION,
      kind,
      widget_id: this.widgetId,
      mount_id: this.mountId,
      request_id: requestId,
      render_id: renderId ?? null,
      payload,
    };
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.settle(requestId, { ok: false, error: { code: "timeout", message: `${kind} timed out` } });
      }, this.opts.timeoutMs);
      this.pending.set(requestId, { resolve, reject, timer });
      app.makeRequest(
        this.handlerName,
        [envelope],
        (value) => {
          const ack = value as Ack | null;
          if (!ack || typeof ack !== "object") {
            this.settle(requestId, { ok: false, error: { code: "protocol", message: "Malformed ack" } });
          } else if (ack.status !== "pending") {
            this.settle(requestId, ack);
          }
        },
        (err) => {
          this.settle(requestId, { ok: false, error: { code: "server_error", message: String(err) } });
        },
        undefined,
      );
    });
  }

  /** Handle a deferred `response` message from Python. Returns false if ignored. */
  handleResponse(message: ServerMessage): boolean {
    if (message.mount_id !== this.mountId || typeof message.request_id !== "string") return false;
    return this.settle(message.request_id, message.payload as Partial<Ack>);
  }

  private settle(requestId: string, outcome: Partial<Ack>): boolean {
    const entry = this.pending.get(requestId);
    if (!entry) return false;
    this.pending.delete(requestId);
    clearTimeout(entry.timer);
    if (outcome.ok) {
      entry.resolve(outcome.result);
    } else {
      const err = outcome.error ?? { code: "error", message: "Request failed" };
      entry.reject(new RpcRequestError(err.code, err.message));
    }
    return true;
  }

  /** Reject every pending request and refuse new ones. */
  dispose(reason = "Widget is unmounted"): void {
    this.disposed = true;
    for (const [id, entry] of this.pending) {
      clearTimeout(entry.timer);
      entry.reject(new RpcRequestError("disposed", reason));
      this.pending.delete(id);
    }
  }
}
