// Review M6 (2026-10-18): the owner approves what he SEES. A value past the old 2000-character cut, a canary at the very end of
// a long payload, and a card the kernel marked as cut off. No DOM renderer is installed, so the card's wiring is pinned by
// reading the screen's source (as lib/voice.test.ts does for the voice handlers).
import { describe, expect, it } from "vitest";
import commandCenterSource from "../screens/CommandCenter.tsx?raw";
import { canApprove, cardBlocked, cardChars, cardFields, cardIncomplete, cardInvisible, cardNeedsAck, CUT_MARK, KEY_MARK } from "./approval";
import type { Approval } from "./types";

const END = "ZZ-END-CANARY-8d41-SYNTH";
const padded = (n: number) => ("harmless words ".repeat(Math.ceil(n / 15))).slice(0, n - END.length) + END;
const card = (over: Partial<Approval> = {}, params: Record<string, unknown> = {}): Approval => ({
  id: "abc123", level: "YELLOW_APPROVAL", seconds_left: 300, reason: "rule", digest: "0123456789ab", truncated: false, requester: "hermes",
  action: { tool: "lab", op: "task", target: "sub-1", lane: "private", params }, ...over,
});

describe("approval card completeness", () => {
  it.each([1990, 2000, 2001, 2500, 8000, 20000])("shows a %i character value to its last character", (n) => {
    const a = card({ chars: n }, { prompt: padded(n) });
    const f = cardFields(a).find((x) => x.key === "prompt")!;
    expect(f.text.endsWith(END)).toBe(true);
    expect(f.chars).toBe(n);
    expect(f.long).toBe(n > 300);
    expect(cardIncomplete(a)).toBe(false);
    expect(canApprove(a, true)).toBe(true);
  });

  it("shows the extra card text the kernel adds (a project patch) in full", () => {
    const patch = "diff --git a/x b/x\n" + "+line\n".repeat(900) + `+${END}\n`;
    const a = card({}, { files: ["x"] });
    a.action.card = { patch, root_cause: "x: wrong" };
    const keys = cardFields(a).map((f) => f.key);
    expect(keys).toEqual(["files", "card.patch", "card.root_cause"]);
    expect(cardFields(a).find((f) => f.key === "card.patch")!.text.endsWith(`+${END}\n`)).toBe(true);
  });

  it("never lets an incomplete card be approved", () => {
    expect(canApprove(card({ truncated: true }), true)).toBe(false);                                    // the kernel says it is cut off
    expect(canApprove(card({}, { prompt: "x".repeat(2000) + CUT_MARK }), true)).toBe(false);          // a value still ends in the cut marker
    expect(canApprove(card({}, { nested: { deep: ["ok", "y" + CUT_MARK] } }), true)).toBe(false);
    expect(canApprove(card({}, { key: KEY_MARK }), true)).toBe(false);                                 // replaced because it held a private key
    expect(cardIncomplete(card({}, { prompt: "a normal prompt" }))).toBe(false);
  });

  it("does not offer approval without a PIN or above the yellow level", () => {
    expect(canApprove(card(), false)).toBe(false);
    expect(canApprove(card({ level: "RED" }), true)).toBe(false);
  });

  it("counts the characters on the card", () => {
    expect(cardChars(card({ chars: 4321 }))).toBe(4321);
    expect(cardChars(card({}, { a: "12345" }))).toBeGreaterThanOrEqual(5);
  });
});

describe("invisible and misleading characters (re-review M1)", () => {
  const withInv = (status: "blocked" | "warning" | "notice", extra: Partial<Approval> = {}): Approval => {
    const a = card({ ...extra }, { prompt: "note<U+E0073><U+E0065>" });
    a.action.invisible = { status, invisible_chars: 2, hostile: status === "blocked" ? 2 : 0, benign: status === "notice" ? 2 : 0, has_invisible: true,
      fields: { "params.prompt": { invisible_chars: 2, has_invisible: true, hostile: status === "blocked" ? 2 : 0 } }, flags: ["params.prompt: tag characters spell hidden text: 'se'"] };
    return a;
  };

  it("never offers approval on a blocked card, whatever the server flag says", () => {
    expect(canApprove(withInv("blocked"), true)).toBe(false);
    expect(cardBlocked(withInv("blocked"))).toBe(true);
    expect(cardBlocked(card({ blocked: true }))).toBe(true);
    expect(canApprove(card({ blocked: true }), true)).toBe(false);
  });

  it("keeps a card with only joiners in ordinary text approvable, and asks for an acknowledgement only for a look-alike host", () => {
    expect(canApprove(withInv("notice"), true)).toBe(true);
    expect(cardNeedsAck(withInv("notice"))).toBe(false);
    expect(canApprove(withInv("warning"), true)).toBe(true);
    expect(cardNeedsAck(withInv("warning"))).toBe(true);
    expect(cardNeedsAck(card())).toBe(false);
  });

  it("shows the per-field invisible count", () => {
    const f = cardFields(withInv("blocked")).find((x) => x.key === "prompt")!;
    expect([f.invisible, f.hostile]).toEqual([2, 2]);
    expect(cardInvisible(card())).toBeNull();
    expect(cardFields(card({}, { prompt: "plain" }))[0].invisible).toBe(0);
  });

  it("wires the warning, the block and the acknowledgement into the card", () => {
    expect(commandCenterSource).toMatch(/cardBlocked\(a\)/);
    expect(commandCenterSource).toMatch(/disabled=\{busy \|\| incomplete \|\| blocked \|\| \(needsAck && !ack\)/);
    expect(commandCenterSource).toMatch(/invisible, control or text-direction character/);
    expect(commandCenterSource).toMatch(/type="checkbox" checked=\{ack\}/);
  });
});

describe("CommandCenter approval card wiring", () => {
  it("uses the completeness check and no longer claims the full original is approved when it is cut", () => {
    expect(commandCenterSource).toMatch(/canApprove\(a, pinSet\)/);
    expect(commandCenterSource).toMatch(/disabled=\{busy \|\| incomplete \|\|/);
    expect(commandCenterSource).not.toMatch(/what you approve is the full original/);
    expect(commandCenterSource).not.toMatch(/longer than can be shown here/);
  });

  it("shows the whole target and scrolls long values instead of cutting them", () => {
    expect(commandCenterSource).not.toMatch(/target \?\? ""\)\.slice\(/);
    expect(commandCenterSource).toMatch(/max-h-64 overflow-auto/);
    expect(commandCenterSource).toMatch(/max-h-72 overflow-auto/);
  });
});
