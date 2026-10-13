import { useEffect, useState, useSyncExternalStore } from "react";
import { api, owner } from "../lib/api";
import { store, useStore } from "../lib/store";
import { voice } from "../lib/voice";
import { Badge, Button, Card, ErrorNote } from "../components/ui";
import type { AuditEvent } from "../lib/types";

interface Cfg { kernel_url: string; hub_url: string; port: number; owner_token_file_present: boolean; outputs_dir: string; voice_dir: string; voice_threads: number; voice_idle_unload_s: number; local_llm_url: string }


interface CheckRow { lang: "en" | "hi" | "te"; expected: string; heard: string; wer: number | null; cer: number | null; exact: boolean; ms: number | null; speech: boolean; model: string }
interface CheckSummary { [lang: string]: { phrases: number; mean_wer: number; mean_cer: number; exact: number; median_ms: number | null; no_speech: number } }

function VoiceCheck() {
  const vs = useSyncExternalStore(voice.subscribe, voice.snapshot);
  const [phrases, setPhrases] = useState<Record<string, string[]>>({});
  const [history, setHistory] = useState<{ file: string; at: number; device: string; n: number; summary: CheckSummary }[]>([]);
  const [lang, setLang] = useState<"en" | "hi" | "te">("en");
  const [idx, setIdx] = useState(0);
  const [rows, setRows] = useState<CheckRow[]>([]);
  const [pending, setPending] = useState<CheckRow | null>(null);
  const [device, setDevice] = useState("");
  const [saved, setSaved] = useState<CheckSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { api.get<{ phrases: Record<string, string[]>; results: typeof history }>("/api/voice/check/phrases").then((r) => { setPhrases(r.phrases); setHistory(r.results); }).catch(() => undefined); }, [saved]);
  const list = phrases[lang] ?? [];
  const done = new Set(rows.filter((r) => r.lang === lang).map((r) => r.expected));

  const record = async () => {
    setErr(null); setPending(null);
    if (!device) {
      try {
        const ds = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
        setDevice(ds[0]?.label || "default microphone");
      } catch { /* the label is optional */ }
    }
    await voice.start();
  };
  const stop = async () => {
    const heard = await voice.finish(lang);
    if (heard === null) return;
    const t = voice.snapshot().lastTranscript;
    const expected = list[idx] ?? "";
    try {
      const sc = await api.post<{ wer: number | null; cer: number | null; exact: boolean }>("/api/voice/check/score", { expected, heard });
      setPending({ lang, expected, heard, wer: sc.wer, cer: sc.cer, exact: sc.exact, ms: t?.ms ?? null, speech: t?.speech ?? true, model: t?.model ?? "" });
    } catch (e) { setErr((e as Error).message); }
  };
  const accept = () => { if (pending) { setRows((r) => [...r, pending]); setPending(null); setIdx((i) => Math.min(i + 1, Math.max(0, list.length - 1))); } };
  const save = async () => {
    try { const r = await api.post<{ saved: string; summary: CheckSummary }>("/api/voice/check/save", { rows, device }); setSaved(r.summary); setRows([]); store.toast("ok", `Saved ${r.saved} on this computer.`); }
    catch (e) { setErr((e as Error).message); }
  };

  return (
    <Card title="Voice check (your real microphone)">
      <div className="space-y-3 text-sm">
        <p className="text-fg2">Say each phrase naturally. Everything runs on this computer; only the text and scores are saved (not the audio). The phrases are a draft: edit <span className="font-mono">console/jarvis_console/voice_phrases.json</span> to what you would really say.</p>
        <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Language to check" className="rounded-lg border border-input bg-bg2 px-3 py-2 text-sm text-fg" value={lang} onChange={(e) => { setLang(e.target.value as "en" | "hi" | "te"); setIdx(0); setPending(null); }}>
            <option value="en">English</option><option value="hi">Hindi</option><option value="te">Telugu</option></select>
          <span className="text-xs text-fg3">{done.size}/{list.length} phrases this session · device: {device || "not yet detected"}</span>
        </div>
        {list.length === 0 ? <p className="text-fg3">No phrases for this language.</p> : (
          <div className="space-y-2 rounded-lg border border-line p-3">
            <p className="text-xs text-fg3">Phrase {idx + 1} of {list.length}</p>
            <p lang={lang} className="text-lg font-medium" aria-label="Phrase to say">{list[idx]}</p>
            <div className="flex flex-wrap gap-2">
              {vs.mode === "recording" ? <Button kind="danger" onClick={() => void stop()}>Stop and score</Button>
                : <Button kind="primary" disabled={vs.mode === "processing" || vs.mode === "muted"} onClick={() => void record()}>Record this phrase</Button>}
              <Button disabled={idx === 0} onClick={() => { setIdx(idx - 1); setPending(null); }}>Previous</Button>
              <Button disabled={idx >= list.length - 1} onClick={() => { setIdx(idx + 1); setPending(null); }}>Skip</Button>
            </div>
            {vs.mode === "recording" && <p className="text-xs text-err-fg">Recording… {vs.seconds.toFixed(1)}s</p>}
            {vs.error && <ErrorNote>{vs.error}</ErrorNote>}
            {pending && (
              <div className="space-y-1 rounded-lg bg-bg2 p-2">
                <p>Heard: <span lang={lang} className="font-medium">{pending.heard || "(nothing recognised)"}</span></p>
                <p className="text-xs text-fg2">word error {pending.wer === null ? "n/a" : `${Math.round(pending.wer * 100)}%`} · character error {pending.cer === null ? "n/a" : `${Math.round(pending.cer * 100)}%`} · {pending.exact ? "exact match" : "not exact"} · {pending.ms} ms · {pending.model}</p>
                <div className="flex gap-2"><Button onClick={accept}>Keep this result</Button><Button onClick={() => setPending(null)}>Discard and redo</Button></div>
              </div>)}
          </div>)}
        {err && <ErrorNote>{err}</ErrorNote>}
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={rows.length === 0} onClick={() => void save()}>Save {rows.length} result(s)</Button>
          {saved && <span className="text-xs text-fg2">{Object.entries(saved).map(([l, s]) => `${l}: ${s.phrases} phrases, mean word error ${Math.round(s.mean_wer * 100)}%, ${s.exact} exact, median ${s.median_ms} ms`).join(" · ")}</span>}
        </div>
        {history.length > 0 && (
          <details><summary className="cursor-pointer text-xs font-medium text-fg2">Saved sessions ({history.length})</summary>
            <ul className="mt-1 space-y-1 text-xs text-fg2">{history.map((h) => <li key={h.file}><span className="font-mono">{h.file}</span> · {h.n} phrases · {h.device} · {Object.entries(h.summary || {}).map(([l, s]) => `${l} WER ${Math.round(s.mean_wer * 100)}%`).join(", ")}</li>)}</ul></details>)}
      </div>
    </Card>
  );
}

