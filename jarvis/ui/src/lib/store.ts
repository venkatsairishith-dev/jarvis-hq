// Live state: the kernel's owner state (polled), its event feed (server-sent events), the console's health, preferences.
// There is no simulated data anywhere: when something cannot be read, the connection state says so.
import { useSyncExternalStore } from "react";
import { api, ApiError, owner } from "./api";
import type { AuditEvent, Health, OwnerState } from "./types";

export type Conn = "connecting" | "live" | "kernel-offline" | "console-offline" | "unauthorized";
export interface Prefs { lang: "en" | "hi" | "te"; speakReplies: boolean; autoSend: boolean; theme: "dark" | "light"; micMuted: boolean }
export interface Toast { id: number; kind: "ok" | "err" | "info"; text: string }
export interface Snapshot {
  owner: OwnerState | null; health: Health | null; conn: Conn; connError: string | null; events: AuditEvent[]; lastSeq: number;
  toasts: Toast[]; prefs: Prefs; lastRefresh: number;
}

const PREF_KEY = "jarvis.prefs.v1";
const DEFAULT_PREFS: Prefs = { lang: "en", speakReplies: true, autoSend: false, theme: "dark", micMuted: false };

function loadPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (raw) return { ...DEFAULT_PREFS, ...JSON.parse(raw) };
  } catch { /* storage may be blocked: defaults are fine */ }
  return { ...DEFAULT_PREFS };
}

let snap: Snapshot = { owner: null, health: null, conn: "connecting", connError: null, events: [], lastSeq: 0, toasts: [], prefs: loadPrefs(), lastRefresh: 0 };
document.documentElement.dataset.theme = snap.prefs.theme;      // before first paint: no flash of the wrong theme
const subs = new Set<() => void>();
const set = (p: Partial<Snapshot>) => { snap = { ...snap, ...p }; subs.forEach((f) => f()); };
let toastId = 1;
let started = false;
let es: EventSource | null = null;
let refreshTimer: number | null = null;
const listeners = new Set<(e: AuditEvent) => void>();

export const store = {
  subscribe(f: () => void) { subs.add(f); return () => { subs.delete(f); }; },
  get: () => snap,

  toast(kind: Toast["kind"], text: string) {
    const id = toastId++;
    set({ toasts: [...snap.toasts, { id, kind, text }] });
    window.setTimeout(() => set({ toasts: snap.toasts.filter((t) => t.id !== id) }), kind === "err" ? 9000 : 4500);
  },
  dismissToast(id: number) { set({ toasts: snap.toasts.filter((t) => t.id !== id) }); },

  setPrefs(p: Partial<Prefs>) {
    const prefs = { ...snap.prefs, ...p };
    if (p.lang && p.lang !== snap.prefs.lang) void api.post("/api/voice/warm", { lang: p.lang }).catch(() => undefined);   // load that language's models now
    set({ prefs });
    try { localStorage.setItem(PREF_KEY, JSON.stringify(prefs)); } catch { /* not persisted: still applied this session */ }
    document.documentElement.dataset.theme = prefs.theme;
  },

  onEvent(f: (e: AuditEvent) => void) { listeners.add(f); return () => { listeners.delete(f); }; },

  async refresh() {
    try {
      const st = await owner.get<OwnerState>("state");
      set({ owner: st, conn: "live", connError: null, lastRefresh: Date.now() / 1000 });
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status === 0) set({ conn: "console-offline", connError: e.message });
        else if (e.status === 401 || e.status === 403) set({ conn: "unauthorized", connError: e.message });
        else set({ conn: "kernel-offline", connError: e.message });
      }
    }
  },

  async refreshHealth() {
    try { set({ health: await api.get<Health>("/api/health") }); } catch { /* the connection chip already says what failed */ }
  },

  start() {
    if (started) return;
    started = true;
    document.documentElement.dataset.theme = snap.prefs.theme;
    void api.post("/api/voice/warm", { lang: snap.prefs.lang }).catch(() => undefined);      // the first spoken request should not wait for a model load
    void this.refresh(); void this.refreshHealth();
    window.setInterval(() => void this.refresh(), 3000);
    window.setInterval(() => void this.refreshHealth(), 15000);
    this.connectStream();
  },

  connectStream() {
    es?.close();
    es = new EventSource(`/api/stream?since=${snap.lastSeq}`);
    es.addEventListener("audit", (m) => {
      try {
        const ev = JSON.parse((m as MessageEvent).data) as AuditEvent;
        set({ events: [ev, ...snap.events].slice(0, 300), lastSeq: Math.max(snap.lastSeq, ev.seq) });
        listeners.forEach((f) => f(ev));
        if (refreshTimer) window.clearTimeout(refreshTimer);
        refreshTimer = window.setTimeout(() => void this.refresh(), 250);   // a state change: look again right away
      } catch { /* a malformed frame is skipped */ }
    });
    es.onerror = () => {                               // the server ends each stream after a few minutes: reconnect with the last id
      es?.close();
      window.setTimeout(() => this.connectStream(), 2000);
    };
  },
};

export function useStore<T>(select: (s: Snapshot) => T): T {
  return useSyncExternalStore(store.subscribe, () => select(snap));
}
