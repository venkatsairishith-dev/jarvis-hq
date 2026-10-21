// Review M6 (2026-10-18): the owner approves what he SEES. The kernel's approval card now carries the complete scrubbed text of
// the action (it used to cut every value at 2000 characters while the approval covered all of it). A card the kernel marks as cut
// off, or any value that still ends in the cut marker or was replaced because it held a private key, is never approvable here.
import type { Approval, InvisibleReport } from "./types";

export const CUT_MARK = "...[truncated]";          // what policy.Action.describe appends to a value it had to cut
export const KEY_MARK = "[BLOCKED: private key]";  // what it puts in place of a value that held a private key
export const LONG_VALUE = 300;                     // from this many characters a value gets its own scrolling box

function anyCut(x: unknown): boolean {
  if (typeof x === "string") return x.endsWith(CUT_MARK) || x === KEY_MARK;
  if (Array.isArray(x)) return x.some(anyCut);
  if (x && typeof x === "object") return Object.values(x as Record<string, unknown>).some(anyCut);
  return false;
}

function textLen(x: unknown): number {
  if (typeof x === "string") return x.length;
  if (Array.isArray(x)) return x.reduce((n: number, v) => n + textLen(v), 0);
  if (x && typeof x === "object") return Object.entries(x as Record<string, unknown>).reduce((n, [k, v]) => n + k.length + textLen(v), 0);
  return String(x ?? "").length;
}

/** True when the card does not show everything the approval covers. */
export function cardIncomplete(a: Approval): boolean {
  return a.truncated === true || anyCut(a.action);
}

/** How many characters of text the card holds (the kernel's own count when it sent one). */
export function cardChars(a: Approval): number {
  return typeof a.chars === "number" ? a.chars : textLen(a.action);
}

/** Invisible, control or direction characters make the card unapprovable (the server refuses too): what is shown may not be what is read. */
export function cardBlocked(a: Approval): boolean {
  return a.blocked === true || a.action.invisible?.status === "blocked";
}

/** A look-alike or mixed-script host name, or benign invisible characters: approvable, but only with the owner's explicit acknowledgement. */
export function cardNeedsAck(a: Approval): boolean {
  const st = a.action.invisible?.status;
  return st === "warning";
}

export function cardInvisible(a: Approval): InvisibleReport | null {
  return a.action.invisible ?? null;
}

/** Whether the Approve button may be offered at all: the PIN is set, the level is approvable here, and the card is complete and plain. */
export function canApprove(a: Approval, pinSet: boolean): boolean {
  return pinSet && a.level === "YELLOW_APPROVAL" && !cardIncomplete(a) && !cardBlocked(a);
}

export interface CardField { key: string; text: string; chars: number; long: boolean; invisible: number; hostile: number }

/** Every argument of the action, plus any extra text the kernel put on the card (a project patch), each in full. */
export function cardFields(a: Approval): CardField[] {
  const out: CardField[] = [];
  for (const [group, obj] of [["", a.action.params], ["card.", a.action.card]] as const) {
    for (const [k, v] of Object.entries(obj ?? {})) {
      const text = typeof v === "string" ? v : JSON.stringify(v, null, 2);
      const rep = a.action.invisible?.fields?.[(group === "" ? "params." : "card.") + k];
      out.push({ key: group + k, text, chars: text.length, long: text.length > LONG_VALUE || text.includes("\n"),
        invisible: rep?.invisible_chars ?? 0, hostile: rep?.hostile ?? 0 });
    }
  }
  return out;
}
