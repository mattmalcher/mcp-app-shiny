import { describe, expect, it } from "vitest";
import { SandboxTransport } from "../src/sandbox";

function dispatch(data: unknown, origin: string, source: unknown) {
  window.dispatchEvent(new MessageEvent("message", { data, origin, source: source as MessageEventSource }));
}

describe("SandboxTransport", () => {
  it("accepts only messages from the bound window and origin", async () => {
    const target = { postMessage: () => {} } as unknown as Window;
    const t = new SandboxTransport(target, "http://localhost:8765");
    const got: unknown[] = [];
    const errors: Error[] = [];
    t.onmessage = (m) => got.push(m);
    t.onerror = (e) => errors.push(e);
    await t.start();
    const msg = { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "x" } };
    dispatch(msg, "http://localhost:8765", target);
    dispatch(msg, "http://evil.example", target); // wrong origin
    dispatch(msg, "http://localhost:8765", window); // wrong source window
    dispatch({ hello: "not jsonrpc" }, "http://localhost:8765", target); // ignored silently
    dispatch({ jsonrpc: "2.0", bogus: true }, "http://localhost:8765", target); // malformed
    expect(got).toEqual([msg]);
    expect(errors).toHaveLength(1);
    await t.close();
    dispatch(msg, "http://localhost:8765", target);
    expect(got).toHaveLength(1);
  });

  it("posts with an explicit target origin, never '*'", async () => {
    const posted: [unknown, string][] = [];
    const target = { postMessage: (m: unknown, o: string) => posted.push([m, o]) } as unknown as Window;
    const t = new SandboxTransport(target, "http://localhost:8765");
    await t.start();
    await t.send({ jsonrpc: "2.0", method: "ui/notifications/tool-input", params: {} } as never);
    expect(posted[0][1]).toBe("http://localhost:8765");
    await t.close();
    await t.send({ jsonrpc: "2.0", method: "x" } as never);
    expect(posted).toHaveLength(1);
  });
});
