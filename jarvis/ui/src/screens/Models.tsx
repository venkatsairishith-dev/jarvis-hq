import { useEffect, useState } from "react";
import { api, owner } from "../lib/api";
import { useStore } from "../lib/store";
import { ago } from "../lib/format";
import type { ProviderRow } from "../lib/types";
import { Badge, Card, Empty, ErrorNote, Tabs } from "../components/ui";

interface Models {
  providers: ProviderRow[];
  agents: { name: string; lane: string; calls: number; failed_requests: number; last_call: number | null; provider: string | null }[];
  kpis: { model_calls_24h: number; failed_requests_24h: number; success_rate_24h: number | null; est_tokens_in_24h: number;
          measured_tokens_24h?: { prompt: number; completion: number; calls: number }; measured_by_provider_24h?: Record<string, { prompt: number; completion: number; calls: number }> };
  actions_24h: Record<string, number>;
}
interface Skills { active: Record<string, unknown>; staged: { name: string; risk: string | null; source: string | null; license: string | null; rejected: boolean }[] }
interface Local { llm: { url: string; up: boolean; ms: number; models: string[] } }
type Tab = "providers" | "local" | "skills";
interface Route { lane: string; chain: string[]; chosen: string | null; note: string | null; est_tokens: number | null;
  candidates: { provider: string; lane: string; model: string; priority: number; context_limit: number | null; verdict: string; codes: string[]; rank?: number }[]; }

const CODE_TEXT: Record<string, string> = {
  LANE_MISMATCH: "not allowed for this lane", PROVIDER_DISABLED: "disabled in the configuration", PAID_REFUSED: "paid providers are never used",
  KEY_MISSING: "its key file is not on this machine", NO_ENDPOINT: "no endpoint configured", OTHER_MACHINE_ONLY: "exists on another machine only",
  PURPOSE_MISMATCH: "does not serve this purpose", CONTEXT_TOO_SMALL: "the request is larger than its context limit",
  RESTING_AFTER_ERRORS: "resting after recent errors", UNKNOWN_LANE: "unknown lane", OWNER_PREFERRED: "you prefer it", LOCAL_FIRST: "local models first",
};

interface Probe { ok: boolean; provider: string; base_url_host: string; configured_model: string; why?: string; status?: number; models_count?: number;
  model_listed?: boolean; sample_models?: string[]; models_error?: string; headers?: Record<string, string>; ms?: number;
  chat?: { ok: boolean; ms: number; model?: string; usage?: Record<string, number>; error?: string; finish_reason?: string; headers?: Record<string, string> } }

