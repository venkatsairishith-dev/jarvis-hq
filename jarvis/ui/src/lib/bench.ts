// Types and formatting for the Bench screen. The rule everywhere: a score is never shown without its sample size and label.

export type Status = "pass" | "fail" | "error" | "deferred" | "not run";
export type Evidence = "live" | "integration" | "mocked";
export type Counts = { pass: number; fail: number; error: number; deferred: number };

export interface Dimension {
  score: number | null; label: "verified" | "provisional" | "unverified"; cases: number; executed: number; passed: number;
  failed: string[]; deferred: number; deferred_critical: string[]; why: string[]; case_ids: string[];
  evidence: Record<Evidence, number>; description: string;
}
export interface CaseRow {
  id: string; title: string; area: string; risk: string; status: Status; observed: string; run?: string; evidence: Evidence;
  platform: string; scenario?: string; expected?: string; dims?: string[];
}
export interface JobView {
  id: string; kind: string; title: string; state: "running" | "done" | "failed" | "cancelled"; started: number; finished: number | null;
  done: number; total: number; current: string; step: number; steps: number; counts: Counts; saved: string[]; error: string | null; log_tail: string[];
}
export interface Summary {
  bench_version: number | null; rubric_version?: number; ten_requires?: string; not_equivalence?: string; total_cases: number;
  groups: { id: string; label: string; areas: string[]; cases: number }[];
  latest: { file: string; time: string; host: string; counts: Counts; cases: number; mean_of_scored: number | null; scored_dimensions: number; scorecard_file: string | null } | null;
  dimensions: Record<string, Dimension>;
  failed: { id: string; title: string; status: Status; risk: string; observed: string }[];
  deferred: { id: string; title: string; area: string; risk: string; platform: string; reason: string }[];
  trend: { file: string; time: string; counts: Counts; mean_of_scored: number | null }[];
  official_v1: { label: "official"; file: string; time: string; file_label: string; rules_version: number; overall_mean: number; lowest: number;
                 capabilities: Record<string, { name: string; score: number; why: string[] }>; note: string } | null;
  models: ModelResults | null;
  context: { file: string; set: string; time: string; fixtures: number; summary: Record<string, Record<string, string | number>>; evidence: Evidence }[];
  live: { file: string; name: string; time: string; counts: Partial<Counts>; evidence: Evidence; cases: { id: string; title: string; status: Status; observed: string }[] }[];
  evidence_note: string; job: JobView | null;
}
export interface ModelRow { model: string; error?: string; sets?: Record<string, string>; correct?: number; total?: number; json_valid?: string;
  p50_s?: number | null; p95_s?: number | null; gen_tps_p50?: number | null; peak_rss_mb?: number; load_s?: number }
export interface ModelResults { file: string; time: string; host: string; threads: number; sets: string[]; rows: ModelRow[]; evidence: Evidence }
export interface Candidate { name: string; repo: string; file: string; sha256: string; bytes: number; license: string; note: string; present: boolean }
export interface Grade { key: string; question: string; answer: "yes" | "no" | "partly" | "n/a"; evidence: string }
export interface Challenge {
  id: string; time: string; text: string; trusted: false; note: string;
  model: { available: boolean; note: string; used: string; seconds: number };
  plan: { status: string; question: string; problems: string[]; model: string; steps: number };
  steps: { n: number; tool: string; agent: string; args: Record<string, string>; post: string; ran: boolean; passed: boolean | null; checked: string | null;
           risk: { level: string | null; reason: string; needs_approval: boolean; denied: boolean } }[];
  coordinator: { status: string; summary: string; simulated: boolean };
  grades: Grade[]; yes: number; highest_risk: string | null;
}

export type Tone = "idle" | "run" | "wait" | "err" | "done" | "accent";

/** "9.0 / 10" only next to its evidence: "32/32 passed, 5 deferred"; an unverified dimension gets no number. */
export function scoreText(d: Pick<Dimension, "score">): string {
  return d.score === null ? "no score" : `${d.score.toFixed(d.score % 1 === 0 ? 1 : 2)} / 10`;
}

export function sampleText(d: Pick<Dimension, "passed" | "executed" | "deferred" | "cases">): string {
  const parts = [`${d.passed}/${d.executed} executed passed`];
  if (d.deferred) parts.push(`${d.deferred} deferred`);
  parts.push(`${d.cases} case${d.cases === 1 ? "" : "s"}`);
  return parts.join(" · ");
}

export function labelTone(label: string): Tone {
  return label === "verified" ? "done" : label === "provisional" ? "wait" : "err";
}

export function statusTone(s: string): Tone {
  if (s === "pass" || s === "done" || s === "yes") return "done";
  if (s === "fail" || s === "error" || s === "failed" || s === "no" || s === "DENY" || s === "RED") return "err";
  if (s === "deferred" || s === "partly" || s === "cancelled" || s === "YELLOW_APPROVAL") return "wait";
  if (s === "running") return "run";
  return "idle";
}

export function riskTone(level: string | null): Tone {
  if (!level) return "idle";
  if (level === "GREEN" || level === "YELLOW_AUTO") return "done";
  return statusTone(level);
}

export function evidenceText(e: Partial<Record<Evidence, number>>): string {
  return (["integration", "mocked", "live"] as Evidence[]).filter((k) => (e[k] ?? 0) > 0).map((k) => `${e[k]} ${k}`).join(" · ") || "no cases";
}

export function countsText(c: Partial<Counts> | null | undefined): string {
  if (!c) return "no counts";
  return (["pass", "fail", "error", "deferred"] as const).filter((k) => c[k] !== undefined).map((k) => `${c[k]} ${k === "pass" ? "passed" : k === "fail" ? "failed" : k === "error" ? "errors" : "deferred"}`).join(" · ");
}

/** 0..100, for a progress bar; an unknown total is 0, never a fake 100. */
export function percent(done: number, total: number): number {
  if (!total || total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((100 * done) / total)));
}

export function sizeText(bytes: number | null | undefined): string {
  if (!bytes) return "size unknown";
  return bytes >= 1e9 ? `${(bytes / 1e9).toFixed(2)} GB` : `${Math.round(bytes / 1e6)} MB`;
}

/** "bench-20261017-031703-K.json" -> "17 Oct 03:17 · area K"; unknown shapes come back as they are. */
export function runLabel(file: string): string {
  const m = /^bench-(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}(?:-([A-Za-z0-9_]+))?\.json$/.exec(file);
  if (!m) return file;
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m[2]) - 1] ?? m[2];
  const sel = m[6] ? (m[6] === "sel" ? " · selected cases" : ` · area ${m[6]}`) : " · full run";
  return `${Number(m[3])} ${mon} ${m[4]}:${m[5]}${sel}`;
}

export function accuracy(r: ModelRow): string {
  if (r.error) return "error";
  if (r.correct === undefined || !r.total) return "-";
  return `${r.correct}/${r.total} (${Math.round((100 * r.correct) / r.total)}%)`;
}

export const DIM_NAMES: Record<string, string> = {
  intent: "Intent", planning: "Planning", desktop: "Desktop control", browser: "Browser control", autonomy: "Multi-step autonomy",
  recovery: "Recovery", research: "Research", memory: "Memory", routing: "Routing", security: "Security", reliability: "Reliability",
  speed: "Speed", resources: "Resources", skills: "Skills", ux: "User experience", voice: "Voice",
};
