import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { owner } from "./lib/api";
import { store, useStore } from "./lib/store";
import { voice } from "./lib/voice";
import { Badge, Button, Dialog, Kbd } from "./components/ui";
import { CommandCenter } from "./screens/CommandCenter";
import { Desktop } from "./screens/Desktop";
import { AgentFactory } from "./screens/AgentFactory";
import { Tasks } from "./screens/Tasks";
import { Automations } from "./screens/Automations";
import { Workspace } from "./screens/Workspace";
import { Memory } from "./screens/Memory";
import { Models } from "./screens/Models";
import { Settings } from "./screens/Settings";

const SCREENS = [
  { id: "command", label: "Command Center", key: "1", el: <CommandCenter /> },
  { id: "desktop", label: "Agentic OS", key: "2", el: <Desktop /> },
  { id: "agents", label: "Agent Factory", key: "3", el: <AgentFactory /> },
  { id: "tasks", label: "Tasks", key: "4", el: <Tasks /> },
  { id: "automations", label: "Automations", key: "5", el: <Automations /> },
  { id: "workspace", label: "Workspace", key: "6", el: <Workspace /> },
  { id: "memory", label: "Memory", key: "7", el: <Memory /> },
  { id: "models", label: "Models & skills", key: "8", el: <Models /> },
  { id: "settings", label: "Settings & health", key: "9", el: <Settings /> },
] as const;

function useRoute(): string {
  const get = () => window.location.hash.replace(/^#\/?/, "").split("/")[0] || "command";
  return useSyncExternalStore((f) => { window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f); }, get);
}

export function go(id: string) { window.location.hash = "/" + id; }

function Conn() {
  const conn = useStore((s) => s.conn);
  const err = useStore((s) => s.connError);
  const map = {
    live: ["done", "Connected"], connecting: ["wait", "Connecting"], "kernel-offline": ["err", "Kernel unreachable"],
    "console-offline": ["err", "Console offline"], unauthorized: ["err", "Not authorized"],
  } as const;
  const [tone, text] = map[conn];
  return <Badge tone={tone} title={err ?? undefined}>{text}</Badge>;
}

function MicChip() {
  const mode = useSyncExternalStore(voice.subscribe, () => voice.snapshot().mode);
  const tone = mode === "recording" ? "err" : mode === "processing" ? "wait" : mode === "speaking" ? "accent" : mode === "muted" ? "idle" : "done";
  const text = { idle: "Mic ready", recording: "Recording", processing: "Transcribing", speaking: "Speaking", muted: "Mic muted" }[mode];
  return <Badge tone={tone}>{text}</Badge>;
}