function ProbeButton({ name }: { name: string }) {
  const [r, setR] = useState<Probe | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async (chat: boolean) => {
    setBusy(true); setErr(null);
    try { setR(await owner.post<Probe>("models/probe", { provider: name, chat })); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-1">
      <div className="flex gap-1">
        <button className="rounded-md border border-edge px-2 py-0.5 text-xs hover:bg-hover disabled:opacity-50" disabled={busy} onClick={() => void run(false)} title="Ask the provider for its model list (no tokens used)">Check</button>
        <button className="rounded-md border border-edge px-2 py-0.5 text-xs hover:bg-hover disabled:opacity-50" disabled={busy} onClick={() => void run(true)} title="One tiny real request with a fixed prompt; reports the tokens it used">Test call</button>
      </div>
      {err && <p className="text-xs text-err-fg">{err}</p>}
      {r && (
        <div className="max-w-xs space-y-0.5 text-xs text-fg2" aria-label={`Probe of ${name}`}>
          <p><Badge tone={r.ok ? "done" : "err"}>{r.ok ? "reachable" : "problem"}</Badge> {r.base_url_host}{r.status ? ` · HTTP ${r.status}` : ""}{r.ms ? ` · ${r.ms} ms` : ""}</p>
          {r.why && <p>{r.why}</p>}
          {r.models_error && <p className="text-err-fg">model list: {r.models_error}</p>}
          {r.models_count !== undefined && <p>{r.models_count} models; configured “{r.configured_model}” {r.model_listed ? "is listed" : "is NOT in the list"}</p>}
          {r.headers && Object.keys(r.headers).length > 0 && <p className="font-mono">{Object.entries(r.headers).map(([k, v]) => `${k}: ${v}`).join(" · ")}</p>}
          {r.chat && <p>{r.chat.ok ? `test call ok in ${r.chat.ms} ms, model ${r.chat.model}, tokens ${JSON.stringify(r.chat.usage)}` : `test call failed: ${r.chat.error}`}</p>}
        </div>)}
    </div>
  );
}

function Why() {
  const [lane, setLane] = useState("private");
  const [tokens, setTokens] = useState("2000");
  const [r, setR] = useState<Route | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { owner.get<Route>(`route?lane=${lane}&tokens=${encodeURIComponent(tokens)}`).then((x) => { setR(x); setErr(null); }).catch((e: Error) => setErr(e.message)); }, [lane, tokens]);
  return (
    <Card title="Why this model? (routing explanation)">
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <label className="text-xs font-medium text-fg2">Lane<select className="mt-1 block rounded-lg border border-input bg-bg2 px-3 py-2 text-sm text-fg" value={lane} onChange={(e) => setLane(e.target.value)}><option value="private">private</option><option value="public">public (lab)</option></select></label>
        <label className="text-xs font-medium text-fg2">Request size (tokens)<input className="mt-1 block w-32 rounded-lg border border-input bg-bg2 px-3 py-2 text-sm text-fg" value={tokens} inputMode="numeric" onChange={(e) => setTokens(e.target.value.replace(/\D/g, ""))} /></label>
      </div>
      {err && <ErrorNote>{err}</ErrorNote>}
      {r && <div className="space-y-2 text-sm">
        <div>{r.chosen ? <p>First choice: <strong>{r.chosen}</strong>{r.chain.length > 1 ? `, then ${r.chain.slice(1).join(", ")}` : ""}.</p> : <ErrorNote>{r.note}</ErrorNote>}</div>
        <ul className="divide-y divide-line">{r.candidates.map((c) => (
          <li key={c.provider} className="flex flex-wrap items-center gap-2 py-1.5">
            <Badge tone={c.verdict === "eligible" ? "done" : "idle"}>{c.verdict === "eligible" ? `#${c.rank}` : "excluded"}</Badge><span className="font-medium">{c.provider}</span><span className="text-xs text-fg3">{c.lane} · {c.model}</span>
            <span className="ml-auto text-xs text-fg2">{c.codes.map((x) => CODE_TEXT[x] ?? (x.startsWith("PRIORITY_") ? `priority ${x.slice(9)}` : x)).join("; ")}</span></li>))}</ul>
        <p className="text-xs text-fg3">The same rules the gateway applies on every call: a request never crosses lanes, paid providers are refused, and a local classifier or a model can suggest but never authorizes.</p>
      </div>}
    </Card>
  );
}

interface LaneState { chain: string[]; preferred: string | null }
interface Lanes { lanes: Record<string, LaneState> }

