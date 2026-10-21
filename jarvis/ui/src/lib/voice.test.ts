// VOI-D6: the 60 s auto-stop of push-to-talk used to transcribe in English and drop the text. These tests drive the real
// voice controller with a fake microphone, a fake audio engine, fake timers and a recording api.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import commandCenterSource from "../screens/CommandCenter.tsx?raw";

const audioCalls: string[] = [];
let audioImpl: (path: string) => Promise<unknown>;
vi.mock("./api", () => {
  class ApiError extends Error {}
  return { ApiError, api: { audio: (path: string) => { audioCalls.push(path); return audioImpl(path); }, speech: vi.fn() } };
});

type Port = { onmessage: ((ev: { data: Float32Array }) => void) | null };
let port: Port;

class FakeAudioContext {
  sampleRate = 16000;
  audioWorklet = { addModule: async () => undefined };
  createMediaStreamSource() { return { connect: () => undefined }; }
  async close() { /* nothing */ }
  constructor(_o?: unknown) { void _o; }
}
class FakeNode {
  port: Port = { onmessage: null };
  constructor() { port = this.port; }
  disconnect() { /* nothing */ }
}

const heard = (text: string) => async () => ({ text, speech: true, language: "x", model: "fake", ms: 5, audio_s: 1 });

async function freshVoice() {
  vi.resetModules();
  return (await import("./voice")).voice;
}

/** Talk: one second of "speech" arrives, then `seconds` pass on the fake clock. */
async function talk(v: Awaited<ReturnType<typeof freshVoice>>, opts: Parameters<typeof v.start>[0], seconds: number) {
  await v.start(opts);
  expect(v.snapshot().mode).toBe("recording");
  port.onmessage?.({ data: new Float32Array(16000).fill(0.2) });
  await vi.advanceTimersByTimeAsync(seconds * 1000);
}

beforeEach(() => {
  vi.useFakeTimers();
  audioCalls.length = 0;
  audioImpl = heard("namaste");
  vi.stubGlobal("window", globalThis);
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("AudioWorkletNode", FakeNode);
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop: () => undefined }] }) } });
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("push-to-talk 60 s auto-stop", () => {
  it("transcribes in the selected language and hands the text to the caller", async () => {
    const v = await freshVoice();
    const got: Array<string | null> = [];
    await talk(v, { lang: () => "hi", onAutoStop: (t) => got.push(t) }, 61);
    expect(audioCalls).toEqual(["/api/voice/transcribe?lang=hi"]);        // it was always ?lang=en
    expect(got).toEqual(["namaste"]);                                       // it was dropped
    expect(v.snapshot().mode).toBe("idle");
    expect(v.snapshot().lastTranscript?.text).toBe("namaste");
  });

  it("uses the language selected when the limit is reached, not when recording began", async () => {
    const v = await freshVoice();
    let lang = "en";
    const got: Array<string | null> = [];
    await v.start({ lang: () => lang, onAutoStop: (t) => got.push(t) });
    port.onmessage?.({ data: new Float32Array(16000).fill(0.2) });
    await vi.advanceTimersByTimeAsync(30_000);
    lang = "te";
    await vi.advanceTimersByTimeAsync(31_000);
    expect(audioCalls).toEqual(["/api/voice/transcribe?lang=te"]);
    expect(got).toEqual(["namaste"]);
  });

  it("still defaults to English when the caller gives no language", async () => {
    const v = await freshVoice();
    await talk(v, undefined, 61);
    expect(audioCalls).toEqual(["/api/voice/transcribe?lang=en"]);
    expect(v.snapshot().lastTranscript?.text).toBe("namaste");
  });

  it("tells the caller when nothing was heard or recognition failed, and leaves the controller usable", async () => {
    const v = await freshVoice();
    const got: Array<string | null> = [];
    audioImpl = heard("");
    await talk(v, { lang: () => "hi", onAutoStop: (t) => got.push(t) }, 61);
    audioImpl = async () => { throw new Error("down"); };
    await talk(v, { lang: () => "hi", onAutoStop: (t) => got.push(t) }, 61);
    expect(got).toEqual(["", null]);
    expect(v.snapshot().mode).toBe("idle");
    expect(v.snapshot().error).toMatch(/Speech recognition failed/);
  });

  it("a handler that throws does not leave the controller stuck", async () => {
    const v = await freshVoice();
    await talk(v, { onAutoStop: () => { throw new Error("handler bug"); } }, 61);
    expect(v.snapshot().mode).toBe("idle");
  });

  it("a recording stopped by hand before the limit never calls the auto-stop handler", async () => {
    const v = await freshVoice();
    const got: Array<string | null> = [];
    await talk(v, { lang: () => "hi", onAutoStop: (t) => got.push(t) }, 5);
    const text = await v.finish("hi");
    await vi.advanceTimersByTimeAsync(120_000);
    expect(text).toBe("namaste");
    expect(got).toEqual([]);
    expect(audioCalls).toEqual(["/api/voice/transcribe?lang=hi"]);        // transcribed once, not again at 60 s
  });
});

describe("CommandCenter wiring", () => {
  it("gives the auto-stop the same handler as the manual stop", () => {
    expect(commandCenterSource).toMatch(/voice\.start\(\{[^}]*lang: \(\) => talkRef\.current\.lang[^}]*onAutoStop:/s);
    expect(commandCenterSource).toMatch(/acceptTranscript\(await voice\.finish\(prefs\.lang\)\)/);
    expect(commandCenterSource).not.toMatch(/finish\("en"\)/);
  });
});
