import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, consoleSecret, forgetConsoleSecret, owner } from "./api";

// The console's per-launch secret (audit SEC-D9): the page carries it in a <meta> tag and every POST/DELETE echoes it in
// X-Jarvis-Console. The constant "1" the client used to send is never sent any more.
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function page(secretValue: string | null) {
  vi.stubGlobal("document", {
    querySelector: (sel: string) => (sel === 'meta[name="jarvis-console-secret"]' && secretValue !== null ? { getAttribute: () => secretValue } : null),
  });
}

describe("console secret", () => {
  beforeEach(() => forgetConsoleSecret());
  afterEach(() => vi.unstubAllGlobals());

  it("sends the page's secret on every write, never the old constant", async () => {
    page("S3CRET-LAUNCH-A");
    const f = vi.fn().mockResolvedValue(json(200, { ok: true }));
    vi.stubGlobal("fetch", f);
    await owner.post("kill");
    await api.del("/api/bench/challenge/x");
    await api.get("/api/health");
    for (const [, init] of f.mock.calls) expect((init as RequestInit).headers).toMatchObject({ "X-Jarvis-Console": "S3CRET-LAUNCH-A" });
    for (const [, init] of f.mock.calls) expect(JSON.stringify((init as RequestInit).headers)).not.toContain('"1"');
  });

  it("sends the secret with an audio upload too", async () => {
    page("S3CRET-LAUNCH-A");
    const f = vi.fn().mockResolvedValue(json(200, { text: "hi" }));
    vi.stubGlobal("fetch", f);
    await api.audio("/api/voice/transcribe?lang=en", new Blob(["x"]));
    expect(f.mock.calls[0][1].headers).toEqual({ "X-Jarvis-Console": "S3CRET-LAUNCH-A" });
  });

  it("reads a fresh secret from the page and sends the write once more when the console was restarted", async () => {
    page("OLD-SECRET");
    const f = vi.fn()
      .mockResolvedValueOnce(json(403, { error: "request refused", reload: true }))
      .mockResolvedValueOnce(new Response('<html><head><meta name="jarvis-console-secret" content="NEW-SECRET"></head></html>', { status: 200, headers: { "content-type": "text/html" } }))
      .mockResolvedValueOnce(json(200, { done: true }));
    vi.stubGlobal("fetch", f);
    await expect(owner.post("command", { text: "hello" })).resolves.toEqual({ done: true });
    expect(f.mock.calls.map((c) => c[0])).toEqual(["/api/owner/command", "/", "/api/owner/command"]);
    expect(f.mock.calls[2][1].headers["X-Jarvis-Console"]).toBe("NEW-SECRET");
    expect(consoleSecret()).toBe("NEW-SECRET");
  });

  it("retries a refused write only once and does not retry other refusals", async () => {
    page("OLD-SECRET");
    const f = vi.fn()
      .mockResolvedValueOnce(json(403, { error: "stale", reload: true }))
      .mockResolvedValueOnce(new Response('<meta name="jarvis-console-secret" content="STILL-WRONG">', { status: 200 }))
      .mockResolvedValueOnce(json(403, { error: "stale again", reload: true }));
    vi.stubGlobal("fetch", f);
    await expect(owner.post("kill")).rejects.toBeInstanceOf(ApiError);
    expect(f).toHaveBeenCalledTimes(3);
    const g = vi.fn().mockResolvedValue(json(403, { error: "owner token only" }));
    vi.stubGlobal("fetch", g);
    await expect(owner.post("kill")).rejects.toMatchObject({ status: 403, message: "owner token only" });
    expect(g).toHaveBeenCalledTimes(1);
  });

  it("sends an empty header (and gets refused) when the page has no secret, instead of guessing", async () => {
    page(null);
    expect(consoleSecret()).toBe("");
    const f = vi.fn().mockResolvedValue(json(200, {}));
    vi.stubGlobal("fetch", f);
    await api.post("/api/owner/pc", {});
    expect(f.mock.calls[0][1].headers["X-Jarvis-Console"]).toBe("");
  });
});
