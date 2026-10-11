import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { api, ApiError, owner } from "../lib/api";
import { store, useStore } from "../lib/store";
import { voice } from "../lib/voice";
import { ago, bytes, TERMINAL } from "../lib/format";
import type { Approval, Command, FileRow, Task } from "../lib/types";
import { Badge, Button, Card, Dialog, Empty, ErrorNote, Field, inputCls, stateTone } from "../components/ui";
import { go } from "../App";

const PAGE_LOADED = Date.now() / 1000;
const QUICK = [
  "What are you working on right now?",
  "Open my Downloads folder",
  "Check the jarvis-hq project and tell me if tests pass",
  "Summarize what finished today and what failed",
];

function ApprovalCard({ a, pinSet, onDone }: { a: Approval; pinSet: boolean; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [pin, setPin] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const red = a.level !== "YELLOW_APPROVAL";
  const target = String(a.action.target ?? "").slice(0, 120);
  const approve = async () => {
    setBusy(true); setErr(null);
    try { await owner.post("approve", { id: a.id, confirm, pin }); setOpen(false); store.toast("ok", "Approved. It is single-use and expires in minutes."); onDone(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); setPin(""); }
  };
  const deny = async () => {
    try { await owner.post("deny", { id: a.id }); store.toast("info", "Denied."); onDone(); } catch (e) { store.toast("err", (e as Error).message); }
  };
  return (
    <li className="rounded-lg border border-wait/40 bg-wait-bg/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={red ? "err" : "wait"}>{a.level.replace("_", " ")}</Badge>
        <span className="text-sm font-medium">{a.action.tool}.{a.action.op}</span>
        <span className="num ml-auto text-xs text-fg3">{a.seconds_left}s left</span>
      </div>
      <p className="mt-1 break-words text-sm text-fg2">{target || "(no target)"}{a.action.lane ? ` · ${a.action.lane} lane` : ""}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {red
          ? <p className="text-xs text-fg3">This level is approved at the kernel console only.</p>
          : <Button kind="primary" onClick={() => setOpen(true)} disabled={!pinSet} title={pinSet ? "" : "Set an approval PIN at the kernel console first"}>Approve…</Button>}
        <Button onClick={deny}>Deny</Button>
      </div>
      {!pinSet && !red && <p className="mt-2 text-xs text-fg3">No approval PIN is set, so approving here is disabled. Set one with <code>python3 -m jarvis_kernel set-approval-pin</code> on the server.</p>}
      <Dialog open={open} onClose={() => setOpen(false)} title="Approve this action"
        footer={<><Button onClick={() => setOpen(false)}>Cancel</Button><Button kind="primary" disabled={busy || confirm !== a.id || pin.length < 6} onClick={approve}>Approve once</Button></>}>
        <p className="mb-3 text-sm text-fg2">{a.action.tool}.{a.action.op} on <strong>{target}</strong>. Single use, expires in {a.seconds_left}s.</p>
        <div className="space-y-3">
          <Field label={`Type the approval id to confirm: ${a.id}`}><input className={inputCls} value={confirm} onChange={(e) => setConfirm(e.target.value.trim())} autoComplete="off" /></Field>
          <Field label="Approval PIN"><input className={inputCls} type="password" value={pin} onChange={(e) => setPin(e.target.value)} autoComplete="off" /></Field>
          {err && <ErrorNote>{err}</ErrorNote>}
        </div>
      </Dialog>
    </li>
  );
}

function TaskProgress({ t }: { t: Task }) {
  const now = Date.now() / 1000;
  const total = Math.max(1, t.deadline - t.created);
  const used = Math.min(1, Math.max(0, (now - t.created) / total));
  const verified = t.verification?.passed === true;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={stateTone(t.state)}>{t.state.replace("_", " ")}</Badge>
        {verified && <Badge tone="done">independently verified</Badge>}
        <span className="num text-xs text-fg3">attempt {t.attempts}/{t.max_attempts}</span>
        <button className="ml-auto text-xs text-accent-fg underline" onClick={() => go("tasks")}>Open in Tasks</button>
      </div>
      <p className="text-sm font-medium">{t.goal}</p>
      {t.step && <p className="text-sm text-fg2">Now: {t.step}</p>}
      {!TERMINAL.has(t.state) && (
        <div>
          <div className="h-1.5 overflow-hidden rounded-full bg-bg2" role="progressbar" aria-label="Time budget used" aria-valuenow={Math.round(used * 100)} aria-valuemin={0} aria-valuemax={100}>
            <div className="h-full bg-run" style={{ width: `${used * 100}%` }} />
          </div>
          <p className="mt-1 text-xs text-fg3">Time budget used. Progress here is the task's real state, not an estimate of completion.</p>
        </div>
      )}
      {t.criteria.length > 0 && (
        <ul className="space-y-1 text-sm text-fg2">
          {t.criteria.slice(0, 6).map((c, i) => <li key={i} className="flex gap-2"><span aria-hidden>{verified ? "✓" : "○"}</span><span>{c}</span></li>)}
        </ul>
      )}
      {t.result && <p className="rounded-lg bg-bg2 p-2 text-sm text-fg2">{t.result}</p>}
    </div>
  );
}

