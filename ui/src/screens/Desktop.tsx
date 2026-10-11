import { useCallback, useEffect, useState } from "react";
import { api, ApiError, owner } from "../lib/api";
import { store, useStore } from "../lib/store";
import { ago } from "../lib/format";
import { Badge, Button, Card, Empty, ErrorNote, Field, inputCls, Tabs } from "../components/ui";
import { usePcAction } from "../lib/useJob";

interface Ctrl {
  mode: "agent" | "owner"; since: number | null; returned_at: number | null;
  tasks: { id?: string; agent?: string; goal?: string; last?: unknown; pids?: number[]; [k: string]: unknown }[];
  recent: { time: string; agent: string; action: string; detail: string }[];
}
type Target = "agentpc" | "windows" | "nodes";

function AgentPc() {
  const hub = useStore((s) => s.health?.hub);
  const [c, setC] = useState<Ctrl | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const [shot, setShot] = useState(0);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setC(await api.get<Ctrl>("/api/desktop/state")); setErr(null); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "The Agent PC control is not answering."); setC(null); }
  }, []);
  useEffect(() => { void load(); const t = window.setInterval(() => { void load(); setShot((n) => n + 1); }, 4000); return () => window.clearInterval(t); }, [load]);

  const act = async (what: "take" | "continue") => {
    setBusy(true);
    try { setC(await api.post<Ctrl>(`/api/desktop/${what}`)); store.toast("ok", what === "take" ? "You have control: the agents are frozen where they were." : "Control handed back: the agents carry on."); }
    catch (e) { store.toast("err", (e as Error).message); }
    finally { setBusy(false); }
  };
  const ownerControls = c?.mode === "owner";
  const vnc = `${hub?.url ?? "http://hub.localhost:8088"}/pc/vnc.html?autoconnect=1&resize=scale&reconnect=1&shared=1&path=pc/websockify&view_only=${ownerControls ? 0 : 1}`;

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      <Card title="Agent PC (the agents' own Linux desktop)"
        actions={c ? <Badge tone={ownerControls ? "wait" : "run"}>{ownerControls ? "You are in control" : "Agents are in control"}</Badge> : <Badge tone="err">Not reachable</Badge>}>
        {err && <ErrorNote>{err} Nothing below is simulated: when the lab box is stopped there is no desktop to show.</ErrorNote>}
        {c && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <Button kind="primary" disabled={busy || ownerControls} onClick={() => act("take")} title="Freeze the agents' tasks and their screen drivers; you can use the desktop">Take control</Button>
              <Button disabled={busy || !ownerControls} onClick={() => act("continue")} title="Hand the computer back; each agent is told to look at the screen again">Continue task</Button>
              <Button onClick={() => setLive((v) => !v)} aria-pressed={live}>{live ? "Hide live desktop" : "Open live desktop"}</Button>
            </div>
            <p className="text-xs text-fg3">
              {ownerControls ? `You took control ${ago(c.since)}. The agents are frozen and new agent tasks are frozen too. Windows they opened stay usable.`
                : `Agents have had the computer since ${c.returned_at ? ago(c.returned_at) : "the lab started"}. The live view is view-only until you take control.`}
            </p>
            {live ? (
              <iframe title="Agent PC live desktop" src={vnc} className="h-[56vh] min-h-[320px] w-full rounded-lg border border-line bg-black" />
            ) : (
              <img alt="Latest picture of the Agent PC" key={shot} src={`/api/desktop/screen.jpg?t=${shot}`} className="aspect-video w-full rounded-lg border border-line bg-black object-contain"
                onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />
            )}
            <p className="text-xs text-fg3">A real capture of the lab desktop every few seconds. Open the live desktop for the interactive view.</p>
          </div>
        )}
      </Card>
      <div className="space-y-4">
        <Card title="Open windows and agent tasks">
          {!c ? <Empty title="No data" /> : c.tasks.length === 0 ? <Empty title="No agent tasks are running on the Agent PC" /> :
            <ul className="space-y-2 text-sm">{c.tasks.map((t, i) => (
              <li key={i} className="rounded-lg border border-line p-2"><div className="font-medium">{String(t.agent ?? "agent")}</div><div className="text-fg2">{String(t.goal ?? "")}</div>
                {t.last ? <div className="text-xs text-fg3">last: {typeof t.last === "string" ? t.last : JSON.stringify(t.last).slice(0, 120)}</div> : null}</li>))}</ul>}
          <p className="mt-3 text-xs text-fg3">Per-window lists need the desktop's window manager; this screen shows the agent tasks the control service tracks.</p>
        </Card>
        <Card title="Recent agent activity">
          {!c || c.recent.length === 0 ? <Empty title="Nothing recent" /> :
            <ul className="space-y-1 font-mono text-xs">{c.recent.slice().reverse().map((r, i) => <li key={i}><span className="text-fg3">{r.time}</span> {r.agent} · {r.action} {r.detail}</li>)}</ul>}
        </Card>
      </div>
    </div>
  );
}

