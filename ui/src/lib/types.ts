// Shapes of what the kernel's owner plane and the console return. Nothing here is invented client-side.
export type TaskState =
  | "planning" | "waiting_approval" | "queued" | "running" | "verifying" | "repairing" | "paused" | "completed" | "failed" | "cancelled";

export interface TaskEvent { ts: number; state: string; kind: string; note: string }
export interface Task {
  id: string; owner: string; lane: string; source: string; goal: string; state: TaskState; step: string;
  attempts: number; max_attempts: number; created: number; updated: number; deadline: number; result: string;
  criteria: string[]; plan: string[]; verification: { passed?: boolean; [k: string]: unknown }; evidence: string[]; events: TaskEvent[];
}
export interface Approval { id: string; level: string; action: { tool: string; op: string; target: string; lane?: string; params?: Record<string, unknown> }; seconds_left: number }
export interface Job { id: string; node: string; kind: string; state: string; created: number; finished: number | null; owner: string; params: string; result: string }
export interface Command { id: string; ts: number; source: string; text: string; state: "running" | "done" | "failed" | "cancelled"; reply: string | null; error: string | null; finished: number | null }
export interface OwnerState {
  now: number; killed: boolean; audit: { seq: number; hash: string }; pin_set: boolean; voice_command_ready: boolean; busy: boolean;
  tasks: Task[]; approvals: Approval[]; jobs: Job[]; commands: Command[];
}
export interface AuditEvent { seq: number; ts: number; kind: string; payload: Record<string, unknown> }

export interface VoiceStatus {
  engine: string | null; vad: boolean; resident_stt: string | null; resident_tts: boolean; idle_unload_s: number; threads: number;
  stt: Record<string, { model: string; available: boolean }>; tts: Record<string, { model: string; available: boolean }>;
  stats: { stt_calls: number; tts_calls: number; last_stt_ms: number | null; last_tts_ms: number | null }; last_error: string | null;
  audio_leaves_machine: boolean;
}
export interface Health {
  console: { ok: boolean; uptime_s: number; port: number };
  kernel: { reachable: boolean; authorized: boolean; status?: number; ms?: number | null; killed?: boolean | null; pin_set?: boolean | null; voice_command_ready?: boolean | null; error: string | null };
  voice: VoiceStatus;
  local_llm: { ok: boolean; ms: number; url: string; error?: string };
  hub: { ok: boolean; ms: number; url: string; error?: string };
  disk: { free_gb: number; total_gb: number } | null;
}

export interface Automation {
  id: string; name: string; owner: string; action_kind: "lab_task" | "job" | "command"; action: Record<string, unknown>;
  trigger_kind: string; schedule: string; tz: string; enabled: number; state: string; paused_reason: string | null;
  next_run: number | null; catchup: string; overlap: string; max_runs_per_day: number; pause_after_failures: number;
  failure_streak: number; repeat_done: number; version: number; created: number;
  runs: { id: number; run_key: string; scheduled_for: number | null; fired_at: number | null; source: string; status: string;
          task_id: string | null; job_id: string | null; command_id: string | null; detail: string | null }[];
}
export interface AutomationPreview {
  definition: Record<string, unknown>; next_runs: { utc: string; local: string }[]; side_effects: string[];
  limits: Record<string, unknown>;
}
export interface AgentDef {
  id: string; name: string; purpose: string; role: string; instructions: string; runtime: string; lane: string; tools: string[];
  skills: string[]; model_pref: string | null; model_fallback: string; workspace: Record<string, string>; permissions: string[];
  criteria: string[]; verification: string; max_attempts: number; budget_s: number; max_runs_per_day: number;
  state: "draft" | "active" | "paused" | "terminated"; version: number; runs_today: number;
  tasks: { id: string; goal: string; state: string; created: number; attempts: number; max_attempts: number }[];
  capability_notes?: Record<string, string>; enforced_by_kernel?: string[]; not_enforced_by_kernel?: string[];
}
export interface MemoryEntry { id: string; kind: string; text: string; version: number; updated: number; created: number; history: { v: number; at: number; op: string; text?: string }[] }
export interface ProviderRow {
  name: string; lane: string; model: string; available: boolean; preferred: boolean; ok: number | null; failed: number | null;
  last_ok: number | null; last_fail: number | null; last_error: string | null; limits: Record<string, unknown>;
}
export interface FileRow { path: string; name: string; group: string; bytes: number; modified: number; kind: string; area: string }
