import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RpcRequestError, ShinyRpc, type ServerMessage } from "../src/shiny-transport";

type Call = { method: string; args: unknown[]; ok: (v: unknown) => void; err: (e: unknown) => void };
let calls: Call[];
let connected: boolean;

beforeEach(() => {
  calls = [];
  connected = true;
  (window as any).Shiny = {
    shinyapp: {
      isConnected: () => connected,
      makeRequest: (method: string, args: unknown[], ok: (v: unknown) => void, err: (e: unknown) => void) =>
        calls.push({ method, args, ok, err }),
    },
    addCustomMessageHandler: () => {},
  };
});
afterEach(() => {
  delete (window as any).Shiny;
  vi.useRealTimers();
});

const response = (rpc: ShinyRpc, requestId: string, payload: Record<string, unknown>, mountId = rpc.mountId): ServerMessage => ({
  v: 1, host_id: rpc.hostId, widget_id: rpc.widgetId, kind: "response", mount_id: mountId, request_id: requestId, payload,
});

describe("ShinyRpc", () => {
  it("sends namespaced, versioned envelopes with unique request ids", () => {
    const rpc = new ShinyRpc("mcp", "w1", "m1");
    rpc.request("tools/call", { params: { name: "x" } }, 3);
    rpc.request("tools/call", { params: { name: "x" } }, 3);
    expect(calls.map((c) => c.method)).toEqual(["shiny-mcp-host:mcp", "shiny-mcp-host:mcp"]);
    const [a, b] = calls.map((c) => c.args[0] as any);
    expect(a).toMatchObject({ v: 1, kind: "tools/call", widget_id: "w1", mount_id: "m1", render_id: 3 });
    expect(a.request_id).not.toBe(b.request_id);
  });

  it("resolves immediate acks", async () => {
    const rpc = new ShinyRpc("mcp", "w1", "m1");
    const p = rpc.request("model-context", {});
    calls[0].ok({ status: "done", ok: true, result: { a: 1 } });
    await expect(p).resolves.toEqual({ a: 1 });
  });

  it("correlates deferred responses by request id, out of order", async () => {
    const rpc = new ShinyRpc("mcp", "w1", "m1");
    const p1 = rpc.request("tools/call", {});
    const p2 = rpc.request("tools/call", {});
    calls.forEach((c) => c.ok({ status: "pending" }));
    const [id1, id2] = calls.map((c) => (c.args[0] as any).request_id);
    expect(rpc.handleResponse(response(rpc, id2, { ok: true, result: "two" }))).toBe(true);
    expect(rpc.handleResponse(response(rpc, id1, { ok: true, result: "one" }))).toBe(true);
    await expect(p1).resolves.toBe("one");
    await expect(p2).resolves.toBe("two");
    // Duplicate / replayed responses are ignored.
    expect(rpc.handleResponse(response(rpc, id1, { ok: true, result: "again" }))).toBe(false);
  });

  it("ignores responses for another mount", async () => {
    const rpc = new ShinyRpc("mcp", "w1", "m1");
    rpc.request("tools/call", {});
    const id = (calls[0].args[0] as any).request_id;
    expect(rpc.handleResponse(response(rpc, id, { ok: true }, "m2"))).toBe(false);
    expect(rpc.pendingCount).toBe(1);
  });

  it("rejects errors with code", async () => {
    const rpc = new ShinyRpc("mcp", "w1", "m1");
    const p = rpc.request("tools/call", {});
    calls[0].ok({ status: "done", ok: false, error: { code: "policy_rejected", message: "nope" } });
    await expect(p).rejects.toMatchObject({ code: "policy_rejected", message: "nope" });
  });

  it("times out and bounds pending requests", async () => {
    vi.useFakeTimers();
    const rpc = new ShinyRpc("mcp", "w1", "m1", { timeoutMs: 100, maxPending: 2 });
    const p = rpc.request("tools/call", {});
    rpc.request("tools/call", {}).catch(() => {});
    await expect(rpc.request("tools/call", {})).rejects.toBeInstanceOf(RpcRequestError);
    vi.advanceTimersByTime(150);
    await expect(p).rejects.toMatchObject({ code: "timeout" });
    expect(rpc.pendingCount).toBe(0);
  });

  it("dispose settles everything and refuses new requests", async () => {
    const rpc = new ShinyRpc("mcp", "w1", "m1");
    const p = rpc.request("tools/call", {});
    rpc.dispose();
    await expect(p).rejects.toMatchObject({ code: "disposed" });
    await expect(rpc.request("tools/call", {})).rejects.toMatchObject({ code: "disposed" });
  });

  it("refuses when Shiny is disconnected", async () => {
    connected = false;
    await expect(new ShinyRpc("mcp", "w1", "m1").request("mount")).rejects.toMatchObject({ code: "disconnected" });
  });
});