export default function App() {
  const route = useRoute();
  const owner_ = useStore((s) => s.owner);
  const theme = useStore((s) => s.prefs.theme);
  const toasts = useStore((s) => s.toasts);
  const [confirmStop, setConfirmStop] = useState(false);
  const [confirmKill, setConfirmKill] = useState(false);

  useEffect(() => { store.start(); }, []);
  useEffect(() => { voice.setMuted(store.get().prefs.micMuted); }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t.isContentEditable;
      if (e.key === "Escape") { void voice.cancel(); voice.stopSpeaking(); }
      if (e.altKey && /^[1-9]$/.test(e.key)) { go(SCREENS[Number(e.key) - 1].id); e.preventDefault(); }
      if (e.key === "/" && !typing) { go("command"); window.setTimeout(() => document.getElementById("command-input")?.focus(), 50); e.preventDefault(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const current = SCREENS.find((s) => s.id === route) ?? SCREENS[0];
  const pending = owner_?.approvals.length ?? 0;
  const running = owner_?.tasks.filter((t) => !["completed", "failed", "cancelled"].includes(t.state)).length ?? 0;

  const stopAll = async () => {
    setConfirmStop(false);
    try {
      const r = await owner.post<{ cancelled_tasks: string[]; denied_approvals: number }>("stop_all");
      store.toast("ok", `Stopped: ${r.cancelled_tasks.length} task(s) cancelled, ${r.denied_approvals} approval(s) denied.`);
      void store.refresh();
    } catch (e) { store.toast("err", (e as Error).message); }
  };
  const kill = async () => {
    setConfirmKill(false);
    try { await owner.post("kill"); store.toast("ok", "Kill switch is ON. Lifting it is done at the kernel console."); void store.refresh(); }
    catch (e) { store.toast("err", (e as Error).message); }
  };

  return (
    <div className="grid h-dvh grid-rows-[auto_minmax(0,1fr)] md:grid-cols-[232px_minmax(0,1fr)] md:grid-rows-[56px_minmax(0,1fr)]">
      <header className="col-span-full flex items-center gap-3 border-b border-line bg-bg2/80 px-4 backdrop-blur md:col-span-2">
        <div className="flex items-center gap-2">
          <span aria-hidden className="grid size-7 place-items-center rounded-lg bg-accent text-white"><svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth="2.2"><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="1.5" fill="currentColor" /></svg></span>
          <span className="text-sm font-semibold tracking-wide">JARVIS</span>
        </div>
        <div className="ml-2 hidden items-center gap-2 sm:flex"><Conn /><MicChip />
          {owner_?.killed && <Badge tone="err" title="The kernel refuses model calls and node jobs until it is lifted at the console">Kill switch ON</Badge>}
          {running > 0 && <Badge tone="run">{running} running</Badge>}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {pending > 0 && <Button kind="primary" onClick={() => go("command")}>{pending} approval{pending > 1 ? "s" : ""} waiting</Button>}
          <Button kind="danger" onClick={() => setConfirmStop(true)} title="Cancel every open task and deny pending approvals">Stop all</Button>
          <Button kind="ghost" onClick={() => setConfirmKill(true)} title="Kill switch: refuses model calls and node jobs" disabled={owner_?.killed}>Kill</Button>
          <Button kind="ghost" aria-label="Toggle light and dark theme" onClick={() => store.setPrefs({ theme: theme === "dark" ? "light" : "dark" })}>{theme === "dark" ? "Light" : "Dark"}</Button>
        </div>
      </header>

      <nav aria-label="Screens" className="flex gap-1 overflow-x-auto border-b border-line bg-bg2/60 p-2 md:flex-col md:overflow-visible md:border-b-0 md:border-r md:p-3">
        {SCREENS.map((s) => (
          <a key={s.id} href={`#/${s.id}`} aria-current={current.id === s.id ? "page" : undefined}
            className={`flex shrink-0 items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm ${current.id === s.id ? "bg-accent-bg font-medium text-accent-fg" : "text-fg2 hover:bg-hover"}`}>
            <span>{s.label}</span><Kbd>Alt+{s.key}</Kbd>
          </a>
        ))}
      </nav>

      <main className="min-h-0 overflow-y-auto p-4 md:p-6" id="main" tabIndex={-1}>
        <div className="mx-auto max-w-[1280px]" key={current.id}>{current.el}</div>
      </main>

      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(420px,92vw)] flex-col gap-2">
        {toasts.map((t) => (
          <div key={t.id} role={t.kind === "err" ? "alert" : "status"} className={`pointer-events-auto flex items-start justify-between gap-3 rounded-lg border px-3 py-2 text-sm shadow-lg ${
            t.kind === "err" ? "border-err/60 bg-err-bg text-err-fg" : t.kind === "ok" ? "border-done/50 bg-done-bg text-done-fg" : "border-edge bg-surface"}`}>
            <span>{t.text}</span>
            <button aria-label="Dismiss" className="text-fg3 hover:text-fg" onClick={() => store.dismissToast(t.id)}>×</button>
          </div>
        ))}
      </div>

      <Dialog open={confirmStop} onClose={() => setConfirmStop(false)} title="Stop everything?"
        footer={<><Button onClick={() => setConfirmStop(false)}>Keep working</Button><Button kind="danger" onClick={stopAll}>Stop all tasks</Button></>}>
        <p className="text-sm text-fg2">This cancels every open task ({running}) and denies every pending approval ({pending}). Lab workers stop within about 20 seconds. Files already delivered stay where they are.</p>
      </Dialog>
      <Dialog open={confirmKill} onClose={() => setConfirmKill(false)} title="Turn the kill switch on?"
        footer={<><Button onClick={() => setConfirmKill(false)}>Cancel</Button><Button kind="danger" onClick={kill}>Kill switch ON</Button></>}>
        <p className="text-sm text-fg2">The kernel will refuse every model call and node job. <strong>Lifting it needs the kernel console</strong> (a deliberate safety rule: it cannot be undone from this page).</p>
      </Dialog>
    </div>
  );
}

export type { ReactNode };
