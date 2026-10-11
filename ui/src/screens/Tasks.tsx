import { useEffect, useMemo, useState } from "react";
import { owner } from "../lib/api";
import { store, useStore } from "../lib/store";
import { ago, clock, duration, TERMINAL } from "../lib/format";
import type { AuditEvent, Task } from "../lib/types";
import { Badge, Button, Card, Dialog, Empty, ErrorNote, inputCls, stateTone, Tabs } from "../components/ui";

type Filter = "all" | "open" | "done" | "failed";
const NO_TASKS: Task[] = [];

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const [t, setT] = useState<Task | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const live = useStore((s) => s.owner?.tasks.find((x) => x.id === id));
  useEffect(() => { owner.get<Task>(`task/${id}`).then(setT).catch((e: Error) => setErr(e.message)); }, [id, live?.updated]);
  useEffect(() => store.onEvent((e) => { if (JSON.stringify(e.payload).includes(id)) setEvents((a) => [e, ...a].slice(0, 40)); }), [id]);
  const control = async (op: "pause" | "resume" | "cancel") => {
    setBusy(true);
    try { await owner.post("task", { id, op }); store.toast("ok", `Task ${op === "cancel" ? "cancelled" : op + "d"}.`); void store.refresh(); } catch (e) { store.toast("err", (e as Error).message); } finally { setBusy(false); }
  };
  const t_ = t ?? live ?? null;
  return (
    <Dialog open onClose={onClose} title={t_ ? `Task ${t_.id}` : "Task"}
      footer={t_ && !TERMINAL.has(t_.state) ? <>
        {t_.state === "paused" ? <Button disabled={busy} onClick={() => control("resume")}>Resume</Button> : <Button disabled={busy} onClick={() => control("pause")}>Pause</Button>}
        <Button kind="danger" disabled={busy} onClick={() => control("cancel")}>Cancel task</Button></> : <Button onClick={onClose}>Close</Button>}>
      {err && <ErrorNote>{err}</ErrorNote>}
      {t_ && (
        <div className="max-h-[62vh] space-y-3 overflow-y-auto pr-1 text-sm">
          <div className="flex flex-wrap items-center gap-2"><Badge tone={stateTone(t_.state)}>{t_.state}</Badge>
            {t_.verification?.passed === true && <Badge tone="done">independently verified</Badge>}
            {t_.verification?.passed === false && <Badge tone="err">verification failed</Badge>}
            <span className="num text-xs text-fg3">attempt {t_.attempts}/{t_.max_attempts} · {t_.owner} · {t_.lane} lane · {t_.source}</span></div>
          <p className="font-medium">{t_.goal}</p>
          {t_.step && <p><span className="text-fg3">Current step:</span> {t_.step}</p>}
          {t_.result && <p className="rounded-lg bg-bg2 p-2">{t_.result}</p>}
          <div><p className="mb-1 text-xs font-medium text-fg2">Acceptance criteria</p><ul className="list-disc pl-5 text-fg2">{t_.criteria.map((c, i) => <li key={i}>{c}</li>)}</ul></div>
          {t_.plan.length > 0 && <div><p className="mb-1 text-xs font-medium text-fg2">Plan</p><ol className="list-decimal pl-5 text-fg2">{t_.plan.map((c, i) => <li key={i}>{c}</li>)}</ol></div>}
          {t_.evidence.length > 0 && <div><p className="mb-1 text-xs font-medium text-fg2">Evidence</p><ul className="list-disc pl-5 break-words text-fg2">{t_.evidence.map((c, i) => <li key={i}>{c}</li>)}</ul></div>}
          {t_.verification && Object.keys(t_.verification).length > 0 && (
            <details><summary className="cursor-pointer text-xs font-medium text-fg2">Verification record</summary><pre className="mt-1 max-h-48 overflow-auto rounded-lg bg-bg2 p-2 font-mono text-xs">{JSON.stringify(t_.verification, null, 2)}</pre></details>)}
          <div><p className="mb-1 text-xs font-medium text-fg2">Log (newest first)</p>
            <ul className="space-y-1 font-mono text-xs">{[...events.map((e) => ({ ts: e.ts, state: e.kind, note: "" })), ...t_.events].slice(0, 30).map((e, i) => (
              <li key={i}><span className="text-fg3">{clock(e.ts)}</span> {e.state}{e.note ? ` · ${e.note}` : ""}</li>))}</ul></div>
        </div>)}
    </Dialog>
  );
}

export function Tasks() {
  const tasks = useStore((s) => s.owner?.tasks) ?? NO_TASKS;
  const conn = useStore((s) => s.conn);
  const [f, setF] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const shown = useMemo(() => tasks.filter((t) => (f === "all" || (f === "open" && !TERMINAL.has(t.state)) || (f === "done" && t.state === "completed") || (f === "failed" && (t.state === "failed" || t.state === "cancelled")))
    && (!q || `${t.goal} ${t.id} ${t.owner} ${t.state}`.toLowerCase().includes(q.toLowerCase()))), [tasks, f, q]);
  const count = (fn: (t: Task) => boolean) => tasks.filter(fn).length;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Tasks</h1>
        <Tabs value={f} onChange={setF} items={[{ id: "all", label: "All", count: tasks.length }, { id: "open", label: "Open", count: count((t) => !TERMINAL.has(t.state)) },
          { id: "done", label: "Completed", count: count((t) => t.state === "completed") }, { id: "failed", label: "Failed / cancelled", count: count((t) => t.state === "failed" || t.state === "cancelled") }]} />
      </div>
      <input aria-label="Search tasks" placeholder="Search goal, id, agent or state" className={inputCls} value={q} onChange={(e) => setQ(e.target.value)} />
      {conn !== "live" && <ErrorNote>The kernel is not reachable, so this list may be out of date.</ErrorNote>}
      <Card>
        {shown.length === 0 ? <Empty title={tasks.length ? "No task matches" : "No tasks yet"} hint={tasks.length ? undefined : "Tasks appear when JARVIS takes on work. Each is a durable record in the kernel."} /> :
          <div className="overflow-x-auto"><table className="w-full text-left text-sm">
            <thead className="text-xs text-fg3"><tr><th className="py-2 pr-3">State</th><th className="pr-3">Goal</th><th className="pr-3">Agent</th><th className="pr-3">Attempts</th><th className="pr-3">Elapsed</th><th>Updated</th></tr></thead>
            <tbody>{shown.map((t) => (
              <tr key={t.id} className="cursor-pointer border-t border-line hover:bg-hover" onClick={() => setOpen(t.id)}>
                <td className="py-2 pr-3"><Badge tone={stateTone(t.state)}>{t.state.replace("_", " ")}</Badge></td>
                <td className="max-w-[380px] truncate pr-3"><button className="text-left hover:underline" onClick={(e) => { e.stopPropagation(); setOpen(t.id); }}>{t.goal}</button></td>
                <td className="pr-3 text-fg2">{t.owner}</td><td className="num pr-3">{t.attempts}/{t.max_attempts}</td>
                <td className="num pr-3">{duration((TERMINAL.has(t.state) ? t.updated : Date.now() / 1000) - t.created)}</td>
                <td className="num text-fg3">{ago(t.updated)}</td></tr>))}</tbody></table></div>}
      </Card>
      {open && <Detail id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