export function CommandCenter() {
  const st = useStore((s) => s.owner);
  const conn = useStore((s) => s.conn);
  const prefs = useStore((s) => s.prefs);
  const health = useStore((s) => s.health);
  const vs = useSyncExternalStore(voice.subscribe, voice.snapshot);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [files, setFiles] = useState<FileRow[]>([]);
  const mine = useRef<Set<string>>(new Set());
  const spoken = useRef<Set<string>>(new Set());
  const holdStart = useRef(0);
  const countdown = useRef<number | null>(null);
  const [auto, setAuto] = useState<number | null>(null);
  const [intent, setIntent] = useState<{ category: string; label: string; method: string; ms: number } | null>(null);
  const fromVoice = useRef(false);                 // the text in the box started as speech (it may have been corrected since)

  useEffect(() => {
    api.get<{ delivered: FileRow[] }>("/api/files").then((r) => setFiles(r.delivered.slice(0, 6))).catch(() => setFiles([]));
  }, [st?.tasks.length]);

  const sendText = async (t: string, source: "voice" | "text") => {
    const body = t.trim();
    if (!body) return;
    setSending(true); setErr(null);
    try {
      const r = await owner.post<{ id: string }>("command", { text: body, source });
      mine.current.add(r.id);
      setText(""); fromVoice.current = false; void store.refresh();
    } catch (e) { setErr(e instanceof ApiError ? e.message : "The command could not be sent."); }
    finally { setSending(false); }
  };

  // JARVIS speaks the outcome of commands this page started and of outcomes announced after the page loaded.
  useEffect(() => {
    for (const c of st?.commands ?? []) {
      if (c.state !== "done" || !c.reply || spoken.current.has(c.id)) continue;
      const fresh = mine.current.has(c.id) || (c.ts > PAGE_LOADED && c.source !== "console" && c.source !== "voice");
      spoken.current.add(c.id);
      if (fresh && prefs.speakReplies) void voice.speak(c.reply, prefs.lang);
    }
  }, [st?.commands, prefs.speakReplies, prefs.lang]);

  // An advisory read of what the text is (a small local model, or rules). It never authorizes or runs anything.
  useEffect(() => {
    const t = text.trim();
    if (t.length < 4) { setIntent(null); return; }
    const h = window.setTimeout(() => {
      api.post<{ category: string; label: string; method: string; ms: number }>("/api/classify", { text: t }).then(setIntent).catch(() => setIntent(null));
    }, 500);
    return () => window.clearTimeout(h);
  }, [text]);

  const startTalk = async () => { holdStart.current = performance.now(); if (countdown.current) { clearInterval(countdown.current); countdown.current = null; setAuto(null); } await voice.start(); };
  const stopTalk = async () => {
    const t = await voice.finish(prefs.lang);
    if (t === null) return;
    if (!t.trim()) return;
    fromVoice.current = true;
    setText(t);
    if (prefs.autoSend) {
      let n = 3; setAuto(n);
      countdown.current = window.setInterval(() => {
        n -= 1; setAuto(n);
        if (n <= 0 && countdown.current) { clearInterval(countdown.current); countdown.current = null; setAuto(null); void sendText(t, "voice"); }
      }, 1000);
    }
  };
  const onMicClick = () => { if (vs.mode === "recording") void stopTalk(); else if (vs.mode === "idle" || vs.mode === "speaking") void startTalk(); };
  const onMicUp = () => { if (vs.mode === "recording" && performance.now() - holdStart.current > 700) void stopTalk(); };

  const cancelAuto = () => { if (countdown.current) clearInterval(countdown.current); countdown.current = null; setAuto(null); };

  const active = useMemo(() => (st?.tasks ?? []).filter((t) => !TERMINAL.has(t.state)), [st?.tasks]);
  const latest = active[0] ?? st?.tasks[0];
  const convo: Command[] = [...(st?.commands ?? [])].reverse();
  const mic = vs.mode;
  const micLabel = { idle: "Talk", recording: "Stop", processing: "Working…", speaking: "Interrupt & talk", muted: "Muted" }[mic];
  const sttOk = health?.voice.stt?.[prefs.lang]?.available;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card title="Talk to JARVIS">
          <div className="flex flex-col items-center gap-4 py-2">
            <button aria-label={micLabel} aria-pressed={mic === "recording"} disabled={mic === "muted" || mic === "processing"}
              onPointerDown={() => { if (mic === "idle" || mic === "speaking") void startTalk(); }} onPointerUp={onMicUp}
              onClick={(e) => { if (e.detail === 0) onMicClick(); else if (mic === "recording" && performance.now() - holdStart.current <= 700) void stopTalk(); }}
              className={`grid size-28 place-items-center rounded-full border-2 text-sm font-semibold transition-colors disabled:opacity-50 ${
                mic === "recording" ? "rec-pulse border-err bg-err-bg text-err-fg" : mic === "speaking" ? "speak-pulse border-accent bg-accent-bg text-accent-fg"
                : "border-edge bg-bg2 text-fg hover:border-accent"}`}>
              <span className="flex flex-col items-center gap-1">
                <svg viewBox="0 0 24 24" className="size-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></svg>
                <span>{micLabel}</span>
              </span>
            </button>
            <div className="h-2 w-56 overflow-hidden rounded-full bg-bg2" aria-hidden><div className="h-full bg-err transition-[width] duration-100" style={{ width: `${mic === "recording" ? Math.max(4, vs.level * 100) : 0}%` }} /></div>
            <p className="text-xs text-fg3" aria-live="polite">
              {mic === "recording" ? `Recording… ${vs.seconds.toFixed(1)}s (release or press again to stop, Esc cancels)`
                : mic === "processing" ? "Transcribing on this computer…"
                : mic === "speaking" ? "Speaking. Press the button or Esc to interrupt."
                : mic === "muted" ? "Microphone is muted. Typing works as usual."
                : "Press and hold, or click once to start and again to stop."}
            </p>
            {vs.error && <ErrorNote>{vs.error}</ErrorNote>}
            {health && sttOk === false && <ErrorNote>The {prefs.lang.toUpperCase()} speech model is not available. Typing still works.</ErrorNote>}
            <div className="flex flex-wrap items-center justify-center gap-2">
              <select aria-label="Spoken language" value={prefs.lang} onChange={(e) => store.setPrefs({ lang: e.target.value as "en" | "hi" | "te" })} className={`${inputCls} !w-auto`}>
                <option value="en">English</option><option value="hi">हिन्दी (Hindi)</option><option value="te">తెలుగు (Telugu)</option>
              </select>
              <Button aria-pressed={prefs.micMuted} onClick={() => { const m = !prefs.micMuted; store.setPrefs({ micMuted: m }); voice.setMuted(m); }}>{prefs.micMuted ? "Unmute microphone" : "Mute microphone"}</Button>
              <Button aria-pressed={prefs.speakReplies} onClick={() => { store.setPrefs({ speakReplies: !prefs.speakReplies }); if (prefs.speakReplies) voice.stopSpeaking(); }}>{prefs.speakReplies ? "Spoken replies on" : "Spoken replies off"}</Button>
              {mic === "speaking" && <Button kind="danger" onClick={() => voice.stopSpeaking()}>Stop speaking</Button>}
            </div>
          </div>

          <form className="mt-2 space-y-2" onSubmit={(e) => { e.preventDefault(); cancelAuto(); void sendText(text, fromVoice.current ? "voice" : "text"); }}>
            <Field label={vs.lastTranscript ? `Command. Heard by ${vs.lastTranscript.model} in ${vs.lastTranscript.ms} ms: correct it if needed` : "Command (type or speak)"}>
              <textarea id="command-input" rows={3} value={text} onChange={(e) => { cancelAuto(); if (!e.target.value) fromVoice.current = false; setText(e.target.value); }} className={inputCls}
                placeholder="e.g. open my Downloads folder, or: build a word-count tool in Python and test it"
                onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); cancelAuto(); void sendText(text, "text"); } }} />
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button type="submit" kind="primary" disabled={sending || !text.trim() || conn !== "live" || !st?.voice_command_ready}>{sending ? "Sending…" : "Send"}</Button>
              {auto !== null && <><span className="text-sm text-fg2">Sending in {auto}s</span><Button onClick={cancelAuto}>Cancel</Button></>}
              <label className="ml-auto flex items-center gap-2 text-xs text-fg2"><input type="checkbox" checked={prefs.autoSend} onChange={(e) => store.setPrefs({ autoSend: e.target.checked })} />Send voice commands after 3 s</label>
            </div>
            {intent && (
              <p className="text-xs text-fg3" aria-live="polite">
                Looks like: <Badge tone={intent.category === "blocked" ? "err" : intent.category === "clarify" ? "wait" : "accent"}>{intent.label}</Badge>{" "}
                <span title="Advisory only: the kernel decides what is allowed">({intent.method === "local-llm" ? `local model, ${intent.ms} ms` : "rules"}, advisory)</span>
                {intent.category === "blocked" && <span className="ml-1 text-err-fg">This kind of request is usually refused or needs your approval.</span>}
                {intent.category === "clarify" && <span className="ml-1">JARVIS will probably ask what you mean.</span>}
              </p>)}
            {st && !st.voice_command_ready && <ErrorNote>This kernel has no link to JARVIS's agent (no Hermes API key on the server), so commands cannot be sent yet.</ErrorNote>}
            {err && <ErrorNote>{err}</ErrorNote>}
          </form>
          <div className="mt-3 flex flex-wrap gap-2" aria-label="Quick actions">
            {QUICK.map((q) => <Button key={q} kind="ghost" className="!border-line !text-fg2" onClick={() => { setText(q); document.getElementById("command-input")?.focus(); }}>{q}</Button>)}
          </div>
        </Card>

        <Card title="Conversation">
          {convo.length === 0 ? <Empty title="No commands yet" hint="What you say or type, and JARVIS's answer, appear here. Answers are the agent's own words plus the kernel's record of any task it touched." />
            : <ol className="space-y-3">
              {convo.slice(-8).map((c) => (
                <li key={c.id} className="space-y-1.5">
                  <div className="flex items-center gap-2 text-xs text-fg3"><Badge tone={c.source === "voice" ? "accent" : "idle"}>{c.source}</Badge><span>{ago(c.ts)}</span><Badge tone={stateTone(c.state)}>{c.state}</Badge></div>
                  <p className="rounded-lg bg-accent-bg px-3 py-2 text-sm">{c.text}</p>
                  {c.state === "running" && <p className="px-1 text-sm text-fg3">JARVIS is working…</p>}
                  {c.reply && <div className="rounded-lg border border-line bg-bg2 px-3 py-2"><p className="whitespace-pre-wrap text-sm">{c.reply}</p>
                    <div className="mt-2 flex gap-2"><Button kind="ghost" onClick={() => void voice.speak(c.reply!, prefs.lang)}>Speak this</Button></div></div>}
                  {c.error && <ErrorNote>{c.error}</ErrorNote>}
                </li>
              ))}
            </ol>}
        </Card>
      </div>

      <div className="space-y-4">
        <Card title={`Approvals${st?.approvals.length ? ` (${st.approvals.length})` : ""}`}>
          {!st?.approvals.length ? <Empty title="Nothing is waiting on you" /> :
            <ul className="space-y-2">{st.approvals.map((a) => <ApprovalCard key={a.id} a={a} pinSet={st.pin_set} onDone={() => void store.refresh()} />)}</ul>}
        </Card>

        <Card title={active.length ? `Current task${active.length > 1 ? ` (1 of ${active.length} open)` : ""}` : "Latest task"}>
          {latest ? <TaskProgress t={latest} /> : <Empty title="No tasks yet" hint="When JARVIS takes on work, its real state, attempts and verification show here." />}
        </Card>

        <Card title="Recently delivered files" actions={<Button kind="ghost" onClick={() => go("workspace")}>All files</Button>}>
          {files.length === 0 ? <Empty title="Nothing delivered yet" hint="Only files that passed independent verification are delivered." /> :
            <ul className="divide-y divide-line">{files.map((f) => (
              <li key={f.path} className="flex items-center justify-between gap-3 py-2 text-sm">
                <span className="min-w-0 truncate" title={f.path}>{f.path}</span><span className="num shrink-0 text-xs text-fg3">{bytes(f.bytes)} · {ago(f.modified)}</span>
              </li>))}</ul>}
        </Card>

        <Card title="Agent activity">
          {(st?.jobs ?? []).length === 0 ? <Empty title="No recent jobs" /> :
            <ul className="space-y-1.5">{st!.jobs.slice(0, 6).map((j) => (
              <li key={j.id} className="flex items-center gap-2 text-sm"><Badge tone={stateTone(j.state)}>{j.state}</Badge><span className="truncate">{j.kind}</span><span className="text-fg3">· {j.owner}</span><span className="num ml-auto text-xs text-fg3">{ago(j.created)}</span></li>))}
            </ul>}
        </Card>
      </div>
    </div>
  );
}
