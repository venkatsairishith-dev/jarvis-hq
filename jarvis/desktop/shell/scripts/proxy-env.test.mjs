import { describe, expect, it } from "vitest";

import { ensureLoopbackNoProxy } from "./proxy-env.mjs";

describe("ensureLoopbackNoProxy", () => {
  it("adds loopback hosts when no bypass rules exist", () => {
    const env = {};

    expect(ensureLoopbackNoProxy(env)).toBe("127.0.0.1,localhost,::1");
    expect(env.NO_PROXY).toBe("127.0.0.1,localhost,::1");
    expect(env.no_proxy).toBe("127.0.0.1,localhost,::1");
  });

  it("preserves and merges existing uppercase and lowercase rules", () => {
    const env = {
      NO_PROXY: "internal.example, localhost",
      no_proxy: ".corp.example,127.0.0.1",
    };

    expect(ensureLoopbackNoProxy(env)).toBe(
      "internal.example,localhost,.corp.example,127.0.0.1,::1",
    );
  });

  it("deduplicates case-insensitively and remains idempotent", () => {
    const env = {
      NO_PROXY: "LOCALHOST,Example.com",
      no_proxy: "localhost,example.com",
    };

    const first = ensureLoopbackNoProxy(env);
    const second = ensureLoopbackNoProxy(env);

    expect(first).toBe("LOCALHOST,Example.com,127.0.0.1,::1");
    expect(second).toBe(first);
  });
});
