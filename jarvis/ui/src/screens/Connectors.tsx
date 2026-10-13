import { useCallback, useEffect, useMemo, useState } from "react";
import { api, owner } from "../lib/api";
import { store } from "../lib/store";
import { ago, clock } from "../lib/format";
import type { Connector, ConnectorCall, ConnectorLocal, ConnectorRisk, ConnectorStatus, ConnectorTemplate, ConnectorTool } from "../lib/types";
import { Badge, Button, Card, Dialog, Empty, ErrorNote, Field, Spinner, inputCls } from "../components/ui";

type Tone = "idle" | "run" | "wait" | "err" | "done" | "accent";
const STATUS: Record<ConnectorStatus, { tone: Tone; text: string; hint: string }> = {
  connected: { tone: "done", text: "Connected", hint: "Answered the last test or call, and (for a local program) is still presumed running." },
  installed: { tone: "accent", text: "Installed, not running", hint: "The program or service was reached and its tools were found, but nothing is running now." },
  configured: { tone: "wait", text: "Configured, not tested", hint: "The definition exists. Press Test connection to find out whether it works." },
  degraded: { tone: "err", text: "Degraded", hint: "It worked before, and the latest test or call failed." },
  disabled: { tone: "idle", text: "Disabled", hint: "No agent can use it while it is disabled." },
};
const FILTERS: { id: "all" | ConnectorStatus | "setup"; label: string }[] = [
  { id: "all", label: "All" }, { id: "connected", label: "Connected" }, { id: "installed", label: "Installed" }, { id: "configured", label: "Configured" },
  { id: "degraded", label: "Degraded" }, { id: "disabled", label: "Disabled" }, { id: "setup", label: "Needs setup" },
];
const RISKS: { id: ConnectorRisk; label: string }[] = [
  { id: "unknown", label: "Not classified" }, { id: "read", label: "Read (only looks)" }, { id: "write", label: "Write (changes something)" },
  { id: "send", label: "Send (reaches other people)" }, { id: "destructive", label: "Destructive (never enabled)" }, { id: "credential", label: "Credential (never enabled)" },
];
const RISK_NOTE: Record<string, string> = {
  read: "Runs when an agent asks.", write: "Needs your approval of the exact arguments, every time.", send: "Needs your approval of the exact arguments, every time.",
  destructive: "Can never be enabled.", credential: "Can never be enabled.", unknown: "Does not run until you classify it.",
};

const ERROR_HELP: Record<string, string> = {
  needs_setup: "A secret file this connector needs is missing. Create it as described in the setup steps.",
  not_configured: "This computer has no program configured for this connector. Press Edit and set the program.",
  start: "The program could not be started: check the path and that it is installed.",
  timeout: "The server did not answer in time.", crash: "The server stopped unexpectedly.", protocol: "The server speaks a protocol version JARVIS cannot use.",
  handshake: "The server rejected the protocol handshake.", unreachable: "The service could not be reached.", ssrf: "The address is internal or private and is refused.",
  insecure: "Plain http is refused: use https.", auth: "The service rejected the credential.", tool_changed: "The server changed this tool: review it again.",
  oversize: "The server sent more data than allowed.", redirect: "The service redirected the request; redirects are not followed.",
};

function riskTone(r: string | null): Tone { return r === "read" ? "done" : r === "write" || r === "send" ? "wait" : r === "destructive" || r === "credential" ? "err" : "idle"; }

interface Form {
  name: string; label: string; purpose: string; transport: "stdio" | "https"; program: string; args: string; refs: string; statics: string;
  endpoint: string; secret_ref: string; lanePublic: boolean; calls: number; timeout: number; max_output: number;
}
const BLANK: Form = { name: "", label: "", purpose: "", transport: "stdio", program: "", args: "", refs: "", statics: "", endpoint: "", secret_ref: "", lanePublic: false, calls: 60, timeout: 20, max_output: 16000 };

