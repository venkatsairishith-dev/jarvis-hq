import { useCallback, useEffect, useState } from "react";
import { owner } from "../lib/api";
import { store } from "../lib/store";
import { ago, clock, until } from "../lib/format";
import type { Automation, AutomationPreview } from "../lib/types";
import { Badge, Button, Card, Dialog, Empty, ErrorNote, Field, inputCls, stateTone } from "../components/ui";

const TEMPLATES = {
  job: { label: "Check the jarvis-hq project (runs its tests on this laptop)", action_kind: "job", action: { kind: "project_check", params: { project: "jarvis-hq" }, node: "laptop" } },
  lab_task: { label: "A lab task (an agent builds it, an independent check verifies it)", action_kind: "lab_task", action: { goal: "", instructions: "", criteria: [""], agent: "hermes", max_attempts: 2, budget_s: 1800 } },
  command: { label: "Ask JARVIS (your own agent) to do something", action_kind: "command", action: { text: "" } },
} as const;
type Kind = keyof typeof TEMPLATES;

export function Automations() {
  const [list, setList] = useState<Automation[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<Kind>("job");
  const [name, setName] = useState("");
  const [phrase, setPhrase] = useState("every morning at 8");
  const [when, setWhen] = useState<"time" | "file">("time");
  const [watch, setWatch] = useState("downloads");
  const [glob, setGlob] = useState("*");
  const [watches, setWatches] = useState<{ node: string; watch: string }[]>([]);
  const [cron, setCron] = useState("");
  const [tz, setTz] = useState("Asia/Kolkata");
  const [goal, setGoal] = useState("");
  const [instr, setInstr] = useState("");
  const [crit, setCrit] = useState("");
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<AutomationPreview | null>(null);
  const [perr, setPerr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<Automation | null>(null);
  const [confirmDel, setConfirmDel] = useState<Automation | null>(null);
  const [typed, setTyped] = useState("");

  const load = useCallback(async () => {
    try { const r = await owner.get<{ automations: Automation[] }>("automations"); setList(r.automations); setErr(null); setSel((s) => r.automations.find((a) => a.id === s?.id) ?? null); }
    catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 5000); return () => window.clearInterval(t); }, [load]);
  useEffect(() => { owner.get<{ watches: { node: string; watch: string }[] }>("watches").then((r) => setWatches(r.watches)).catch(() => setWatches([])); }, []);

  const definition = () => {
    const base = JSON.parse(JSON.stringify(TEMPLATES[kind])) as { action_kind: string; action: Record<string, unknown> };
    if (kind === "lab_task") base.action = { ...base.action, goal, instructions: instr, criteria: crit.split("\n").map((x) => x.trim()).filter(Boolean) };
    if (kind === "command") base.action = { text };
    if (when === "file") return { name, action_kind: base.action_kind, action: base.action, trigger_kind: "event", event_spec: { watch, glob: glob || "*" }, schedule: "", tz };
    return { name, action_kind: base.action_kind, action: base.action, trigger_kind: "cron", schedule: cron, tz };
  };
  const body = () => ({ definition: definition(), phrase: when === "time" && !cron ? phrase : undefined });

  const doPreview = async () => {
    setBusy(true); setPerr(null); setPreview(null);
    try { setPreview(await owner.post<AutomationPreview>("automations/preview", body())); } catch (e) { setPerr((e as Error).message); } finally { setBusy(false); }
  };
  const create = async () => {
    setBusy(true);
    try { await owner.post("automations", body()); store.toast("ok", "Automation saved and scheduled."); setOpen(false); setPreview(null); setName(""); await load(); }
    catch (e) { setPerr((e as Error).message); } finally { setBusy(false); }
  };
  const act = async (a: Automation, what: "pause" | "resume" | "run") => {
    try {
      const r = await owner.post<Automation | { status: string; run: { detail?: string } }>(`automations/${what}`, { id: a.id });
      store.toast(what === "run" ? ((r as { status: string }).status === "fired" ? "ok" : "err") : "ok", what === "run" ? `Run: ${(r as { status: string }).status}${(r as { run: { detail?: string } }).run.detail ? " · " + (r as { run: { detail?: string } }).run.detail : ""}` : `Automation ${what}d.`);
      await load();
    } catch (e) { store.toast("err", (e as Error).message); }
  };
  const del = async () => {
    if (!confirmDel) return;
    try { await owner.post("automations/delete", { id: confirmDel.id, confirm: typed }); store.toast("ok", "Deleted."); setConfirmDel(null); setSel(null); await load(); }
    catch (e) { store.toast("err", (e as Error).message); }
  };

  const trig = (a: Automation) => a.trigger_kind === "event" ? `when a file arrives in ${(a as unknown as { event_spec?: { watch: string; glob: string } }).event_spec?.watch ?? "?"}` : a.trigger_kind === "cron" ? `cron ${a.schedule}` : a.trigger_kind === "interval" ? `every ${Number(a.schedule) / 60} min` : `once ${a.schedule}`;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h1 className="text-lg font-semibold">Automation Studio</h1><Button kind="primary" onClick={() => setOpen(true)}>New automation</Button></div>
      {err && <ErrorNote>{err}</ErrorNote>}
      <Card>
        {!list ? <p className="text-sm text-fg3">Loading…</p> : list.length === 0 ? <Empty title="No automations" hint="Create one in plain words, such as “every morning at 8”. You see the next runs and every side effect before it is saved." /> :
          <ul className="divide-y divide-line">{list.map((a) => (
            <li key={a.id} className="space-y-1 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <button className="text-sm font-semibold hover:underline" onClick={() => setSel(a)}>{a.name}</button>
                <Badge tone={stateTone(a.state)}>{a.state}</Badge><Badge>{a.action_kind.replace("_", " ")}</Badge>
                <span className="text-xs text-fg3">{trig(a)} · {a.tz}</span>
                <span className="num ml-auto text-xs text-fg3">next {a.next_run ? `${until(a.next_run)} (${clock(a.next_run)})` : "none"}</span>
              </div>
              {a.paused_reason && <p className="text-xs text-wait-fg">{a.paused_reason}</p>}
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => act(a, "run")}>Run now</Button>
                {a.enabled ? <Button onClick={() => act(a, "pause")}>Pause</Button> : <Button onClick={() => act(a, "resume")} disabled={a.state === "completed"}>Resume</Button>}
                <Button kind="ghost" onClick={() => setSel(a)}>History</Button>
                <Button kind="danger" onClick={() => { setConfirmDel(a); setTyped(""); }}>Delete</Button>
              </div>
            </li>))}</ul>}
      </Card>

      <Dialog open={open} onClose={() => { setOpen(false); setPreview(null); setPerr(null); }} title="New automation"
        footer={<><Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button disabled={busy || !name.trim()} onClick={doPreview}>Preview</Button>
          <Button kind="primary" disabled={busy || !preview} onClick={create} title={preview ? "" : "Preview first"}>Save and schedule</Button></>}>
        <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
          <Field label="Name"><input className={inputCls} value={name} onChange={(e) => { setName(e.target.value); setPreview(null); }} /></Field>
          <Field label="What should run"><select className={inputCls} value={kind} onChange={(e) => { setKind(e.target.value as Kind); setPreview(null); }}>{(Object.keys(TEMPLATES) as Kind[]).map((k) => <option key={k} value={k}>{TEMPLATES[k].label}</option>)}</select></Field>
          {kind === "lab_task" && <>
            <Field label="Goal"><input className={inputCls} value={goal} onChange={(e) => { setGoal(e.target.value); setPreview(null); }} /></Field>
            <Field label="Instructions for the lab agent"><textarea rows={3} className={inputCls} value={instr} onChange={(e) => { setInstr(e.target.value); setPreview(null); }} /></Field>
            <Field label="Acceptance criteria (one per line)"><textarea rows={3} className={inputCls} value={crit} onChange={(e) => { setCrit(e.target.value); setPreview(null); }} /></Field></>}
          {kind === "command" && <Field label="What to ask JARVIS"><textarea rows={3} className={inputCls} value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }} /></Field>}
          <Field label="Start it"><select className={inputCls} value={when} onChange={(e) => { setWhen(e.target.value as "time" | "file"); setPreview(null); }}>
            <option value="time">On a schedule</option><option value="file">When a file arrives in a watched folder</option></select></Field>
          {when === "file" ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Watched folder" hint={watches.length ? "Folders this laptop allows (config/watches.json)." : "No node has reported a watched folder yet."}>
                <select className={inputCls} value={watch} onChange={(e) => { setWatch(e.target.value); setPreview(null); }}>
                  {(watches.length ? watches.map((w) => w.watch) : ["downloads"]).map((w) => <option key={w}>{w}</option>)}</select></Field>
              <Field label="File name pattern" hint="* matches anything; for example *.pdf"><input className={inputCls} value={glob} onChange={(e) => { setGlob(e.target.value); setPreview(null); }} /></Field>
            </div>
          ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="When, in words" hint="every morning at 7:30 · weekdays at 6pm · every monday at 9 · every 30 minutes"><input className={inputCls} value={phrase} disabled={!!cron} onChange={(e) => { setPhrase(e.target.value); setPreview(null); }} /></Field>
            <Field label="Or a cron expression" hint="minute hour day month weekday, e.g. 0 8 * * 1-5"><input className={inputCls} value={cron} onChange={(e) => { setCron(e.target.value); setPreview(null); }} /></Field>
          </div>)}
          <Field label="Time zone"><input className={inputCls} value={tz} onChange={(e) => { setTz(e.target.value); setPreview(null); }} /></Field>
          {perr && <ErrorNote>{perr}</ErrorNote>}
          {preview && <div className="space-y-2 rounded-lg border border-line bg-bg2 p-3 text-sm">
            <p className="font-medium">Next runs</p>
            <ul className="list-disc pl-5">{preview.next_runs.map((r) => <li key={r.utc}>{r.local} <span className="text-fg3">({r.utc})</span></li>)}</ul>
            <p className="font-medium">Side effects</p>
            <ul className="list-disc pl-5">{preview.side_effects.map((s, i) => <li key={i}>{s}</li>)}</ul>
            <p className="text-xs text-fg3">Limits: {JSON.stringify(preview.limits)}</p>
          </div>}
        </div>
      </Dialog>

      <Dialog open={!!sel} onClose={() => setSel(null)} title={sel ? `Run history: ${sel.name}` : ""} footer={<Button onClick={() => setSel(null)}>Close</Button>}>
        {sel && <div className="max-h-[60vh] space-y-2 overflow-y-auto text-sm">
          <p className="text-fg2">{trig(sel)} · {sel.tz} · catch-up {sel.catchup}, overlap {sel.overlap}, max {sel.max_runs_per_day}/day, pauses after {sel.pause_after_failures} failures (streak {sel.failure_streak}).</p>
          {sel.runs.length === 0 ? <Empty title="No runs yet" /> : <ul className="divide-y divide-line">{sel.runs.map((r) => (
            <li key={r.id} className="space-y-0.5 py-2"><div className="flex items-center gap-2"><Badge tone={stateTone(r.status)}>{r.status}</Badge><span className="text-xs text-fg3">{r.source} · {ago(r.fired_at ?? r.scheduled_for)}</span></div>
              <p className="break-all font-mono text-xs text-fg3">{r.run_key}</p>
              {(r.task_id || r.job_id || r.command_id) && <p className="text-xs text-fg2">{r.task_id ? `task ${r.task_id}` : r.job_id ? `job ${r.job_id}` : `command ${r.command_id}`}</p>}
              {r.detail && <p className="text-xs text-fg2">{r.detail}</p>}</li>))}</ul>}
        </div>}
      </Dialog>

      <Dialog open={!!confirmDel} onClose={() => setConfirmDel(null)} title="Delete this automation?"
        footer={<><Button onClick={() => setConfirmDel(null)}>Cancel</Button><Button kind="danger" disabled={typed !== confirmDel?.id} onClick={del}>Delete</Button></>}>
        {confirmDel && <div className="space-y-2 text-sm"><p>“{confirmDel.name}” and its run history are removed. This cannot be undone.</p><Field label={`Type ${confirmDel.id} to confirm`}><input className={inputCls} value={typed} onChange={(e) => setTyped(e.target.value.trim())} /></Field></div>}
      </Dialog>
    </div>
  );
}
