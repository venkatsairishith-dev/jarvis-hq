// Shapes of what the kernel's owner plane and the console return. Nothing here is invented client-side.
export type TaskState =
  | "planning" | "waiting_approval" | "queued" | "running" | "verifying" | "repairing" | "paused" | "completed" | "failed" | "cancelled";

export interface TaskEvent { ts: number; state: string; kind: string; note: string }
export interface Task {
  id: string; owner: string; lane: string; source: string; goal: string; state: TaskState; step: string;
  attempts: number; max_attempts: number; created: number; updated: number; deadline: number; result: string;
  criteria: string[]; plan: string[]; verification: { passed?: boolean; [k: string]: unknown }; evidence: string[]; events: TaskEvent[];
}
export interface Approval {
  id: string; level: string;
  action: {
    tool: string; op: string; target: string; lane?: string; params?: Record<string, unknown>; card?: Record<string, unknown>;
    invisible?: InvisibleReport; invisible_chars?: number; has_invisible?: boolean;   // the server wrote invisible characters out as <U+XXXX> (re-review M1)
    purged?: boolean; text_removed?: string;                                          // a decided approval whose text was removed (retention)
  };
  seconds_left: number; reason: string; digest: string; truncated: boolean;
  blocked?: boolean; invisible_chars?: number; has_invisible?: boolean;   // blocked: invisible/control/direction characters, it cannot be approved
  chars?: number;       // characters of text on the card; the card holds ALL of it, or `truncated` is true and it cannot be approved (review M6)
  requester?: string;   // the agent that asked ("" or absent: the owner's own request); part of what is approved (audit SEC-D3)
}
export interface InvisibleReport {
  status: "clean" | "notice" | "warning" | "blocked"; invisible_chars: number; hostile: number; benign: number; has_invisible: boolean;
  fields: Record<string, { invisible_chars: number; has_invisible: boolean; hostile: number }>; flags: string[]; note?: string;
}
export interface ApprovalOutcome {
  id: string; level: string; state: "approved" | "used" | "denied" | "expired"; at: number; job: string | null; reason: string; requester?: string;
  action: { tool: string; op: string; target: string; lane?: string };
}
export interface Job { id: string; node: string; kind: string; state: string; created: number; finished: number | null; owner: string; params: string; result: string }
export interface Command { id: string; ts: number; source: string; text: string; state: "running" | "done" | "failed" | "cancelled"; reply: string | null; error: string | null; finished: number | null }
export interface OwnerState {
  now: number; killed: boolean; audit: { seq: number; hash: string }; pin_set: boolean; voice_command_ready: boolean; busy: boolean;
  tasks: Task[]; approvals: Approval[]; approval_history: ApprovalOutcome[]; jobs: Job[]; commands: Command[];
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
  hub: { ok: boolean; ms: number; url: string; error?: string; configured?: boolean };
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

// Connectors: what the kernel records (the owner's decisions per tool) and what the laptop's allowlist adds.
export type ConnectorRisk = "unknown" | "read" | "write" | "send" | "destructive" | "credential";
export type ConnectorStatus = "connected" | "installed" | "configured" | "degraded" | "disabled";
export interface ConnectorTool {
  tool: string; description: string; schema: { properties?: Record<string, { type?: string; description?: string; enum?: unknown[] }>; required?: string[] };
  claimed: Record<string, boolean>; digest: string; suggested: ConnectorRisk; risk: ConnectorRisk; enabled: boolean;
  approved_digest: string | null; changed: boolean; present: boolean;
}
export interface Connector {
  name: string; label: string; purpose: string; transport: "stdio" | "https"; node: string; endpoint: string; secret_ref: string;
  env_refs: Record<string, string>; lanes: string[]; enabled: boolean; max_calls_per_hour: number; timeout_s: number; max_output: number;
  installed: number | null; era: string | null; protocol: string | null; server: { name?: string; version?: string }; reconnects: number;
  last_ok: number | null; last_test: number | null; last_test_ok: number | null; last_test_ms: number | null;
  last_error: string | null; last_error_class: string | null; state: string | null;
  status: ConnectorStatus; needs_setup: boolean; tools: ConnectorTool[]; tools_enabled: number; tools_changed: number;
}
export interface ConnectorCall {
  ts: number; connector: string; tool: string; risk: string | null; agent: string; lane: string; decision: string; outcome: string;
  ms: number | null; bytes: number | null; error_class: string | null; job: string | null;
}
export interface ConnectorLocal {
  command: string[]; installed: boolean; env_refs: Record<string, string>; secrets_present: Record<string, boolean>; env_static: string[]; timeout_s: number;
}
export interface ConnectorTemplate {
  id: string; title: string; status: "tested" | "template"; summary: string;
  source: { project: string; url: string; license: string; package: string };
  transport: "stdio" | "https"; definition: Record<string, unknown>;
  local: { command: string[]; env_refs: Record<string, string>; env_static: Record<string, string>; timeout_s: number } | null;
  setup: string[]; risks: string[];
}