export function Settings() {
  const health = useStore((s) => s.health);
  const prefs = useStore((s) => s.prefs);
  const events = useStore((s) => s.events);
  const conn = useStore((s) => s.conn);
  const [cfg, setCfg] = useState<Cfg | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[] | null>(null);
  const [verify, setVerify] = useState<{ ok: boolean; message: string } | null>(null);
  const [derr, setDerr] = useState<string | null>(null);

  useEffect(() => { api.get<Cfg>("/api/config").then(setCfg).catch(() => setCfg(null)); void store.refreshHealth(); }, []);
  const listDevices = async () => {
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => s.getTracks().forEach((t) => t.stop()));   // labels need a permission grant
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(all.filter((d) => d.kind === "audioinput")); setDerr(null);
    } catch (e) { setDerr((e as DOMException).name === "NotAllowedError" ? "Microphone permission is denied for this page." : `Could not list devices (${(e as Error).name}).`); }
  };
  const row = (label: string, ok: boolean | null | undefined, detail: string) => (
    <li className="flex items-center gap-3 py-2 text-sm"><Badge tone={ok ? "done" : ok === false ? "err" : "idle"}>{ok ? "OK" : ok === false ? "Down" : "Unknown"}</Badge><span className="font-medium">{label}</span><span className="ml-auto truncate text-xs text-fg3">{detail}</span></li>
  );
  const errs = events.filter((e: AuditEvent) => /fail|refus|error|stuck|bad_pin|killed|kill/.test(e.kind)).slice(0, 12);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Settings and system health</h1>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Connections" actions={<Button onClick={() => void store.refreshHealth()}>Check now</Button>}>
          <ul className="divide-y divide-line">
            {row("Console (this app's server)", health?.console.ok, health ? `up ${Math.round(health.console.uptime_s / 60)} min` : "no answer")}
            {row("Kernel owner plane", health?.kernel.authorized, health ? (health.kernel.error ?? `${health.kernel.ms ?? "?"} ms${health.kernel.killed ? " · KILL SWITCH ON" : ""}`) : "no data")}
            {row("JARVIS's agent link (Hermes)", health?.kernel.voice_command_ready, health?.kernel.voice_command_ready ? "commands can be sent" : "no Hermes key on the server")}
            {row("Approval PIN", health?.kernel.pin_set, health?.kernel.pin_set ? "set (browser approvals enabled for YELLOW)" : "not set: approve at the kernel console")}
            {row("Local language model", health?.local_llm.ok, health ? `${health.local_llm.url} · ${health.local_llm.ms} ms` : "")}
            {row("Hub / Agent PC control", health?.hub.ok, health ? `${health.hub.url} · ${health.hub.ms} ms` : "")}
            {row("Disk (D:)", health?.disk ? health.disk.free_gb > 5 : null, health?.disk ? `${health.disk.free_gb} GB free of ${health.disk.total_gb}` : "unknown")}
          </ul>
          {conn !== "live" && <div className="mt-3"><ErrorNote>The console cannot reach the kernel right now ({conn.replace("-", " ")}). Voice and file features still work locally.</ErrorNote></div>}
        </Card>

        <Card title="Voice">
          <div className="space-y-3 text-sm">
            <div className="grid gap-2 sm:grid-cols-2">
              <label className="text-xs font-medium text-fg2">Spoken language
                <select className="mt-1 w-full rounded-lg border border-input bg-bg2 px-3 py-2 text-sm text-fg" value={prefs.lang} onChange={(e) => store.setPrefs({ lang: e.target.value as "en" | "hi" | "te" })}>
                  <option value="en">English</option><option value="hi">Hindi</option><option value="te">Telugu</option></select></label>
              <label className="flex items-center gap-2 pt-5 text-sm"><input type="checkbox" checked={prefs.speakReplies} onChange={(e) => store.setPrefs({ speakReplies: e.target.checked })} />Speak replies</label>
            </div>
            <div><Button onClick={listDevices}>List microphones</Button>
              {derr && <div className="mt-2"><ErrorNote>{derr}</ErrorNote></div>}
              {devices && <ul className="mt-2 list-disc pl-5 text-xs text-fg2">{devices.length === 0 ? <li>No microphone found.</li> : devices.map((d) => <li key={d.deviceId}>{d.label || "Microphone"}</li>)}</ul>}
              <p className="mt-2 text-xs text-fg3">The browser picks the system default microphone. The recording indicator is on only while you are recording.</p></div>
            {health?.voice && <ul className="space-y-1 text-xs text-fg2">
              <li>Engine: {health.voice.engine ?? "not installed"} · VAD {health.voice.vad ? "on" : "missing"} · {health.voice.threads} threads · unload after {health.voice.idle_unload_s}s idle</li>
              <li>Audio leaves this computer: <strong>{health.voice.audio_leaves_machine ? "yes" : "no"}</strong></li>
              {health.voice.last_error && <li className="text-err-fg">Last error: {health.voice.last_error}</li>}
            </ul>}
          </div>
        </Card>

        <div className="lg:col-span-2"><VoiceCheck /></div>

        <Card title="Where things live (no secrets shown)">
          {!cfg ? <p className="text-sm text-fg3">Loading…</p> : <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
            <dt className="text-fg3">Kernel</dt><dd className="truncate font-mono text-xs">{cfg.kernel_url}</dd>
            <dt className="text-fg3">Owner token</dt><dd>{cfg.owner_token_file_present ? "present on this computer (never sent to the browser)" : "missing"}</dd>
            <dt className="text-fg3">Hub</dt><dd className="truncate font-mono text-xs">{cfg.hub_url}</dd>
            <dt className="text-fg3">Delivered files</dt><dd className="truncate font-mono text-xs">{cfg.outputs_dir}</dd>
            <dt className="text-fg3">Voice models</dt><dd className="truncate font-mono text-xs">{cfg.voice_dir}</dd>
            <dt className="text-fg3">Console port</dt><dd className="num">{cfg.port} (loopback only)</dd></dl>}
        </Card>

        <Card title="Audit chain and recent problems">
          <div className="space-y-3 text-sm">
            <div className="flex items-center gap-2"><Button onClick={async () => { try { setVerify(await owner.get("audit/verify")); } catch (e) { setVerify({ ok: false, message: (e as Error).message }); } }}>Verify the audit chain</Button>
              {verify && <Badge tone={verify.ok ? "done" : "err"}>{verify.message}</Badge>}</div>
            {errs.length === 0 ? <p className="text-fg3">No failures or refusals in this session's event feed.</p> :
              <ul className="space-y-1 font-mono text-xs">{errs.map((e) => <li key={e.seq}><span className="text-fg3">#{e.seq}</span> {e.kind} {JSON.stringify(e.payload).slice(0, 100)}</li>)}</ul>}
          </div>
        </Card>
      </div>
    </div>
  );
}