function WindowsPc() {
  const act = usePcAction();
  const [folder, setFolder] = useState("Downloads");
  const [app, setApp] = useState("");
  const r = act.job?.result as Record<string, unknown> | null | undefined;
  const wins = (r?.windows as { title: string; process: string; foreground: boolean; minimized: boolean }[] | undefined) ?? [];
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="This Windows PC">
        <div className="space-y-3 text-sm">
          <p className="text-fg2">Each button is one fixed, kernel-authorized job that the laptop node runs and then <strong>checks by looking at the desktop</strong>. The result is what it observed, not what was requested.</p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={act.busy} onClick={() => void act.run({ op: "windows" })}>List open windows</Button>
            <Button disabled={act.busy} onClick={() => void act.run({ op: "screenshot" })} title="Saved to the delivered-files folder; never sent to a model">Take a screenshot</Button>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Open a known folder"><select className={inputCls} value={folder} onChange={(e) => setFolder(e.target.value)}>{["Downloads", "Documents", "Desktop", "Pictures", "Videos", "Music"].map((f) => <option key={f}>{f}</option>)}</select></Field>
            <Button disabled={act.busy} onClick={() => void act.run({ op: "open_folder", target: folder })}>Open folder</Button>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="Open an app from the Start list"><input className={inputCls} value={app} placeholder="e.g. Notepad" onChange={(e) => setApp(e.target.value)} /></Field>
            <Button disabled={act.busy || !app.trim()} onClick={() => void act.run({ op: "open_app", target: app.trim() })}>Open app</Button>
          </div>
          <p className="text-xs text-fg3">There is no live video of this PC here on purpose: it is the computer you are using. Clicking and typing into other apps always asks you first and is verified by reading the control back.</p>
        </div>
      </Card>
      <Card title="Result">
        {act.error && <ErrorNote>{act.error}</ErrorNote>}
        {!act.job && !act.error && <Empty title="Nothing run yet" hint="Pick an action. If the laptop node is offline the job waits, and expires after 30 minutes." />}
        {act.job && (
          <div className="space-y-2 text-sm">
            <div className="flex items-center gap-2"><Badge tone={act.job.state === "done" ? "done" : act.job.state === "queued" ? "wait" : act.job.state === "running" ? "run" : "err"}>{act.job.state}</Badge>
              <span className="text-fg2">{act.job.kind}</span>{act.job.state === "queued" && <span className="text-xs text-fg3">waiting for the laptop node…</span>}</div>
            {r && r.ok === false && <ErrorNote>{String(r.error ?? "failed")}{Array.isArray(r.closest) && r.closest.length ? ` (closest: ${(r.closest as string[]).join(", ")})` : ""}</ErrorNote>}
            {r && r.ok === true && "verified" in r && (
              <p><Badge tone={r.verified === "verified" ? "done" : r.verified === "unconfirmed" ? "wait" : "idle"}>{String(r.verified)}</Badge>{" "}
                {r.verified === "verified" ? `A window "${(r.window as { title: string }).title}" is on screen.` : String(r.note ?? "The open could not be confirmed by looking at the desktop.")}</p>)}
            {r && r.ok === true && typeof r.opened === "string" && <p className="text-fg2">Opened: {r.opened}</p>}
            {r && r.ok === true && typeof r.saved === "string" && <p>Saved <span className="font-mono text-xs">screens\{String(r.saved)}</span> ({String(r.width)}×{String(r.height)}). Find it under Workspace, Delivered.</p>}
            {wins.length > 0 && <ul className="max-h-72 space-y-1 overflow-auto">{wins.map((w, i) => (
              <li key={i} className="flex items-center gap-2"><Badge tone={w.foreground ? "accent" : "idle"}>{w.foreground ? "foreground" : w.minimized ? "minimized" : "open"}</Badge><span className="truncate">{w.title}</span><span className="ml-auto shrink-0 text-xs text-fg3">{w.process}</span></li>))}</ul>}
          </div>)}
      </Card>
    </div>
  );
}

function Nodes() {
  const [m, setM] = useState<{ nodes: { name: string; online: boolean; seconds_since_report: number }[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { owner.get<{ nodes: { name: string; online: boolean; seconds_since_report: number }[] }>("models").then(setM).catch((e: Error) => setErr(e.message)); }, []);
  return (
    <Card title="Nodes and authorized remote machines">
      {err && <ErrorNote>{err}</ErrorNote>}
      {!m ? null : m.nodes.length === 0 ? <Empty title="No node has reported yet" /> :
        <ul className="divide-y divide-line">{m.nodes.map((n) => (<li key={n.name} className="flex items-center gap-3 py-2 text-sm"><Badge tone={n.online ? "done" : "err"}>{n.online ? "online" : "offline"}</Badge><span className="font-medium">{n.name}</span><span className="num ml-auto text-xs text-fg3">last report {n.seconds_since_report}s ago</span></li>))}</ul>}
      <p className="mt-3 text-xs text-fg3">Nodes connect outward to the kernel; the kernel never dials a machine. Adding a VM or a remote node means pairing it at the kernel console.</p>
    </Card>
  );
}

export function Desktop() {
  const [t, setT] = useState<Target>("agentpc");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Agentic OS</h1>
        <Tabs value={t} onChange={setT} items={[{ id: "agentpc", label: "Agent PC (Linux lab)" }, { id: "windows", label: "This Windows PC" }, { id: "nodes", label: "Nodes & VMs" }]} />
      </div>
      {t === "agentpc" ? <AgentPc /> : t === "windows" ? <WindowsPc /> : <Nodes />}
    </div>
  );
}
