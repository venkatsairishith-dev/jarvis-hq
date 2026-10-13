import { useCallback, useEffect, useState } from "react";
import { owner } from "../lib/api";
import { store } from "../lib/store";
import { ago } from "../lib/format";
import type { AgentDef } from "../lib/types";
import { Badge, Button, Card, Dialog, Empty, ErrorNote, Field, inputCls, stateTone } from "../components/ui";

type Draft = Omit<AgentDef, "id" | "state" | "version" | "runs_today" | "tasks"> & { id?: string };

function DefinitionView({ d, editable, onChange }: { d: Draft; editable?: boolean; onChange?: (p: Partial<Draft>) => void }) {
  const list = (a: string[]) => a.join("\n");
  const unlist = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
  return (
    <div className="space-y-3 text-sm">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Name"><input className={inputCls} value={d.name} disabled={!editable} onChange={(e) => onChange?.({ name: e.target.value })} /></Field>
        <Field label="Role"><input className={inputCls} value={d.role} disabled={!editable} onChange={(e) => onChange?.({ role: e.target.value })} /></Field>
      </div>
      <Field label="Purpose"><input className={inputCls} value={d.purpose} disabled={!editable} onChange={(e) => onChange?.({ purpose: e.target.value })} /></Field>
      <Field label="Instructions"><textarea rows={5} className={inputCls} value={d.instructions} disabled={!editable} onChange={(e) => onChange?.({ instructions: e.target.value })} /></Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Acceptance criteria (one per line)"><textarea rows={4} className={inputCls} value={list(d.criteria)} disabled={!editable} onChange={(e) => onChange?.({ criteria: unlist(e.target.value) })} /></Field>
        <Field label="Verification procedure"><textarea rows={4} className={inputCls} value={d.verification} disabled={!editable} onChange={(e) => onChange?.({ verification: e.target.value })} /></Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Environment (runtime)"><select className={inputCls} value={d.runtime} disabled={!editable} onChange={(e) => onChange?.({ runtime: e.target.value })}><option>hermes</option><option>opencode</option><option>goose</option></select></Field>
        <Field label="Lane (models)"><input className={inputCls} value={`${d.lane} (lab, never private data)`} disabled /></Field>
        <Field label="Preferred model"><input className={inputCls} value={d.model_pref ?? ""} placeholder="kernel order" disabled={!editable} onChange={(e) => onChange?.({ model_pref: e.target.value || null })} /></Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Attempts (1-5)"><input type="number" min={1} max={5} className={inputCls} value={d.max_attempts} disabled={!editable} onChange={(e) => onChange?.({ max_attempts: Number(e.target.value) })} /></Field>
        <Field label="Time budget per task (s)"><input type="number" min={120} max={5400} className={inputCls} value={d.budget_s} disabled={!editable} onChange={(e) => onChange?.({ budget_s: Number(e.target.value) })} /></Field>
        <Field label="Runs per day"><input type="number" min={1} max={100} className={inputCls} value={d.max_runs_per_day} disabled={!editable} onChange={(e) => onChange?.({ max_runs_per_day: Number(e.target.value) })} /></Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div><p className="mb-1 text-xs font-medium text-fg2">Capabilities</p>
          <ul className="space-y-1">{d.tools.map((t) => <li key={t}><Badge tone="accent">{t}</Badge> <span className="text-xs text-fg3">{d.capability_notes?.[t]}</span></li>)}</ul></div>
        <div><p className="mb-1 text-xs font-medium text-fg2">Permissions and workspace</p>
          <ul className="list-disc space-y-0.5 pl-5 text-fg2">{d.permissions.map((p, i) => <li key={i}>{p}</li>)}<li>Workspace: {d.workspace?.root ?? "the task's own folder"}</li></ul></div>
      </div>
      {d.enforced_by_kernel && (
        <div className="rounded-lg border border-line bg-bg2 p-3 text-xs">
          <p><strong>Enforced by the kernel:</strong> {d.enforced_by_kernel.join(", ")}.</p>
          <p className="mt-1 text-fg3"><strong>Not enforced by the kernel:</strong> {d.not_enforced_by_kernel?.join(" ")}</p>
        </div>
      )}
    </div>
  );
}

