// VOI-D6 for the Settings voice check: its push-to-talk used to call voice.start() with no options, so the 60 s auto-stop
// transcribed in English and dropped the text. The behaviour of the controller itself is covered in lib/voice.test.ts; these
// tests pin the screen wiring (no DOM renderer is installed, so they read the source, as the CommandCenter wiring test does).
import { describe, expect, it } from "vitest";
import settingsSource from "./Settings.tsx?raw";

const screens = import.meta.glob("./*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

describe("Settings voice check wiring", () => {
  it("starts the recording with the language and the auto-stop handler", () => {
    expect(settingsSource).toMatch(/voice\.start\(\{[^}]*lang: \(\) => talkRef\.current\.lang[^}]*onAutoStop:/s);
  });

  it("scores an auto-stopped recording the way a manual stop is scored", () => {
    expect(settingsSource).toMatch(/onAutoStop: \(t\) => \{ if \(mounted\.current\) void talkRef\.current\.score\(t\); \}/);
    expect(settingsSource).toMatch(/const stop = async \(\) => scoreHeard\(await voice\.finish\(lang\)\)/);
    expect(settingsSource).toMatch(/talkRef\.current = \{ lang, score: scoreHeard \}/);   // the latest render's language and phrase
  });

  it("never forces English and does nothing after the screen is left", () => {
    expect(settingsSource).not.toMatch(/finish\("en"\)/);
    expect(settingsSource).toMatch(/return \(\) => \{ mounted\.current = false; \}/);
  });

  it("no screen starts push-to-talk without options", () => {
    const bare = Object.entries(screens).filter(([, src]) => /voice\.start\(\s*\)/.test(src)).map(([name]) => name);
    expect(bare).toEqual([]);
  });
});