const lines = (s: string) => s.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
const pairs = (s: string, sep: string) => Object.fromEntries(lines(s).map((l) => { const i = l.indexOf(sep); return i < 0 ? [l, ""] : [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }));
const unpairs = (o: Record<string, string>, sep: string) => Object.entries(o).map(([k, v]) => `${k}${sep}${v}`).join("\n");

export function Connectors() {
  const [list, setList] = useState<Connector[] | null>(null);
  const [calls, setCalls] = useState<ConnectorCall[]>([]);
  const [local, setLocal] = useState<Record<string, ConnectorLocal>>({});
  const [err, setErr] = useState<string | null>(null);
  const [sel, setSel] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("all");
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState<Form | null>(null);
  const [editing, setEditing] = useState(false);
  const [templates, setTemplates] = useState<ConnectorTemplate[]>([]);
  const [tpl, setTpl] = useState<ConnectorTemplate | null>(null);
  const [formErr, setFormErr] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Connector | null>(null);
  const [confirmName, setConfirmName] = useState("");
  const [trying, setTrying] = useState<{ c: Connector; t: ConnectorTool } | null>(null);
  const [tryArgs, setTryArgs] = useState("{}");
  const [tryOut, setTryOut] = useState<{ text: string; ok: boolean } | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await owner.get<{ connectors: Connector[]; calls: ConnectorCall[] }>("connectors");
      setList(r.connectors); setCalls(r.calls); setErr(null);
      try { setLocal((await api.get<{ local: Record<string, ConnectorLocal> }>("/api/connectors/local")).local); } catch { /* the kernel part is what matters */ }
    } catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 10000); return () => window.clearInterval(t); }, [load]);

  const shown = useMemo(() => (list ?? []).filter((c) => {
    if (filter === "setup" ? !c.needs_setup : filter !== "all" && c.status !== filter) return false;
    const s = q.trim().toLowerCase();
    return !s || [c.name, c.label, c.purpose].some((x) => x.toLowerCase().includes(s));
  }), [list, q, filter]);
  const cur = (list ?? []).find((c) => c.name === sel) ?? null;

  const act = async (c: Connector, what: "test" | "connect" | "disconnect") => {
    setBusy(c.name + what);
    try {
      const r = await owner.post<{ ok?: boolean; pending?: boolean; error?: string; error_class?: string; ms?: number; connector: Connector }>(`connectors/${what}`, { name: c.name });
      if (r.pending) store.toast("info", "The laptop node has not answered yet. The result is applied when it does.");
      else if (r.ok) store.toast("ok", what === "disconnect" ? "Stopped." : `Connection works: ${r.connector.tools.length} tool(s) found${r.ms ? ` in ${(r.ms / 1000).toFixed(1)} s` : ""}. Nothing was sent to the service.`);
      else store.toast("err", `${ERROR_HELP[r.error_class ?? ""] ?? "The connection failed."}${r.error ? ` (${r.error})` : ""}`);
      await load();
    } catch (e) { store.toast("err", (e as Error).message); } finally { setBusy(null); }
  };
  const toggle = async (c: Connector) => {
    setBusy(c.name + "enable");
    try { await owner.post(`connectors/${c.enabled ? "disable" : "enable"}`, { name: c.name }); store.toast("ok", c.enabled ? "Disabled: no agent can use it." : "Enabled."); await load(); }
    catch (e) { store.toast("err", (e as Error).message); } finally { setBusy(null); }
  };
  const setTool = async (c: Connector, t: ConnectorTool, patch: { enabled?: boolean; risk?: ConnectorRisk }) => {
    setBusy(c.name + t.tool);
    setList((prev) => prev && prev.map((x) => x.name !== c.name ? x : {
      ...x, tools: x.tools.map((y) => y.tool !== t.tool ? y : {
        ...y, ...(patch.risk ? { risk: patch.risk } : {}),
        enabled: patch.enabled ?? (patch.risk && !["read", "write", "send"].includes(patch.risk) ? false : y.enabled),
      }),
    }));                                                    // shown at once; the kernel's answer replaces it a moment later
    try { await owner.post("connectors/tool", { name: c.name, tool: t.tool, ...patch }); await load(); }
    catch (e) { store.toast("err", (e as Error).message); await load(); } finally { setBusy(null); }
  };

  const openAdd = async () => {
    setForm({ ...BLANK }); setEditing(false); setTpl(null); setFormErr(null);
    if (!templates.length) { try { setTemplates((await api.get<{ templates: ConnectorTemplate[] }>("/api/connectors/templates")).templates); } catch { /* the form still works */ } }
  };
  const openEdit = (c: Connector) => {
    const l = local[c.name];
    setForm({ name: c.name, label: c.label, purpose: c.purpose, transport: c.transport, program: l?.command[0] ?? "", args: (l?.command.slice(1) ?? []).join("\n"),
      refs: unpairs(l?.env_refs ?? c.env_refs, "="), statics: "", endpoint: c.endpoint, secret_ref: c.secret_ref, lanePublic: c.lanes.includes("public"),
      calls: c.max_calls_per_hour, timeout: c.timeout_s, max_output: c.max_output });
    setEditing(true); setTpl(null); setFormErr(null);
  };
  const pickTemplate = (id: string) => {
    const t = templates.find((x) => x.id === id) ?? null;
    setTpl(t);
    if (!t) return;
    const d = t.definition as Record<string, unknown>;
    setForm({ name: String(d.name ?? ""), label: String(d.label ?? ""), purpose: String(d.purpose ?? ""), transport: t.transport,
      program: t.local?.command[0] ?? "", args: (t.local?.command.slice(1) ?? []).join("\n"), refs: unpairs(t.local?.env_refs ?? {}, "="),
      statics: unpairs(t.local?.env_static ?? {}, "="), endpoint: String(d.endpoint ?? ""), secret_ref: String(d.secret_ref ?? ""),
      lanePublic: Array.isArray(d.lanes) && d.lanes.includes("public"), calls: Number(d.max_calls_per_hour ?? 60), timeout: Number(d.timeout_s ?? 20), max_output: Number(d.max_output ?? 16000) });
  };
  const submit = async () => {
    if (!form) return;
    setBusy("form"); setFormErr(null);
    const definition: Record<string, unknown> = {
      name: form.name.trim(), label: form.label.trim() || form.name.trim(), purpose: form.purpose.trim(), transport: form.transport,
      lanes: form.lanePublic ? ["private", "public"] : ["private"], max_calls_per_hour: form.calls, timeout_s: form.timeout, max_output: form.max_output,
      ...(form.transport === "stdio" ? { node: "laptop", env_refs: pairs(form.refs, "=") } : { endpoint: form.endpoint.trim(), secret_ref: form.secret_ref.trim() }),
    };
    const localSpec = form.transport === "stdio"
      ? { command: [form.program.trim(), ...lines(form.args)], env_refs: pairs(form.refs, "="), env_static: pairs(form.statics, "="), timeout_s: form.timeout } : null;
    try {
      if (editing) {
        if (localSpec) await api.post("/api/connectors/local", { name: form.name, spec: localSpec });
        await owner.post("connectors/update", { definition });
      } else await api.post("/api/connectors/add", { definition, local: localSpec });
      store.toast("ok", editing ? "Saved." : "Added. Press Test connection to find its tools.");
      setForm(null); setSel(form.name); await load();
    } catch (e) { setFormErr((e as Error).message); } finally { setBusy(null); }
  };
  const remove = async () => {
    if (!removing) return;
    setBusy("remove");
    try { await api.post("/api/connectors/remove", { name: removing.name, confirm: confirmName }); store.toast("ok", "Removed (and read back)."); setRemoving(null); setConfirmName(""); setSel(null); await load(); }
    catch (e) { store.toast("err", (e as Error).message); } finally { setBusy(null); }
  };
  const runTry = async () => {
    if (!trying) return;
    setBusy("try"); setTryOut(null);
    let args: unknown;
    try { args = JSON.parse(tryArgs || "{}"); } catch { setTryOut({ text: "The arguments are not valid JSON.", ok: false }); setBusy(null); return; }
    try {
      const r = await owner.post<{ status: string; result?: { text: string }; reason?: string }>("connectors/try", { name: trying.c.name, tool: trying.t.tool, arguments: args });
      setTryOut({ text: r.result?.text ?? r.reason ?? r.status, ok: r.status === "done" });
      await load();
    } catch (e) { setTryOut({ text: (e as Error).message, ok: false }); } finally { setBusy(null); }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Connectors</h1>
          <p className="max-w-3xl text-sm text-fg3">External services and local programs JARVIS may use. You decide, tool by tool, what each one is allowed to do; the kernel enforces it, and nothing a server says about itself changes that.</p>
        </div>
        <Button kind="primary" onClick={() => void openAdd()}>Add connector</Button>
      </div>

      {err && <ErrorNote>{err}</ErrorNote>}

      <div className="flex flex-wrap items-center gap-2">
        <input aria-label="Search connectors" className={`${inputCls} !w-64`} placeholder="Search connectors" value={q} onChange={(e) => setQ(e.target.value)} />
        <div role="group" aria-label="Filter by state" className="flex flex-wrap gap-1">
          {FILTERS.map((f) => (
            <button key={f.id} aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}
              className={`rounded-full border px-3 py-1 text-xs ${filter === f.id ? "border-accent bg-accent-bg font-medium text-accent-fg" : "border-edge text-fg2 hover:bg-hover"}`}>{f.label}</button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card title={`Catalogue${list ? ` (${shown.length}${shown.length !== list.length ? ` of ${list.length}` : ""})` : ""}`}>
          {!list && !err ? <Spinner label="Loading connectors" /> :
            list && list.length === 0 ? <Empty title="No connectors yet" hint="A connector lets JARVIS use an outside service or a local program. Start with the harmless Time server from the templates." action={<Button kind="primary" onClick={() => void openAdd()}>Add connector</Button>} /> :
              shown.length === 0 ? <Empty title="Nothing matches" hint="Clear the search or the state filter." /> :
                <ul className="space-y-2">{shown.map((c) => {
                  const s = STATUS[c.status];
                  return (
                    <li key={c.name}>
                      <button onClick={() => setSel(c.name)} aria-label={`Open ${c.label}`} aria-current={sel === c.name ? "true" : undefined}
                        className={`w-full rounded-lg border px-3 py-2 text-left hover:bg-hover ${sel === c.name ? "border-accent bg-accent-bg/40" : "border-line"}`}>
                        <span className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{c.label}</span><span className="font-mono text-xs text-fg3">{c.name}</span>
                          <Badge tone={s.tone} title={s.hint}>{s.text}</Badge>
                          {c.needs_setup && <Badge tone="wait">Needs setup</Badge>}
                          {c.tools_changed > 0 && <Badge tone="err">{c.tools_changed} changed</Badge>}
                        </span>
                        <span className="mt-1 block text-xs text-fg3">{c.purpose || "No description."}</span>
                        <span className="mt-1 block text-xs text-fg3">
                          {c.transport === "stdio" ? `Runs on ${c.node}` : `Remote: ${hostOf(c.endpoint)}`} · {c.tools_enabled} of {c.tools.length} tools enabled · {c.last_test ? `tested ${ago(c.last_test)}` : "never tested"}
                        </span>
                      </button>
                    </li>
                  );
                })}</ul>}
        </Card>

        <div className="space-y-4">
          {!cur ? <Card><Empty title="Choose a connector" hint="Its state, tools and permissions appear here." /></Card> : (
            <>
              <Card title={<span className="flex flex-wrap items-center gap-2">{cur.label}<Badge tone={STATUS[cur.status].tone}>{STATUS[cur.status].text}</Badge></span>}
                actions={<>
                  <Button disabled={busy !== null || !cur.enabled} onClick={() => void act(cur, "test")}>{busy === cur.name + "test" ? "Testing…" : "Test connection"}</Button>
                  {cur.transport === "stdio" && (cur.state === "connected"
                    ? <Button disabled={busy !== null} onClick={() => void act(cur, "disconnect")}>Disconnect</Button>
                    : <Button disabled={busy !== null || !cur.enabled} onClick={() => void act(cur, "connect")}>Connect</Button>)}
                  <Button kind="ghost" disabled={busy !== null} onClick={() => void toggle(cur)}>{cur.enabled ? "Disable" : "Enable"}</Button>
                </>}>
                <p className="mb-3 text-sm text-fg2">{STATUS[cur.status].hint}</p>
                {cur.needs_setup && <div className="mb-3"><ErrorNote>{ERROR_HELP[cur.last_error_class ?? ""] ?? "This connector needs setup."}{cur.last_error ? ` (${cur.last_error})` : ""}</ErrorNote></div>}
                {!cur.needs_setup && cur.last_test_ok === 0 && <div className="mb-3"><ErrorNote>The last test failed: {ERROR_HELP[cur.last_error_class ?? ""] ?? cur.last_error ?? "unknown error"}{cur.last_error && ERROR_HELP[cur.last_error_class ?? ""] ? ` (${cur.last_error})` : ""}</ErrorNote></div>}
                <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
                  <dt className="text-fg3">Runs</dt><dd>{cur.transport === "stdio" ? `On the node "${cur.node}" (a program on this computer, started only from this computer's allowlist)` : `Remote service ${hostOf(cur.endpoint)} over https, called by the kernel`}</dd>
                  <dt className="text-fg3">Who may use it</dt><dd>{cur.lanes.map((l) => <Badge key={l} tone={l === "public" ? "wait" : "accent"}>{l} lane</Badge>)}</dd>
                  <dt className="text-fg3">Limits</dt><dd>{cur.max_calls_per_hour} calls per hour · {cur.timeout_s} s timeout · {cur.max_output.toLocaleString()} characters of output</dd>
                  <dt className="text-fg3">Server</dt><dd>{cur.server?.name ? `${cur.server.name} ${cur.server.version ?? ""} (protocol ${cur.protocol ?? "?"}, ${cur.era ?? "?"} era)` : "not yet discovered"}</dd>
                  <dt className="text-fg3">Last test</dt><dd>{cur.last_test ? `${cur.last_test_ok ? "worked" : "failed"} ${ago(cur.last_test)}${cur.last_test_ms ? `, ${(cur.last_test_ms / 1000).toFixed(1)} s` : ""}` : "never"}</dd>
                  {cur.reconnects > 0 && <><dt className="text-fg3">Restarts</dt><dd>{cur.reconnects} automatic restart(s) after a stop</dd></>}
                  {cur.transport === "stdio" && <>
                    <dt className="text-fg3">Program</dt>
                    <dd>{local[cur.name] ? <><span className="break-all font-mono text-xs">{local[cur.name].command.join(" ")}</span> <Badge tone={local[cur.name].installed ? "done" : "err"}>{local[cur.name].installed ? "installed" : "not found"}</Badge></> : <Badge tone="err">no program configured on this computer</Badge>}</dd>
                    {Object.entries(local[cur.name]?.secrets_present ?? {}).map(([k, ok]) => (
                      <div key={k} className="contents"><dt className="text-fg3">Credential</dt><dd><span className="font-mono text-xs">{k}</span> <Badge tone={ok ? "done" : "wait"}>{ok ? "secret file present" : "secret file missing"}</Badge> <span className="font-mono text-xs text-fg3">{local[cur.name].env_refs[k]}</span></dd></div>
                    ))}
                  </>}
                  {cur.transport === "https" && cur.secret_ref && <><dt className="text-fg3">Credential</dt><dd><span className="font-mono text-xs">{cur.secret_ref}</span> <span className="text-xs text-fg3">(on the kernel's computer)</span></dd></>}
                </dl>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button onClick={() => openEdit(cur)}>Edit</Button>
                  <Button kind="danger" onClick={() => { setRemoving(cur); setConfirmName(""); }}>Remove…</Button>
                </div>
              </Card>

              <Card title={`Tools (${cur.tools.length})`}>
                <p className="mb-3 text-sm text-fg3">What a server says about its own tools is shown for information only. <strong>You</strong> assign each tool a risk class, and only read, write and send tools can be switched on. Write and send tools show you the exact arguments in the Command Center and run only after you approve that exact call, once.</p>
                {cur.tools.length === 0 ? <Empty title="No tools discovered yet" hint="Press Test connection. It asks the server for its tool list and sends nothing else." /> : (
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[640px] text-left text-sm">
                      <caption className="sr-only">Tools of {cur.label} with their risk classes</caption>
                      <thead><tr className="border-b border-line text-xs text-fg3"><th scope="col" className="py-2 pr-3">Tool</th><th scope="col" className="pr-3">Your classification</th><th scope="col" className="pr-3">On</th><th scope="col">Try</th></tr></thead>
                      <tbody>{cur.tools.map((t) => (
                        <tr key={t.tool} className="border-b border-line align-top">
                          <td className="py-2 pr-3">
                            <span className="font-mono text-xs">{t.tool}</span>
                            <span className="block text-xs text-fg3">{t.description || "No description."} <em>(the server's own words)</em></span>
                            <span className="mt-1 flex flex-wrap gap-1">
                              {Object.entries(t.claimed).filter(([, v]) => v).map(([k]) => <Badge key={k} tone="idle" title="Claimed by the server. Not trusted and never used to lower a class.">claims {k.replace("Hint", "")}</Badge>)}
                              {t.risk === "unknown" && t.suggested !== "unknown" && <Badge tone="idle" title="A suggestion from the tool's name only. Nothing applies it for you.">name suggests {t.suggested}</Badge>}
                              {t.changed && <Badge tone="err" title="The server's declaration changed after you enabled it, so it was switched off.">changed: review again</Badge>}
                              {!t.present && <Badge tone="err">no longer offered</Badge>}
                            </span>
                          </td>
                          <td className="pr-3">
                            <label className="sr-only" htmlFor={`risk-${t.tool}`}>Risk class of {t.tool}</label>
                            <select id={`risk-${t.tool}`} className={`${inputCls} !w-auto`} value={t.risk} disabled={busy !== null}
                              onChange={(e) => void setTool(cur, t, { risk: e.target.value as ConnectorRisk, ...(t.enabled && !["read", "write", "send"].includes(e.target.value) ? { enabled: false } : {}) })}>
                              {RISKS.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
                            </select>
                            <span className="mt-1 block text-xs text-fg3">{RISK_NOTE[t.risk]}</span>
                          </td>
                          <td className="pr-3">
                            <label className="inline-flex items-center gap-2">
                              <input type="checkbox" checked={t.enabled} disabled={busy !== null || !t.present || !["read", "write", "send"].includes(t.risk)}
                                onChange={(e) => void setTool(cur, t, { enabled: e.target.checked })} />
                              <span className="sr-only">Enable {t.tool}</span>
                              <Badge tone={t.enabled ? riskTone(t.risk) : "idle"}>{t.enabled ? "on" : "off"}</Badge>
                            </label>
                          </td>
                          <td>{t.enabled && t.risk === "read" ? <Button onClick={() => { setTrying({ c: cur, t }); setTryArgs("{}"); setTryOut(null); }}>Try…</Button> : <span className="text-xs text-fg3">—</span>}</td>
                        </tr>))}</tbody>
                    </table>
                  </div>)}
              </Card>
            </>)}

          <Card title="Recent calls">
            {calls.length === 0 ? <Empty title="No calls yet" hint="Every call an agent makes through a connector is listed here, without its content." /> : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] text-left text-sm">
                  <caption className="sr-only">Recent connector calls</caption>
                  <thead><tr className="border-b border-line text-xs text-fg3"><th scope="col" className="py-2 pr-3">When</th><th scope="col" className="pr-3">Connector.tool</th><th scope="col" className="pr-3">Risk</th><th scope="col" className="pr-3">By</th><th scope="col" className="pr-3">Decision</th><th scope="col">Outcome</th></tr></thead>
                  <tbody>{calls.slice(0, 15).map((c, i) => (
                    <tr key={i} className="border-b border-line">
                      <td className="py-1.5 pr-3 text-xs text-fg3" title={clock(c.ts)}>{ago(c.ts)}</td>
                      <td className="pr-3 font-mono text-xs">{c.connector}.{c.tool}</td>
                      <td className="pr-3"><Badge tone={riskTone(c.risk)}>{c.risk ?? "?"}</Badge></td>
                      <td className="pr-3 text-xs">{c.agent} · {c.lane}</td>
                      <td className="pr-3 text-xs">{c.decision}</td>
                      <td className="text-xs"><Badge tone={c.outcome === "ok" ? "done" : c.outcome === "failed" ? "err" : "idle"}>{c.outcome}</Badge>{c.error_class ? ` ${c.error_class}` : ""}{c.ms ? ` · ${c.ms} ms` : ""}</td>
                    </tr>))}</tbody>
                </table>
              </div>)}
          </Card>
        </div>
      </div>

      <Dialog open={form !== null} onClose={() => setForm(null)} title={editing ? "Edit connector" : "Add connector"}
        footer={<><Button onClick={() => setForm(null)}>Cancel</Button><Button kind="primary" disabled={busy === "form" || !form?.name.trim()} onClick={() => void submit()}>{editing ? "Save" : "Add connector"}</Button></>}>
        {form && (
          <div className="space-y-3">
            {!editing && templates.length > 0 && (
              <Field label="Start from a template">
                <select className={inputCls} value={tpl?.id ?? ""} onChange={(e) => pickTemplate(e.target.value)}>
                  <option value="">Blank</option>
                  {templates.map((t) => <option key={t.id} value={t.id}>{t.title}{t.status === "tested" ? " (tested)" : " (template, not run here)"}</option>)}
                </select>
              </Field>)}
            {tpl && (
              <div className="rounded-lg border border-line bg-bg2 p-3 text-sm">
                <p className="mb-1 flex flex-wrap items-center gap-2"><Badge tone={tpl.status === "tested" ? "done" : "wait"}>{tpl.status === "tested" ? "tested on this system" : "template: not run here"}</Badge><a className="text-accent underline" href={tpl.source.url} target="_blank" rel="noreferrer">{tpl.source.project}</a> <span className="text-xs text-fg3">{tpl.source.license}</span></p>
                <p className="text-fg2">{tpl.summary}</p>
                <p className="mt-2 text-xs font-medium text-fg2">Setup</p>
                <ol className="list-decimal space-y-1 pl-5 text-xs text-fg2">{tpl.setup.map((s, i) => <li key={i}>{s}</li>)}</ol>
                {tpl.risks.length > 0 && <><p className="mt-2 text-xs font-medium text-fg2">Think about</p><ul className="list-disc space-y-1 pl-5 text-xs text-fg2">{tpl.risks.map((s, i) => <li key={i}>{s}</li>)}</ul></>}
              </div>)}
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Name" hint="Lowercase letters, digits, - and _"><input className={inputCls} value={form.name} disabled={editing} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="Label"><input className={inputCls} value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value })} /></Field>
            </div>
            <Field label="What it is for"><input className={inputCls} value={form.purpose} onChange={(e) => setForm({ ...form, purpose: e.target.value })} /></Field>
            <Field label="How it runs">
              <select className={inputCls} value={form.transport} disabled={editing} onChange={(e) => setForm({ ...form, transport: e.target.value as "stdio" | "https" })}>
                <option value="stdio">A local program on this computer (stdio)</option>
                <option value="https">A remote service over https</option>
              </select>
            </Field>
            {form.transport === "stdio" ? (
              <>
                <Field label="Program (full path)" hint="One real program. Shells, scripts and interpreters given code are refused."><input className={`${inputCls} font-mono`} value={form.program} onChange={(e) => setForm({ ...form, program: e.target.value })} /></Field>
                <Field label="Arguments (one per line)"><textarea className={`${inputCls} font-mono`} rows={3} value={form.args} onChange={(e) => setForm({ ...form, args: e.target.value })} /></Field>
                <Field label="Credentials as secret-file references (VARIABLE=connectors/file.token)" hint="The value lives in a file in the secrets folder that only you create. It is never entered or shown here.">
                  <textarea className={`${inputCls} font-mono`} rows={2} value={form.refs} onChange={(e) => setForm({ ...form, refs: e.target.value })} /></Field>
                <Field label="Plain settings (VARIABLE=value, never credentials)"><textarea className={`${inputCls} font-mono`} rows={2} value={form.statics} onChange={(e) => setForm({ ...form, statics: e.target.value })} /></Field>
              </>
            ) : (
              <>
                <Field label="Endpoint (https only)"><input className={`${inputCls} font-mono`} value={form.endpoint} onChange={(e) => setForm({ ...form, endpoint: e.target.value })} placeholder="https://example.com/mcp" /></Field>
                <Field label="Bearer token file on the kernel's computer (optional)" hint="A path inside the kernel's secrets folder, e.g. connectors/service.token"><input className={`${inputCls} font-mono`} value={form.secret_ref} onChange={(e) => setForm({ ...form, secret_ref: e.target.value })} /></Field>
              </>)}
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={form.lanePublic} onChange={(e) => setForm({ ...form, lanePublic: e.target.checked })} />
              <span>Also allow the public lane (lab agents on hosted models). Leave this off for anything personal: results would reach a public-lane model.</span>
            </label>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="Calls per hour"><input type="number" min={1} max={600} className={inputCls} value={form.calls} onChange={(e) => setForm({ ...form, calls: Number(e.target.value) })} /></Field>
              <Field label="Timeout (s)"><input type="number" min={3} max={120} className={inputCls} value={form.timeout} onChange={(e) => setForm({ ...form, timeout: Number(e.target.value) })} /></Field>
              <Field label="Max output (chars)"><input type="number" min={500} max={60000} className={inputCls} value={form.max_output} onChange={(e) => setForm({ ...form, max_output: Number(e.target.value) })} /></Field>
            </div>
            {formErr && <ErrorNote>{formErr}</ErrorNote>}
          </div>)}
      </Dialog>

      <Dialog open={removing !== null} onClose={() => setRemoving(null)} title="Remove this connector?"
        footer={<><Button onClick={() => setRemoving(null)}>Keep it</Button><Button kind="danger" disabled={busy === "remove" || confirmName !== removing?.name} onClick={() => void remove()}>Remove</Button></>}>
        <p className="mb-3 text-sm text-fg2">This forgets the connector, your tool decisions and its local program entry. The program and any secret file stay on disk.</p>
        <Field label={`Type ${removing?.name ?? ""} to confirm`}><input className={inputCls} value={confirmName} onChange={(e) => setConfirmName(e.target.value)} /></Field>
      </Dialog>

      <Dialog open={trying !== null} onClose={() => setTrying(null)} title={`Try ${trying?.t.tool ?? ""}`}
        footer={<><Button onClick={() => setTrying(null)}>Close</Button><Button kind="primary" disabled={busy === "try"} onClick={() => void runTry()}>{busy === "try" ? "Running…" : "Run this read tool"}</Button></>}>
        <p className="mb-2 text-sm text-fg2">Runs the tool once with these arguments, as you. Only read tools can be tried from here.</p>
        {trying && Object.keys(trying.t.schema.properties ?? {}).length > 0 && (
          <p className="mb-2 text-xs text-fg3">Arguments: {Object.entries(trying.t.schema.properties ?? {}).map(([k, v]) => `${k} (${v.type ?? "any"}${trying.t.schema.required?.includes(k) ? ", required" : ""})`).join(", ")}</p>)}
        <Field label="Arguments (JSON)"><textarea className={`${inputCls} font-mono`} rows={4} value={tryArgs} onChange={(e) => setTryArgs(e.target.value)} /></Field>
        {tryOut && (
          <div className="mt-3">
            <p className="mb-1 text-xs text-fg3">Result: data from an external service, shown as text. Nothing in it is acted on.</p>
            <pre aria-label="Result" className={`max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border p-2 text-xs ${tryOut.ok ? "border-line bg-bg2" : "border-err/50 bg-err-bg text-err-fg"}`}>{tryOut.text}</pre>
          </div>)}
      </Dialog>
    </div>
  );
}

function hostOf(url: string): string {
  try { return new URL(url).host; } catch { return url || "unknown"; }
}