export function AgentFactory() {
  const [agents, setAgents] = useState<AgentDef[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [sentence, setSentence] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState<AgentDef | null>(null);
  const [edit, setEdit] = useState<Draft | null>(null);
  const [testText, setTestText] = useState("");
  const [confirm, setConfirm] = useState<{ what: "terminate" | "delete"; a: AgentDef } | null>(null);
  const [typed, setTyped] = useState("");

  const load = useCallback(async () => {
    try { const r = await owner.get<{ agents: AgentDef[] }>("agents"); setAgents(r.agents); setErr(null); setSel((s) => r.agents.find((a) => a.id === s?.id) ?? null); }
    catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 5000); return () => window.clearInterval(t); }, [load]);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try { await fn(); store.toast("ok", ok); await load(); } catch (e) { store.toast("err", (e as Error).message); } finally { setBusy(false); }
  };
  const propose = async () => {
    setBusy(true); setErr(null);
    try { setDraft(await owner.post<Draft>("agents/draft", { sentence })); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Agent Factory</h1>
      <Card title="Describe the specialist you need">
        <div className="space-y-3">
          <Field label="One sentence" hint="Example: Create a Python software engineer agent that writes code, tests it and fixes failures.">
            <textarea id="agent-sentence" rows={2} className={inputCls} value={sentence} onChange={(e) => setSentence(e.target.value)} />
          </Field>
          <Button kind="primary" disabled={busy || sentence.trim().length < 8} onClick={propose}>Propose a definition</Button>
          {err && <ErrorNote>{err}</ErrorNote>}
        </div>
      </Card>

      <Dialog open={!!draft} onClose={() => setDraft(null)} title="Review the proposed agent"
        footer={<><Button onClick={() => setDraft(null)}>Discard</Button>
          <Button kind="primary" disabled={busy} onClick={() => run(async () => { await owner.post("agents", { definition: draft }); setDraft(null); setSentence(""); }, "Agent saved as a draft.")}>Save as draft</Button></>}>
        {draft && <div className="max-h-[60vh] overflow-y-auto pr-1"><DefinitionView d={draft} editable onChange={(p) => setDraft({ ...draft, ...p })} />
          <p className="mt-3 text-xs text-fg3">Nothing runs until you activate the saved draft. This definition was drafted from a template; edit anything above.</p></div>}
      </Dialog>

      <Card title={`Your agents${agents ? ` (${agents.length})` : ""}`}>
        {!agents ? <p className="text-sm text-fg3">Loading…</p> : agents.length === 0 ? <Empty title="No agents yet" hint="Describe one above. Each agent has its own identity, limits and task history." /> :
          <ul className="divide-y divide-line">{agents.map((a) => (
            <li key={a.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2">
                <button className="text-left text-sm font-semibold hover:underline" onClick={() => { setSel(a); setEdit(null); }}>{a.name}</button>
                <Badge tone={stateTone(a.state)}>{a.state}</Badge><Badge tone="idle">{a.runtime}</Badge>
                <span className="num text-xs text-fg3">{a.runs_today}/{a.max_runs_per_day} runs today · {a.tasks.length} recent task(s)</span>
              </div>
              <p className="mt-1 text-sm text-fg2">{a.purpose}</p>
            </li>))}</ul>}
      </Card>

      <Dialog open={!!sel} onClose={() => { setSel(null); setEdit(null); }} title={sel ? `Agent: ${sel.name}` : ""}
        footer={sel && <div className="flex flex-wrap gap-2">
          {edit ? <><Button onClick={() => setEdit(null)}>Cancel edit</Button><Button kind="primary" disabled={busy} onClick={() => run(async () => { await owner.post("agents/update", { id: sel.id, version: sel.version, definition: edit }); setEdit(null); }, "Saved. An edited agent returns to draft for review.")}>Save changes</Button></>
            : <>
              {sel.state === "draft" && <Button kind="primary" disabled={busy} onClick={() => run(() => owner.post("agents/activate", { id: sel.id }), "Agent activated.")}>Activate</Button>}
              {sel.state === "active" && <Button disabled={busy} onClick={() => run(() => owner.post("agents/pause", { id: sel.id }), "Agent paused.")}>Pause</Button>}
              {sel.state === "paused" && <Button disabled={busy} onClick={() => run(() => owner.post("agents/resume", { id: sel.id }), "Agent resumed.")}>Resume</Button>}
              {sel.state !== "terminated" && <Button disabled={busy} onClick={() => setEdit({ ...sel })}>Edit</Button>}
              <Button disabled={busy} onClick={() => run(() => owner.post("agents/clone", { id: sel.id }), "Cloned as a new draft.")}>Clone</Button>
              {sel.state !== "terminated" && <Button kind="danger" onClick={() => { setConfirm({ what: "terminate", a: sel }); setTyped(""); }}>Terminate</Button>}
              {sel.state !== "active" && <Button kind="danger" onClick={() => { setConfirm({ what: "delete", a: sel }); setTyped(""); }}>Delete</Button>}
            </>}
        </div>}>
        {sel && <div className="max-h-[62vh] space-y-4 overflow-y-auto pr-1">
          <div className="flex flex-wrap items-center gap-2"><Badge tone={stateTone(sel.state)}>{sel.state}</Badge><span className="text-xs text-fg3">
            {sel.state === "draft" ? "Not active: nothing runs except an explicit test run." : sel.state === "active" ? "Active: may be given work." : sel.state === "paused" ? "Paused: no new work." : "Terminated: cannot run again."}</span></div>
          <DefinitionView d={edit ?? sel} editable={!!edit} onChange={(p) => edit && setEdit({ ...edit, ...p })} />
          {!edit && sel.state !== "terminated" && (
            <div className="space-y-2 rounded-lg border border-line p-3">
              <Field label="Bounded test run (the lab runs it, an independent check verifies it)">
                <textarea rows={2} className={inputCls} value={testText} onChange={(e) => setTestText(e.target.value)} placeholder="e.g. Write a function that reverses words in a sentence, with two unit tests" />
              </Field>
              <Button disabled={busy || testText.trim().length < 8} onClick={() => run(async () => { const r = await owner.post<{ task: string }>("agents/test", { id: sel.id, task: testText }); store.toast("info", `Test task ${r.task} started. Follow it in Tasks.`); setTestText(""); }, "Test run queued.")}>Run test</Button>
            </div>)}
          {!edit && <div><p className="mb-1 text-xs font-medium text-fg2">Task history (this agent only)</p>
            {sel.tasks.length === 0 ? <p className="text-sm text-fg3">No tasks yet.</p> : <ul className="space-y-1 text-sm">{sel.tasks.map((t) => (
              <li key={t.id} className="flex items-center gap-2"><Badge tone={stateTone(t.state)}>{t.state}</Badge><span className="truncate">{t.goal}</span><span className="num ml-auto text-xs text-fg3">{ago(t.created)} · {t.attempts}/{t.max_attempts}</span></li>))}</ul>}</div>}
        </div>}
      </Dialog>

      <Dialog open={!!confirm} onClose={() => setConfirm(null)} title={confirm?.what === "delete" ? "Delete this agent?" : "Terminate this agent?"}
        footer={confirm && <><Button onClick={() => setConfirm(null)}>Cancel</Button>
          <Button kind="danger" disabled={typed !== confirm.a.id || busy} onClick={() => run(async () => { await owner.post(confirm.what === "delete" ? "agents/delete" : "agents/terminate", { id: confirm.a.id, confirm: typed }); setConfirm(null); setSel(null); }, confirm.what === "delete" ? "Agent deleted." : "Agent terminated.")}>Confirm</Button></>}>
        {confirm && <div className="space-y-2 text-sm"><p>{confirm.what === "delete" ? "The definition is removed; its past tasks stay in the task history." : "A terminated agent cannot run again; clone it to reuse the definition."}</p>
          <Field label={`Type ${confirm.a.id} to confirm`}><input className={inputCls} value={typed} onChange={(e) => setTyped(e.target.value.trim())} /></Field></div>}
      </Dialog>
    </div>
  );
}
