import { useCallback, useEffect, useState } from "react";
import { owner } from "../lib/api";
import { store } from "../lib/store";
import { ago, clock } from "../lib/format";
import type { MemoryEntry } from "../lib/types";
import { Badge, Button, Card, Dialog, Empty, ErrorNote, Field, inputCls } from "../components/ui";

interface Mem { owners: string[]; owner: string; scope: string; entries: MemoryEntry[] }
interface Lesson { id: string; owner: string; text: string; task: string | null; created: number; last_check: { ok: boolean; checks: { check: string; ok: boolean; what: string }[] } | null }

export function Memory() {
  const [m, setM] = useState<Mem | null>(null);
  const [who, setWho] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [edit, setEdit] = useState<MemoryEntry | null>(null);
  const [text, setText] = useState("");
  const [forget, setForget] = useState<MemoryEntry | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const loadLessons = useCallback(async () => {
    try { setLessons((await owner.get<{ lessons: Lesson[] }>("lessons")).lessons); } catch { setLessons([]); }
  }, []);
  useEffect(() => { void loadLessons(); }, [loadLessons]);
  const decide = async (l: Lesson, what: "activate" | "reject") => {
    setBusy(true);
    try {
      const r = await owner.post<{ state: string; checks: { check: string; ok: boolean; what: string }[] }>(`lessons/${what}`, { id: l.id, owner: l.owner });
      const failed = r.checks.filter((c) => !c.ok).map((c) => c.what);
      if (what === "activate") store.toast(r.state === "active" ? "ok" : "err", r.state === "active" ? "Lesson activated: it is now part of JARVIS's memory." : `The gate stopped it: ${failed.join("; ")}`);
      else store.toast("info", "Lesson rejected.");
      await loadLessons(); if (m) await load(m.owner);
    } catch (e) { store.toast("err", (e as Error).message); } finally { setBusy(false); }
  };

  const load = useCallback(async (o?: string) => {
    try { const r = await owner.get<Mem>(`memory${o ? `?owner=${encodeURIComponent(o)}` : ""}`); setM(r); setWho(r.owner); setErr(null); }
    catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    if (!m) return;
    setBusy(true);
    try {
      if (edit) await owner.post("memory", { op: "update", owner: m.owner, id: edit.id, text, expected_version: edit.version });
      else await owner.post("memory", { op: "add", owner: m.owner || "owner", text, kind: "preference" });
      store.toast("ok", edit ? "Corrected. The same entry now has the new text (no duplicate)." : "Saved."); setEdit(null); setAdding(false); await load(m.owner);
    } catch (e) { store.toast("err", (e as Error).message); } finally { setBusy(false); }
  };
  const doForget = async () => {
    if (!m || !forget) return;
    setBusy(true);
    try {
      const r = await owner.post<{ verified: boolean }>("memory", { op: "forget", owner: m.owner, id: forget.id });
      store.toast(r.verified ? "ok" : "err", r.verified ? "Forgotten, and the store was read again to confirm it is gone." : "The entry could not be confirmed as removed.");
      setForget(null); await load(m.owner);
    } catch (e) { store.toast("err", (e as Error).message); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Memory and learning</h1>
        <div className="flex items-center gap-2">
          {m && m.owners.length > 1 && <select aria-label="Whose memory" className={`${inputCls} !w-auto`} value={who} onChange={(e) => void load(e.target.value)}>{m.owners.map((o) => <option key={o}>{o}</option>)}</select>}
          <Button kind="primary" onClick={() => { setAdding(true); setText(""); }}>Add a preference</Button>
        </div>
      </div>
      <p className="text-sm text-fg3">What JARVIS carries into every session. Each entry has a stable id, so a correction replaces it instead of adding a duplicate, and forgetting erases the text and every earlier version.</p>
      {err && <ErrorNote>{err}</ErrorNote>}
      <Card title={m?.owner ? `Entries of “${m.owner}”` : "Entries"}>
        {!m ? <p className="text-sm text-fg3">Loading…</p> : m.entries.length === 0 ? <Empty title="No saved memory" hint="Tell JARVIS “remember that I prefer dark themes”, or add one here." /> :
          <ul className="divide-y divide-line">{m.entries.map((e) => (
            <li key={e.id} className="space-y-1 py-3">
              <p className="text-sm">{e.text}</p>
              <div className="flex flex-wrap items-center gap-2 text-xs text-fg3">
                <Badge>{e.kind}</Badge><span className="font-mono">{e.id}</span><span>v{e.version}</span><span>updated {ago(e.updated)}</span>
                <span className="ml-auto flex gap-2">
                  <Button kind="ghost" onClick={() => { setEdit(e); setText(e.text); }}>Correct</Button>
                  <Button kind="ghost" onClick={() => setForget(e)}>Forget</Button></span>
              </div>
              {e.history.length > 1 && <details className="text-xs text-fg3"><summary className="cursor-pointer">History ({e.history.length})</summary>
                <ul className="mt-1 space-y-0.5">{e.history.map((h, i) => <li key={i}>v{h.v} · {h.op} · {clock(h.at)}{h.text ? ` · ${h.text}` : ""}</li>)}</ul></details>}
            </li>))}</ul>}
        <p className="mt-3 text-xs text-fg3">Where entries are used: they are loaded into the system prompt of the owner's JARVIS session at the start of every conversation (the kernel's memory plugin). Texts that look like secrets are refused.</p>
      </Card>

      <Card title={`Lessons proposed from repaired tasks${lessons.length ? ` (${lessons.length})` : ""}`}>
        {lessons.length === 0 ? <Empty title="No candidate lessons" hint="When a task fails, is repaired and then passes independent verification, the kernel proposes what it learned. Nothing becomes active until it passes the checks below and you accept it." /> :
          <ul className="divide-y divide-line">{lessons.map((l) => (
            <li key={l.id} className="space-y-2 py-3">
              <p className="text-sm">{l.text}</p>
              <p className="text-xs text-fg3">From task <span className="font-mono">{l.task ?? "?"}</span> · for {l.owner}</p>
              {l.last_check && !l.last_check.ok && <ErrorNote>Not activated: {l.last_check.checks.filter((c) => !c.ok).map((c) => c.what).join("; ")}</ErrorNote>}
              <div className="flex gap-2"><Button kind="primary" disabled={busy} onClick={() => void decide(l, "activate")}>Run checks and activate</Button><Button disabled={busy} onClick={() => void decide(l, "reject")}>Reject</Button></div>
            </li>))}</ul>}
        <p className="mt-3 text-xs text-fg3">The checks: it comes from a verified, repaired task; it contains no secret; it does not grant permission or tell the agent to skip a check; it does not repeat an existing entry.</p>
      </Card>

      <Dialog open={!!edit || adding} onClose={() => { setEdit(null); setAdding(false); }} title={edit ? "Correct this entry" : "Add a preference"}
        footer={<><Button onClick={() => { setEdit(null); setAdding(false); }}>Cancel</Button><Button kind="primary" disabled={busy || text.trim().length < 3} onClick={save}>Save</Button></>}>
        <Field label="Text (up to 500 characters)"><textarea rows={3} className={inputCls} value={text} maxLength={500} onChange={(e) => setText(e.target.value)} /></Field>
      </Dialog>
      <Dialog open={!!forget} onClose={() => setForget(null)} title="Forget this entry?"
        footer={<><Button onClick={() => setForget(null)}>Keep it</Button><Button kind="danger" disabled={busy} onClick={doForget}>Forget it</Button></>}>
        <p className="text-sm text-fg2">“{forget?.text}” is erased from the store, including its history. JARVIS will not remember it in new sessions.</p>
      </Dialog>
    </div>
  );
}
