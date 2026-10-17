import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { api } from "../lib/api";
import {
  accuracy, countsText, DIM_NAMES, evidenceText, labelTone, percent, riskTone, runLabel, sampleText, scoreText, sizeText, statusTone,
  type Candidate, type CaseRow, type Challenge, type Dimension, type Evidence, type JobView, type ModelResults, type Summary,
} from "../lib/bench";
import { Badge, Button, Card, Empty, ErrorNote, Spinner, inputCls } from "../components/ui";

type Tab = "dashboard" | "run" | "challenge" | "models" | "security";
const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" }, { id: "run", label: "Run" }, { id: "challenge", label: "Challenge" },
  { id: "models", label: "Models" }, { id: "security", label: "Security" },
];
const BIG = "min-h-11";                     // 44 px touch targets on a phone

function useSubTab(): [Tab, (t: Tab) => void] {
  const get = () => (window.location.hash.replace(/^#\/?/, "").split("/")[1] as Tab) || "dashboard";
  const tab = useSyncExternalStore((f) => { window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f); }, get);
  return [TABS.some((t) => t.id === tab) ? tab : "dashboard", (t) => { window.location.hash = `/bench/${t}`; }];
}

const EVIDENCE_HELP: Record<Evidence, string> = {
  integration: "the real code against local fixtures",
  mocked: "the real code with one boundary (usually the local model's answer) replaced by a recorded or hand-written stand-in",
  live: "real services, recorded in results/live-*.json",
};

function EvidenceTag({ kind }: { kind: Evidence }) {
  return <Badge tone={kind === "live" ? "accent" : kind === "mocked" ? "wait" : "idle"} title={EVIDENCE_HELP[kind]}>{kind}</Badge>;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return <div className="space-y-2"><h3 className="text-xs font-semibold uppercase tracking-wide text-fg3">{title}</h3>{children}</div>;
}

// ------------------------------------------------------------------ shared: cases and jobs
function CaseItem({ c }: { c: CaseRow }) {
  return (
    <li className="space-y-1 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={statusTone(c.status)}>{c.status}</Badge>
        <span className="font-mono text-xs">{c.id}</span>
        <EvidenceTag kind={c.evidence} />
        {c.risk && <span className="text-xs text-fg3">{c.risk} risk</span>}
      </div>
      <p className="text-sm">{c.title}</p>
      <details className="text-sm">
        <summary className={`flex ${BIG} cursor-pointer items-center text-accent-fg`}>What was observed</summary>
        <div className="space-y-1 break-words pb-1 text-xs text-fg2">
          <p><span className="font-medium text-fg">Observed:</span> {c.observed || "—"}</p>
          {c.scenario && <p><span className="font-medium text-fg">Scenario:</span> {c.scenario}</p>}
          {c.expected && <p><span className="font-medium text-fg">Expected:</span> {c.expected}</p>}
          {c.run && <p className="font-mono">{c.run}</p>}
        </div>
      </details>
    </li>
  );
}

function useJob(initial: JobView | null) {
  const [job, setJob] = useState<JobView | null>(initial);
  const timer = useRef<number | null>(null);
  const stop = () => { if (timer.current) { window.clearInterval(timer.current); timer.current = null; } };
  const follow = useCallback((j: JobView) => {
    stop(); setJob(j);
    if (j.state !== "running") return;
    timer.current = window.setInterval(async () => {
      try {
        const x = await api.get<JobView>(`/api/bench/jobs/${j.id}`);
        setJob(x);
        if (x.state !== "running") stop();
      } catch { stop(); }
    }, 1000);
  }, []);
  useEffect(() => stop, []);
  return { job, follow };
}

function JobPanel({ job, onChange }: { job: JobView; onChange: (j: JobView) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pct = percent(job.done, job.total);
  const cancel = async () => {
    setBusy(true); setErr(null);
    try { onChange(await api.post<JobView>(`/api/bench/jobs/${job.id}/cancel`)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  return (
    <section aria-label="Benchmark job" className="space-y-3 rounded-xl border border-line bg-bg2 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={statusTone(job.state)}>{job.state}</Badge>
        <span className="text-sm font-medium">{job.title}</span>
      </div>
      <div>
        <div role="progressbar" aria-label="Cases done" aria-valuemin={0} aria-valuemax={job.total || 0} aria-valuenow={job.done}
          className="h-3 overflow-hidden rounded-full bg-surface"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>
        <p className="num mt-1 text-sm" aria-live="polite">{job.done} of {job.total} cases done{job.state === "running" && job.current ? ` · now ${job.current}` : ""}</p>
        <p className="num text-xs text-fg2">{countsText(job.counts)}{job.steps > 1 ? ` · step ${job.step} of ${job.steps}` : ""}</p>
      </div>
      {job.state === "running" && <Button kind="danger" className={`${BIG} w-full sm:w-auto`} disabled={busy} onClick={() => void cancel()}>Cancel job</Button>}
      {err && <ErrorNote>{err}</ErrorNote>}
      {job.error && <ErrorNote>{job.error}</ErrorNote>}
      {job.saved.length > 0 && <p className="break-all text-xs text-fg2">Saved: <span className="font-mono">{job.saved.join(", ")}</span></p>}
      <details open={job.state === "running"}>
        <summary className={`flex ${BIG} cursor-pointer items-center text-sm text-accent-fg`}>Log tail ({job.log_tail.length} lines)</summary>
        <pre aria-label="Log tail" tabIndex={0} className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface p-2 font-mono text-[11px] text-fg2">{job.log_tail.join("\n") || "(nothing yet)"}</pre>
      </details>
    </section>
  );
}

// ------------------------------------------------------------------ dashboard
function DimensionRow({ name, d }: { name: string; d: Dimension }) {
  const [open, setOpen] = useState(false);
  const [cases, setCases] = useState<CaseRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const toggle = async () => {
    setOpen((o) => !o);
    if (!cases) {
      try { setCases((await api.get<{ cases: CaseRow[] }>(`/api/bench/cases?dim=${encodeURIComponent(name)}`)).cases); } catch (e) { setErr((e as Error).message); }
    }
  };
  return (
    <li className="py-1">
      <button type="button" aria-expanded={open} onClick={() => void toggle()}
        className={`flex ${BIG} w-full flex-col items-stretch gap-1 rounded-lg px-2 py-2 text-left hover:bg-hover`}>
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{DIM_NAMES[name] ?? name}</span>
          <span className="num ml-auto font-semibold">{scoreText(d)}</span>
          <Badge tone={labelTone(d.label)}>{d.label}</Badge>
        </span>
        <span className="num text-xs text-fg2">{sampleText(d)}</span>
        <span className="text-xs text-fg3">Evidence: {evidenceText(d.evidence)}</span>
        {d.why.length > 0 && <span className="text-xs text-wait-fg">Why not 10: {d.why.join("; ")}{d.failed.length ? ` (${d.failed.join(", ")})` : ""}{d.deferred_critical.length ? ` (${d.deferred_critical.join(", ")})` : ""}</span>}
        <span className="text-xs text-accent-fg">{open ? "Hide" : "Show"} the {d.case_ids.length} supporting cases</span>
      </button>
      {open && (
        <div className="px-2">
          {err && <ErrorNote>{err}</ErrorNote>}
          {!cases && !err && <Spinner label="Loading cases" />}
          {cases && <ul className="divide-y divide-line" aria-label={`Cases for ${DIM_NAMES[name] ?? name}`}>{cases.map((c) => <CaseItem key={c.id} c={c} />)}</ul>}
        </div>
      )}
    </li>
  );
}

function Dashboard({ s }: { s: Summary }) {
  const v1 = s.official_v1;
  return (
    <div className="space-y-4">
      <Card title={`Benchmark v${s.bench_version ?? "?"}`}>
        {s.latest ? (
          <div className="space-y-2 text-sm">
            <p>Newest full run: <strong>{s.latest.time}</strong> <span className="text-fg3">({s.latest.cases} cases)</span></p>
            <div className="flex flex-wrap gap-2">
              <Badge tone="done">{s.latest.counts.pass} passed</Badge>
              <Badge tone={s.latest.counts.fail ? "err" : "idle"}>{s.latest.counts.fail} failed</Badge>
              <Badge tone={s.latest.counts.error ? "err" : "idle"}>{s.latest.counts.error} errors</Badge>
              <Badge tone="wait">{s.latest.counts.deferred} deferred</Badge>
            </div>
            <p className="text-fg2">Mean of the {s.latest.scored_dimensions} scored dimensions: <strong className="num">{s.latest.mean_of_scored ?? "—"}</strong> (rubric v{s.rubric_version}; not an overall rating).</p>
            {s.not_equivalence && <p className="text-xs text-fg3">{s.not_equivalence}.</p>}
            <p className="break-all font-mono text-xs text-fg3">{s.latest.file}{s.latest.scorecard_file ? ` · ${s.latest.scorecard_file}` : ""}</p>
          </div>
        ) : <Empty title="No full benchmark run saved yet" hint="Start one on the Run tab (choose every group)." />}
      </Card>

      <Card title="Official score (v1, frozen rules)">
        {v1 ? (
          <div className="space-y-2 text-sm">
            <p className="flex flex-wrap items-baseline gap-2"><span className="num text-3xl font-semibold">{v1.overall_mean}</span><span className="text-fg2">mean of 14 capabilities, lowest {v1.lowest}</span><Badge tone="accent">official</Badge></p>
            <p className="text-xs text-fg2">{v1.note}. Scored {v1.time}.</p>
            <details>
              <summary className={`flex ${BIG} cursor-pointer items-center text-accent-fg`}>Per capability</summary>
              <ul className="divide-y divide-line text-sm">{Object.entries(v1.capabilities).map(([k, c]) => (
                <li key={k} className="flex flex-wrap items-center gap-2 py-1.5"><span className="num w-10 font-semibold">{c.score}</span><span>{c.name}</span>{c.why[0] && <span className="w-full text-xs text-fg3">{c.why[0]}</span>}</li>))}</ul>
            </details>
            <p className="break-all font-mono text-xs text-fg3">{v1.file}</p>
          </div>
        ) : <Empty title="No v1 score file found" />}
      </Card>

      <Card title="Dimensions (v2): tap one for its cases">
        <p className="mb-2 text-xs text-fg2">Every score is 10 × passed / executed, capped by the frozen rules; deferred cases never count as passed. {s.ten_requires && `A 10 needs: ${s.ten_requires}.`}</p>
        <p className="mb-2 text-xs text-fg3">{s.evidence_note}</p>
        <ul className="divide-y divide-line">{Object.entries(s.dimensions).map(([k, d]) => <DimensionRow key={k} name={k} d={d} />)}</ul>
      </Card>

      <Card title="Failed and deferred">
        <Section title={`Failed or errored (${s.failed.length})`}>
          {s.failed.length ? <ul className="divide-y divide-line text-sm">{s.failed.map((f) => (
            <li key={f.id} className="py-1.5"><Badge tone="err">{f.status}</Badge> <span className="font-mono text-xs">{f.id}</span> {f.title}<p className="break-words text-xs text-fg2">{f.observed}</p></li>))}</ul>
            : <p className="text-sm text-fg2">None in the newest full run.</p>}
        </Section>
        <div className="mt-4" />
        <Section title={`Known issues: ${s.deferred.length} deferred (need the Windows laptop or the owner's hardware)`}>
          <ul className="divide-y divide-line text-sm">{s.deferred.map((d) => (
            <li key={d.id} className="py-1.5"><div className="flex flex-wrap items-center gap-2"><Badge tone="wait">deferred</Badge><span className="font-mono text-xs">{d.id}</span><span className="text-xs text-fg3">{d.platform} · {d.risk}</span></div>
              <p>{d.title}</p><p className="text-xs text-fg2">{d.reason}</p></li>))}</ul>
        </Section>
      </Card>

      <Card title={`Trend (last ${s.trend.length} full runs)`}>
        {s.trend.length ? <ul className="divide-y divide-line text-sm">{[...s.trend].reverse().map((t) => (
          <li key={t.file} className="py-1.5"><p className="font-medium">{runLabel(t.file)}</p><p className="num text-xs text-fg2">{countsText(t.counts)} · mean {t.mean_of_scored ?? "—"}</p></li>))}</ul>
          : <Empty title="No full runs yet" />}
      </Card>

      <Card title="Context selection">
        {s.context.length ? <ul className="space-y-3 text-sm">{s.context.map((c) => (
          <li key={c.file}><p className="flex flex-wrap items-center gap-2"><span className="font-medium">{c.set}</span><EvidenceTag kind={c.evidence} /><span className="text-xs text-fg3">{c.fixtures} fixtures · {c.time}</span></p>
            <ul className="mt-1 space-y-0.5 text-xs text-fg2">{Object.entries(c.summary).map(([k, v]) => (
              <li key={k} className="num"><span className="font-mono">{k}</span>: answer kept {String(v.answer_kept ?? "—")}, critical recall {String(v.critical_recall_mean ?? "—")}, reduction {String(v.reduction_mean ?? "—")}, fits budget {String(v.fits_budget ?? "—")}</li>))}</ul></li>))}</ul>
          : <Empty title="No context-selection results" />}
      </Card>

      <Card title="Live results">
        {s.live.length ? <ul className="divide-y divide-line text-sm">{s.live.map((l) => (
          <li key={l.file} className="py-1.5"><p className="flex flex-wrap items-center gap-2"><span className="font-medium">{l.name}</span><EvidenceTag kind="live" /></p>
            <p className="num text-xs text-fg2">{countsText(l.counts)} · {l.time}</p></li>))}</ul>
          : <Empty title="No live results" />}
      </Card>

      <Card title="Model comparison (latest)"><ModelResultsView m={s.models} /></Card>
    </div>
  );
}

function ModelResultsView({ m }: { m: ModelResults | null }) {
  if (!m) return <Empty title="No model comparison has been run yet" hint="Nothing is shown until results/models-*.json exists." />;
  return (
    <div className="space-y-2 text-sm">
      <p className="text-xs text-fg2">{m.time} · {m.host} · {m.threads} threads · sets {m.sets.join(", ")}</p>
      <ul className="divide-y divide-line">{m.rows.map((r) => (
        <li key={r.model} className="py-1.5"><p className="font-medium">{r.model}</p>
          <p className="num text-xs text-fg2">{r.error ? r.error : `accuracy ${accuracy(r)} · JSON valid ${r.json_valid ?? "—"} · p50 ${r.p50_s ?? "—"} s · peak RSS ${r.peak_rss_mb ?? "—"} MB`}</p></li>))}</ul>
    </div>
  );
}

// ------------------------------------------------------------------ run
interface RunRow { file: string; time: string; counts: { pass: number; fail: number; error: number; deferred: number } | null; full: boolean; cases: number }
interface Compare { a: string; b: string; fixed: CmpItem[]; broken: CmpItem[]; still_failing: CmpItem[]; other_changes: CmpItem[]; unchanged: number; only_in_a: string[]; only_in_b: string[] }
interface CmpItem { id: string; title: string; a: string; b: string; observed: string }

function RunTab({ s, onJobEnd }: { s: Summary; onJobEnd: () => void }) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [err, setErr] = useState<string | null>(null);
  const { job, follow } = useJob(null);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [rerun, setRerun] = useState("");
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const [cmp, setCmp] = useState<Compare | null>(null);
  const ended = useRef<string | null>(null);

  const loadRuns = useCallback(() => {
    api.get<{ runs: RunRow[] }>("/api/bench/runs").then((r) => {
      setRuns(r.runs);
      setRerun((x) => x || r.runs[0]?.file || "");
      setB((x) => x || r.runs[0]?.file || "");
      setA((x) => x || r.runs[1]?.file || "");
    }).catch((e: Error) => setErr(e.message));
  }, []);
  useEffect(() => {
    loadRuns();
    api.get<{ active: JobView | null }>("/api/bench/jobs").then((r) => { if (r.active) follow(r.active); }).catch(() => undefined);
  }, [loadRuns, follow]);
  useEffect(() => {
    if (job && job.state !== "running" && ended.current !== job.id) { ended.current = job.id; loadRuns(); onJobEnd(); }
  }, [job, loadRuns, onJobEnd]);

  const running = job?.state === "running";
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const total = s.groups.filter((g) => picked.has(g.id)).reduce((n, g) => n + g.cases, 0);
  const start = async () => {
    setErr(null);
    try { follow(await api.post<JobView>("/api/bench/run", { groups: [...picked] })); } catch (e) { setErr((e as Error).message); }
  };
  const rerunFailed = async () => {
    setErr(null);
    try { follow(await api.post<JobView>("/api/bench/rerun-failed", { run: rerun })); } catch (e) { setErr((e as Error).message); }
  };
  const compare = async () => {
    setErr(null); setCmp(null);
    try { setCmp(await api.get<Compare>(`/api/bench/runs/${encodeURIComponent(a)}/compare/${encodeURIComponent(b)}`)); } catch (e) { setErr((e as Error).message); }
  };
  const runOption = (r: RunRow) => <option key={r.file} value={r.file}>{runLabel(r.file)}{r.counts ? ` — ${r.counts.pass} pass, ${r.counts.fail + r.counts.error} fail` : ""}</option>;

  return (
    <div className="space-y-4">
      <Card title="Run benchmark groups">
        <fieldset className="space-y-1">
          <legend className="mb-2 text-sm text-fg2">Choose what to run. Every group runs the real code against fixtures on this server; nothing touches a real PC, account or browser profile.</legend>
          <div className="grid gap-1 sm:grid-cols-2">
            {s.groups.map((g) => (
              <label key={g.id} className={`flex ${BIG} cursor-pointer items-center gap-3 rounded-lg border border-line px-3 hover:bg-hover`}>
                <input type="checkbox" className="size-5 accent-[var(--accent)]" checked={picked.has(g.id)} onChange={() => toggle(g.id)} disabled={running} />
                <span className="text-sm">{g.label}</span><span className="num ml-auto text-xs text-fg3">{g.cases} cases · {g.areas.join("")}</span>
              </label>))}
          </div>
        </fieldset>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button className={BIG} onClick={() => setPicked(new Set(s.groups.map((g) => g.id)))} disabled={running}>Select all</Button>
          <Button className={BIG} onClick={() => setPicked(new Set())} disabled={running}>Clear</Button>
          <Button kind="primary" className={`${BIG} grow sm:grow-0`} disabled={running || picked.size === 0} onClick={() => void start()}>
            Start{picked.size ? ` (${total} cases)` : ""}</Button>
        </div>
        {picked.size === s.groups.length && <p className="mt-2 text-xs text-fg2">Every group = one full run; it becomes the newest full run the scorecard uses.</p>}
        {err && <div className="mt-3"><ErrorNote>{err}</ErrorNote></div>}
        {job && <div className="mt-3"><JobPanel job={job} onChange={follow} /></div>}
      </Card>

      <Card title="Re-run failed cases">
        <label className="block text-sm"><span className="mb-1 block text-xs font-medium text-fg2">From run</span>
          <select className={`${inputCls} ${BIG}`} value={rerun} onChange={(e) => setRerun(e.target.value)}>{runs.map(runOption)}</select></label>
        <Button className={`${BIG} mt-2 w-full sm:w-auto`} disabled={running || !rerun} onClick={() => void rerunFailed()}>Re-run its failed cases</Button>
      </Card>

      <Card title="Compare two runs">
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="block text-sm"><span className="mb-1 block text-xs font-medium text-fg2">Before</span>
            <select className={`${inputCls} ${BIG}`} value={a} onChange={(e) => setA(e.target.value)}>{runs.map(runOption)}</select></label>
          <label className="block text-sm"><span className="mb-1 block text-xs font-medium text-fg2">After</span>
            <select className={`${inputCls} ${BIG}`} value={b} onChange={(e) => setB(e.target.value)}>{runs.map(runOption)}</select></label>
        </div>
        <Button className={`${BIG} mt-2 w-full sm:w-auto`} disabled={!a || !b || a === b} onClick={() => void compare()}>Compare</Button>
        {cmp && (
          <div className="mt-3 space-y-3 text-sm" aria-label="Comparison">
            <p className="num text-fg2">{cmp.fixed.length} fixed · {cmp.broken.length} broken · {cmp.still_failing.length} still failing · {cmp.other_changes.length} other changes · {cmp.unchanged} unchanged</p>
            {([["Fixed", cmp.fixed], ["Broken", cmp.broken], ["Still failing", cmp.still_failing], ["Other changes", cmp.other_changes]] as const).map(([t, list]) => list.length > 0 && (
              <Section key={t} title={t}><ul className="divide-y divide-line">{list.map((x) => (
                <li key={x.id} className="py-1.5"><span className="font-mono text-xs">{x.id}</span> <Badge tone={statusTone(x.a)}>{x.a}</Badge> → <Badge tone={statusTone(x.b)}>{x.b}</Badge><p>{x.title}</p></li>))}</ul></Section>))}
            {(cmp.only_in_a.length > 0 || cmp.only_in_b.length > 0) && <p className="text-xs text-fg3">Only in one run: {cmp.only_in_a.length} before, {cmp.only_in_b.length} after.</p>}
          </div>)}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ challenge lab
function ChallengeView({ c }: { c: Challenge }) {
  return (
    <div className="space-y-3" aria-label="Challenge result">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={c.yes === 6 ? "done" : c.yes >= 4 ? "wait" : "err"}>{c.yes}/6 yes</Badge>
        <Badge tone="wait">untrusted</Badge>
        <Badge tone={c.model.available ? "done" : "idle"}>{c.model.note}</Badge>
        {c.highest_risk && <Badge tone={riskTone(c.highest_risk)}>highest risk {c.highest_risk}</Badge>}
      </div>
      <p className="break-words text-sm">“{c.text}”</p>
      <ol className="space-y-2">{c.grades.map((g) => (
        <li key={g.key} className="rounded-lg border border-line p-2">
          <p className="flex items-center gap-2 text-sm font-medium"><Badge tone={statusTone(g.answer)}>{g.answer}</Badge>{g.question}</p>
          <p className="mt-1 break-words text-xs text-fg2">{g.evidence}</p>
        </li>))}</ol>
      <Section title={`Plan: ${c.plan.status}${c.plan.model ? ` (${c.plan.model === "rule" ? "rules" : c.plan.model})` : ""}`}>
        {c.steps.length ? <ol className="space-y-2">{c.steps.map((s) => (
          <li key={s.n} className="rounded-lg border border-line p-2 text-sm">
            <p className="flex flex-wrap items-center gap-2"><span className="font-medium">{s.n}. {s.tool}</span><span className="text-xs text-fg3">{s.agent} agent</span>
              <Badge tone={riskTone(s.risk.level)}>{s.risk.level ?? "no PC action"}</Badge>{s.risk.needs_approval && <Badge tone="wait">approval needed</Badge>}</p>
            <p className="break-words text-xs text-fg2">{s.risk.reason}</p>
            <p className="break-words text-xs text-fg3">Check: {s.post || "—"}{s.ran ? ` · ran in the sandbox, ${s.passed ? "check passed (simulated)" : "check not passed"}` : " · not run"}</p>
          </li>))}</ol> : <p className="text-sm text-fg2">{c.plan.question || c.plan.problems.join("; ") || "No steps."}</p>}
      </Section>
      <Section title="What the coordinator would say">
        <p className="break-words rounded-lg bg-bg2 p-2 text-sm">{c.coordinator.summary}</p>
        <p className="text-xs text-fg3">{c.note}</p>
      </Section>
    </div>
  );
}

interface ChallengeRow { id: string; time: string; text: string; yes: number; status: string }

function ChallengeTab() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<Challenge | null>(null);
  const [saved, setSaved] = useState<ChallengeRow[]>([]);
  const load = useCallback(() => { api.get<{ challenges: ChallengeRow[] }>("/api/bench/challenges").then((r) => setSaved(r.challenges)).catch(() => undefined); }, []);
  useEffect(load, [load]);
  const submit = async () => {
    setBusy(true); setErr(null);
    try { setRes(await api.post<Challenge>("/api/bench/challenge", { text })); load(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const open = async (id: string) => { try { setRes(await api.get<Challenge>(`/api/bench/challenge/${id}`)); } catch (e) { setErr((e as Error).message); } };
  const remove = async (id: string) => {
    try { await api.del(`/api/bench/challenge/${id}`); if (res?.id === id) setRes(null); load(); } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="space-y-4">
      <Card title="Challenge lab">
        <p className="mb-3 text-sm text-fg2">Type any request. It is planned by the real decider (rules only when no local model answers), run through the real coordinator with
          sandboxed agents that only record what they would do, and every action is checked by the kernel's policy. Nothing happens on a PC. Results are saved as
          <strong> untrusted</strong> and never become benchmark cases by themselves.</p>
        <label className="block"><span className="mb-1 block text-xs font-medium text-fg2">Request to try</span>
          <textarea className={`${inputCls} min-h-24 text-base`} value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} placeholder="e.g. open notepad, or go to github.com" /></label>
        <Button kind="primary" className={`${BIG} mt-2 w-full sm:w-auto`} disabled={busy || !text.trim()} onClick={() => void submit()}>{busy ? "Checking…" : "Submit"}</Button>
        {err && <div className="mt-3"><ErrorNote>{err}</ErrorNote></div>}
        {res && <div className="mt-4"><ChallengeView c={res} /></div>}
      </Card>
      <Card title={`Saved challenges (${saved.length}, untrusted)`}>
        {saved.length ? <ul className="divide-y divide-line">{saved.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-2 py-2">
            <div className="min-w-0 grow"><p className="break-words text-sm">{c.text}</p><p className="num text-xs text-fg3">{c.time} · {c.yes}/6 yes · {c.status}</p></div>
            <Button className={BIG} onClick={() => void open(c.id)} aria-label={`Open challenge ${c.text.slice(0, 40)}`}>Open</Button>
            <Button kind="danger" className={BIG} onClick={() => void remove(c.id)} aria-label={`Delete challenge ${c.text.slice(0, 40)}`}>Delete</Button>
          </li>))}</ul> : <Empty title="No saved challenges" />}
      </Card>
    </div>
  );
}

// ------------------------------------------------------------------ models
interface ModelsView { candidates: Candidate[]; sets: string[]; latest: ModelResults | null; model_dir: string; script_present: boolean; note: string }

function ModelsTab() {
  const [m, setM] = useState<ModelsView | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [sets, setSets] = useState<Set<string>>(new Set(["plan_cases", "dev"]));
  const { job, follow } = useJob(null);
  const load = useCallback(() => { api.get<ModelsView>("/api/bench/models").then(setM).catch((e: Error) => setErr(e.message)); }, []);
  useEffect(load, [load]);
  useEffect(() => { if (job && job.state !== "running") load(); }, [job, load]);
  if (!m) return err ? <ErrorNote>{err}</ErrorNote> : <Spinner />;
  const flip = (s: Set<string>, v: string) => { const n = new Set(s); if (n.has(v)) n.delete(v); else n.add(v); return n; };
  const row = (name: string) => m.latest?.rows.find((r) => r.model === name);
  const run = async () => {
    setErr(null);
    try { follow(await api.post<JobView>("/api/bench/models/run", { models: [...picked], sets: [...sets] })); } catch (e) { setErr((e as Error).message); }
  };
  const running = job?.state === "running";
  return (
    <div className="space-y-4">
      <Card title="Local model candidates">
        <p className="mb-3 text-xs text-fg2">{m.note}</p>
        {m.candidates.length === 0 && <Empty title="No model_candidates.json in this checkout" />}
        <ul className="space-y-2">{m.candidates.map((c) => {
          const r = row(c.name);
          return (
            <li key={c.name} className="rounded-lg border border-line p-2">
              <label className={`flex ${BIG} items-center gap-3`}>
                <input type="checkbox" className="size-5 accent-[var(--accent)]" disabled={!c.present || running} checked={picked.has(c.name)} onChange={() => setPicked((p) => flip(p, c.name))} />
                <span className="font-medium">{c.name}</span>
                <Badge tone={c.present ? "done" : "idle"}>{c.present ? "on this machine" : "not downloaded"}</Badge>
              </label>
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-xs">
                <dt className="text-fg3">Licence</dt><dd>{c.license}</dd>
                <dt className="text-fg3">Size</dt><dd className="num">{sizeText(c.bytes)}</dd>
                <dt className="text-fg3">Accuracy</dt><dd className="num">{r ? accuracy(r) : "not measured"}</dd>
                <dt className="text-fg3">Latency p50</dt><dd className="num">{r?.p50_s != null ? `${r.p50_s} s (CPU, this server)` : "—"}</dd>
                <dt className="text-fg3">Peak RSS</dt><dd className="num">{r?.peak_rss_mb != null ? `${r.peak_rss_mb} MB` : "—"}</dd>
                <dt className="text-fg3">File</dt><dd className="break-all font-mono">{c.repo}/{c.file}</dd>
                <dt className="text-fg3">SHA-256</dt><dd className="break-all font-mono">{c.sha256.slice(0, 16)}…</dd>
              </dl>
              {c.note && <p className="mt-1 text-xs text-fg2">{c.note}</p>}
            </li>);
        })}</ul>
        <fieldset className="mt-3">
          <legend className="mb-1 text-xs font-medium text-fg2">Case sets</legend>
          <div className="flex flex-wrap gap-2">{m.sets.map((s) => (
            <label key={s} className={`flex ${BIG} items-center gap-2 rounded-lg border border-line px-3`}>
              <input type="checkbox" className="size-5 accent-[var(--accent)]" checked={sets.has(s)} disabled={running} onChange={() => setSets((x) => flip(x, s))} /><span className="text-sm">{s}</span></label>))}</div>
        </fieldset>
        <Button kind="primary" className={`${BIG} mt-3 w-full sm:w-auto`} disabled={running || picked.size === 0 || sets.size === 0 || !m.script_present} onClick={() => void run()}>
          Compare {picked.size || ""} selected model{picked.size === 1 ? "" : "s"}</Button>
        {!m.candidates.some((c) => c.present) && m.candidates.length > 0 && <p className="mt-2 text-xs text-fg3">No candidate file is in {m.model_dir} yet, so nothing can run from here.</p>}
        {err && <div className="mt-3"><ErrorNote>{err}</ErrorNote></div>}
        {job && <div className="mt-3"><JobPanel job={job} onChange={follow} /></div>}
      </Card>
      <Card title="Latest comparison"><ModelResultsView m={m.latest} /></Card>
    </div>
  );
}

// ------------------------------------------------------------------ security
function SecurityTab() {
  const [d, setD] = useState<{ cases: CaseRow[]; counts: Record<string, number>; scope: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "bad" | "deferred">("all");
  useEffect(() => { api.get<typeof d>("/api/bench/security").then(setD).catch((e: Error) => setErr(e.message)); }, []);
  if (err) return <ErrorNote>{err}</ErrorNote>;
  if (!d) return <Spinner />;
  const shown = d.cases.filter((c) => filter === "all" || (filter === "deferred" ? c.status === "deferred" : ["fail", "error", "not run"].includes(c.status)));
  return (
    <Card title={`Security and reliability cases (${d.cases.length})`}>
      <p className="mb-2 text-xs text-fg2">{d.scope}. Each case shows its newest recorded result.</p>
      <div className="mb-3 flex flex-wrap gap-2">
        <Badge tone="done">{d.counts.pass} passed</Badge><Badge tone={d.counts.fail ? "err" : "idle"}>{d.counts.fail} failed</Badge>
        <Badge tone={d.counts.error ? "err" : "idle"}>{d.counts.error} errors</Badge><Badge tone="wait">{d.counts.deferred} deferred</Badge>
        {d.counts["not run"] > 0 && <Badge>{d.counts["not run"]} not run</Badge>}
      </div>
      <div role="group" aria-label="Filter" className="mb-2 grid grid-cols-3 gap-1">
        {([["all", "All"], ["bad", "Not passing"], ["deferred", "Deferred"]] as const).map(([k, t]) => (
          <Button key={k} className={BIG} kind={filter === k ? "primary" : "default"} aria-pressed={filter === k} onClick={() => setFilter(k)}>{t}</Button>))}
      </div>
      {shown.length ? <ul className="divide-y divide-line">{shown.map((c) => <CaseItem key={c.id} c={c} />)}</ul> : <Empty title="Nothing in this filter" />}
    </Card>
  );
}

// ------------------------------------------------------------------ screen
export function Bench() {
  const [tab, setTab] = useSubTab();
  const [s, setS] = useState<Summary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => { api.get<Summary>("/api/bench/summary").then((x) => { setS(x); setErr(null); }).catch((e: Error) => setErr(e.message)); }, []);
  useEffect(load, [load]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="text-lg font-semibold">Bench</h1>
        <span className="text-xs text-fg3">what is measured, how, and how sure it is</span>
      </div>
      <div role="tablist" aria-label="Bench sections" className="grid grid-cols-3 gap-1 rounded-xl border border-line bg-bg2 p-1 sm:grid-cols-5">
        {TABS.map((t) => (
          <button key={t.id} role="tab" type="button" aria-selected={tab === t.id} onClick={() => setTab(t.id)}
            className={`${BIG} rounded-lg px-2 text-sm ${tab === t.id ? "bg-surface font-medium text-fg shadow-sm" : "text-fg2 hover:text-fg"}`}>{t.label}</button>))}
      </div>
      {err && <ErrorNote>{err}</ErrorNote>}
      <div role="tabpanel" aria-label={TABS.find((t) => t.id === tab)?.label}>
        {tab === "challenge" ? <ChallengeTab /> : tab === "models" ? <ModelsTab /> : tab === "security" ? <SecurityTab /> :
          !s ? (!err && <Spinner label="Loading results" />) : tab === "run" ? <RunTab s={s} onJobEnd={load} /> : <Dashboard s={s} />}
      </div>
    </div>
  );
}