function Switcher() {
  const [s, setS] = useState<Lanes | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [phrase, setPhrase] = useState("");
  const load = () => owner.get<Lanes>("models/lanes").then((x) => { setS(x); setErr(null); }).catch((e: Error) => setErr(e.message));
  useEffect(() => { void load(); }, []);
  const pick = async (lane: string, provider: string) => {
    setMsg(null); setErr(null);
    try { await owner.post("models/switch", { lane, provider }); setMsg(provider === "auto" ? `The ${lane} lane is back to automatic.` : `The ${lane} lane now asks ${provider} first.`); await load(); } catch (e) { setErr((e as Error).message); }
  };
  const say = async () => {
    setMsg(null); setErr(null);
    try { const r = await api.post<{ handled: boolean; say?: string }>("/api/providers/command", { text: phrase }); setMsg(r.handled ? (r.say ?? "Done.") : "That is not a provider command. Try “switch to groq” or “back to automatic”."); if (r.handled) await load(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <Card title="Which provider to ask first">
      {s && <div className="space-y-3 text-sm">
        {Object.entries(s.lanes).map(([lane, st]) => (
          <div key={lane} className="flex flex-wrap items-center gap-2">
            <Badge tone={lane === "private" ? "done" : "wait"}>{lane}</Badge>
            <label className="sr-only" htmlFor={`sw-${lane}`}>Provider for the {lane} lane</label>
            <select id={`sw-${lane}`} className="rounded-lg border border-input bg-bg2 px-3 py-1.5 text-sm text-fg" value={st.preferred ?? "auto"} onChange={(e) => void pick(lane, e.target.value)}>
              <option value="auto">automatic</option>{st.chain.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
            <span className="text-xs text-fg3">order: {st.chain.join(", ") || "none"}</span>
          </div>))}
        <form className="flex flex-wrap items-center gap-2" onSubmit={(e) => { e.preventDefault(); if (phrase.trim()) void say(); }}>
          <label className="sr-only" htmlFor="sw-phrase">Say or type a provider command</label>
          <input id="sw-phrase" className="min-w-0 flex-1 rounded-lg border border-input bg-bg2 px-3 py-1.5 text-sm text-fg" placeholder="switch to groq / back to automatic" value={phrase} onChange={(e) => setPhrase(e.target.value)} />
          <button className="rounded-md border border-edge px-3 py-1.5 text-sm hover:bg-hover disabled:opacity-50" disabled={!phrase.trim()}>Send</button>
        </form>
      </div>}
      {msg && <p className="mt-2 text-sm" role="status">{msg}</p>}
      {err && <ErrorNote>{err}</ErrorNote>}
      <p className="mt-3 text-xs text-fg3">Your choice only reorders providers inside a lane the kernel already allows. It never crosses lanes, never enables a paid provider, and is written to the audit log.</p>
    </Card>
  );
}

export function Models() {
  const health = useStore((s) => s.health);
  const [tab, setTab] = useState<Tab>("providers");
  const [m, setM] = useState<Models | null>(null);
  const [sk, setSk] = useState<Skills | null>(null);
  const [lm, setLm] = useState<Local | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    owner.get<Models>("models").then(setM).catch((e: Error) => setErr(e.message));
    api.get<Skills>("/api/skills").then(setSk).catch(() => setSk(null));
    api.get<Local>("/api/local-models").then(setLm).catch(() => setLm(null));
  }, []);
  const voice = health?.voice;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Models, tools and skills</h1>
        <Tabs value={tab} onChange={setTab} items={[{ id: "providers", label: "Providers & routing" }, { id: "local", label: "Local models" }, { id: "skills", label: "Skills", count: sk?.staged.length }]} />
      </div>
      {err && <ErrorNote>{err}</ErrorNote>}
      {tab === "providers" && (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Card><p className="text-xs text-fg3">Model calls (24 h)</p><p className="num text-2xl font-semibold">{m?.kpis.model_calls_24h ?? "…"}</p></Card>
            <Card><p className="text-xs text-fg3">Success rate (24 h)</p><p className="num text-2xl font-semibold">{m?.kpis.success_rate_24h != null ? `${m.kpis.success_rate_24h}%` : "n/a"}</p></Card>
            <Card><p className="text-xs text-fg3">Tokens (24 h): reported by providers / estimated from characters</p>
              <p className="num text-2xl font-semibold">{m?.kpis.measured_tokens_24h ? (m.kpis.measured_tokens_24h.prompt + m.kpis.measured_tokens_24h.completion).toLocaleString() : "0"}
                <span className="text-sm font-normal text-fg3"> / ~{m ? m.kpis.est_tokens_in_24h.toLocaleString() : "…"} in</span></p>
              <p className="text-xs text-fg3">{m?.kpis.measured_tokens_24h?.calls ?? 0} of {m?.kpis.model_calls_24h ?? 0} calls reported usage</p></Card>
          </div>
          <Card title="Providers the kernel may use">
            {!m ? null : m.providers.length === 0 ? <Empty title="No enabled provider" /> :
              <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead className="text-xs text-fg3"><tr><th className="py-2 pr-3">Provider</th><th className="pr-3">Lane</th><th className="pr-3">Model</th><th className="pr-3">Status</th><th className="pr-3">ok / failed</th><th className="pr-3">Context limit</th><th className="pr-3">Last</th><th>Probe</th></tr></thead>
                <tbody>{m.providers.map((p) => (
                  <tr key={p.name} className="border-t border-line"><td className="py-2 pr-3 font-medium">{p.name}{p.preferred && <Badge tone="accent"> preferred</Badge>}</td>
                    <td className="pr-3"><Badge tone={p.lane === "private" ? "done" : p.lane === "public" ? "wait" : "idle"}>{p.lane}</Badge></td><td className="pr-3 font-mono text-xs">{p.model}</td>
                    <td className="pr-3"><Badge tone={p.available ? "done" : "err"}>{p.available ? "available" : "unavailable"}</Badge></td>
                    <td className="num pr-3">{p.ok ?? 0} / {p.failed ?? 0}</td>
                    <td className="num pr-3">{String(p.limits?.max_total_tokens ?? p.limits?.max_input_tokens ?? "n/a")}</td>
                    <td className="pr-3 text-xs text-fg3">{p.last_ok ? `ok ${ago(p.last_ok)}` : ""}{p.last_error ? ` · ${p.last_error}` : ""}
                      {m.kpis.measured_by_provider_24h?.[p.name] && <span className="block">{(m.kpis.measured_by_provider_24h[p.name].prompt + m.kpis.measured_by_provider_24h[p.name].completion).toLocaleString()} tokens reported (24 h)</span>}</td>
                    <td><ProbeButton name={p.name} /></td></tr>))}</tbody></table></div>}
            <p className="mt-3 text-xs text-fg3">Routing is decided by the kernel's gateway: the lane comes from each agent's token, and a request never crosses lanes. Adding or removing a provider is a configuration change on the server (<code>config/providers.json</code>); choosing which one is asked first is the control below.</p>
          </Card>
          <Switcher />
          <Why />
          <Card title="Who used which model (24 h)">
            {!m || m.agents.length === 0 ? <Empty title="No model calls recorded" /> : <ul className="divide-y divide-line">{m.agents.map((a) => (
              <li key={a.name} className="flex flex-wrap items-center gap-2 py-2 text-sm"><span className="font-medium">{a.name}</span><Badge>{a.lane ?? "?"}</Badge><span className="text-fg3">via {a.provider ?? "?"}</span>
                <span className="num ml-auto text-xs text-fg3">{a.calls} calls · {a.failed_requests} failed · {ago(a.last_call)}</span></li>))}</ul>}
          </Card>
        </>
      )}
      {tab === "local" && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Local language model server">
            {!lm ? <p className="text-sm text-fg3">Loading…</p> : <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2"><Badge tone={lm.llm.up ? "done" : "err"}>{lm.llm.up ? "running" : "not running"}</Badge><span className="font-mono text-xs text-fg3">{lm.llm.url}</span><span className="num ml-auto text-xs text-fg3">{lm.llm.ms} ms</span></div>
              {lm.llm.models.length > 0 ? <ul className="list-disc pl-5 font-mono text-xs">{lm.llm.models.map((x) => <li key={x}>{x}</li>)}</ul> : <p className="text-fg3">No model reported. Start it with <code>scripts/start-local-llm.ps1</code> (Vulkan, flash attention off on this GPU).</p>}
            </div>}
          </Card>
          <Card title="Speech models (run on this CPU; audio never leaves the laptop)">
            {!voice ? <p className="text-sm text-fg3">Loading…</p> : <div className="space-y-3 text-sm">
              <div><p className="mb-1 text-xs font-medium text-fg2">Speech to text</p><ul className="space-y-1">{Object.entries(voice.stt).map(([l, v]) => <li key={l} className="flex items-center gap-2"><Badge tone={v.available ? "done" : "err"}>{l}</Badge><span className="font-mono text-xs">{v.model}</span></li>)}</ul></div>
              <div><p className="mb-1 text-xs font-medium text-fg2">Text to speech</p><ul className="space-y-1">{Object.entries(voice.tts).map(([l, v]) => <li key={l} className="flex items-center gap-2"><Badge tone={v.available ? "done" : "err"}>{l}</Badge><span className="font-mono text-xs">{v.model}</span></li>)}</ul></div>
              <p className="text-xs text-fg3">Resident now: {voice.resident_stt ? `STT ${voice.resident_stt}` : "no STT model"}{voice.resident_tts ? ", TTS" : ""}. One speech model is loaded at a time and unloaded after {voice.idle_unload_s}s idle. Last STT {voice.stats.last_stt_ms ?? "n/a"} ms, last TTS {voice.stats.last_tts_ms ?? "n/a"} ms.</p>
            </div>}
          </Card>
        </div>
      )}
      {tab === "skills" && (
        <Card title="Skills">
          {!sk ? <p className="text-sm text-fg3">Loading…</p> : <div className="space-y-3 text-sm">
            <p className="text-fg2">Active: {Object.keys(sk.active).length ? Object.keys(sk.active).join(", ") : "none"}. A skill becomes active only after review, a smoke test and a kernel authorization (the skill gate); the importer accepting a skill never activates it.</p>
            {sk.staged.length === 0 ? <Empty title="No staged skills" /> : <ul className="divide-y divide-line">{sk.staged.map((s) => (
              <li key={s.name} className="flex flex-wrap items-center gap-2 py-2"><span className="font-medium">{s.name}</span>
                <Badge tone={s.rejected ? "err" : s.risk === "low" ? "done" : s.risk === "medium" ? "wait" : "err"}>{s.rejected ? "rejected" : `risk ${s.risk ?? "unknown"}`}</Badge>
                {s.license && <span className="text-xs text-fg3">{s.license}</span>}
                <Badge tone={s.name in sk.active ? "done" : "idle"}>{s.name in sk.active ? "active" : "staged"}</Badge></li>))}</ul>}
            <p className="text-xs text-fg3">Activating a skill is done with the skill gate on the laptop and, for medium risk, an approval at the kernel console.</p>
          </div>}
        </Card>
      )}
    </div>
  );
}
