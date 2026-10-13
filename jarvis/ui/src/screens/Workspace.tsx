import { useEffect, useMemo, useState } from "react";
import { api } from "../lib/api";
import { bytes, ago } from "../lib/format";
import type { FileRow } from "../lib/types";
import { Badge, Button, Card, Empty, ErrorNote, inputCls, Tabs } from "../components/ui";

interface Listing { delivered: FileRow[]; root: string; workspace: { available: boolean; files: FileRow[]; why?: string; temporary?: boolean } | null }
type Area = "delivered" | "workspace";

export function Workspace() {
  const [data, setData] = useState<Listing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [area, setArea] = useState<Area>("delivered");
  const [q, setQ] = useState("");
  const [sel, setSel] = useState<FileRow | null>(null);
  const [source, setSource] = useState(false);
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    api.get<Listing>(`/api/files${area === "workspace" ? "?workspace=1" : ""}`).then(setData).catch((e: Error) => setErr(e.message));
  }, [area]);

  const rows = useMemo(() => {
    const list = area === "delivered" ? data?.delivered ?? [] : data?.workspace?.files ?? [];
    return list.filter((f) => !q || f.path.toLowerCase().includes(q.toLowerCase()));
  }, [data, area, q]);

  const url = (f: FileRow, extra = "") => `/api/files/${f.area === "workspace" ? "workspace" : "get"}?path=${encodeURIComponent(f.path)}${f.area === "workspace" ? "&area=workspace" : ""}${extra}`;

  useEffect(() => {
    setText(null);
    if (!sel || sel.kind === "image" || (sel.kind === "html" && !source)) return;
    fetch(url(sel)).then(async (r) => (r.ok ? r.text() : `Could not read the file (HTTP ${r.status}).`)).then((t) => setText(t.slice(0, 200000)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, source]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-lg font-semibold">Workspace and deliverables</h1>
        <Tabs value={area} onChange={(a) => { setArea(a); setSel(null); }} items={[{ id: "delivered", label: "Delivered (verified)" }, { id: "workspace", label: "Lab workspace (temporary)" }]} />
      </div>
      <p className="text-sm text-fg3">
        {area === "delivered" ? `Files that passed independent verification and were written to ${data?.root ?? "the outputs folder"} by the kernel's delivery.`
          : "Scratch files agents are working on inside the lab box. They are not delivered, may be wrong, and can disappear."}
      </p>
      {err && <ErrorNote>{err}</ErrorNote>}
      {area === "workspace" && data?.workspace && !data.workspace.available && <ErrorNote>{data.workspace.why ?? "The lab workspace cannot be read right now."}</ErrorNote>}
      <input aria-label="Filter files" placeholder="Filter by name or task folder" className={inputCls} value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <Card>
          {!data ? <p className="text-sm text-fg3">Loading…</p> : rows.length === 0 ? <Empty title="No files" hint={area === "delivered" ? "Nothing has been delivered yet." : "No agent files at the moment."} /> :
            <ul className="divide-y divide-line">{rows.map((f) => (
              <li key={`${f.area}:${f.path}`}>
                <button onClick={() => { setSel(f); setSource(false); }} className={`flex w-full items-center gap-2 px-1 py-2 text-left text-sm hover:bg-hover ${sel?.path === f.path ? "bg-accent-bg" : ""}`}>
                  <Badge tone={f.area === "delivered" ? "done" : "wait"}>{f.kind}</Badge><span className="min-w-0 flex-1 truncate" title={f.path}>{f.path}</span>
                  <span className="num shrink-0 text-xs text-fg3">{bytes(f.bytes)} · {ago(f.modified)}</span>
                </button>
              </li>))}</ul>}
        </Card>
        <Card title={sel ? sel.name : "Preview"} actions={sel && <>
          {sel.kind === "html" && <Button onClick={() => setSource((s) => !s)}>{source ? "Show rendered" : "Show source"}</Button>}
          <a className="inline-flex min-h-9 items-center rounded-lg border border-edge bg-surface px-3 text-sm hover:bg-hover" href={url(sel, "&download=1")}>Download</a></>}>
          {!sel ? <Empty title="Select a file" hint="Previews never run scripts: HTML is shown without scripts, everything else as plain text or an image." /> :
            sel.kind === "image" ? <img alt={sel.name} src={url(sel)} className="max-h-[60vh] max-w-full rounded-lg border border-line" /> :
            sel.kind === "html" && !source ? <iframe title={`Preview of ${sel.name}`} src={url(sel)} sandbox="" className="h-[60vh] w-full rounded-lg border border-line bg-white" /> :
            sel.kind === "other" ? <p className="text-sm text-fg2">No safe preview for this file type. Use Download to open it yourself.</p> :
            <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-bg2 p-3 font-mono text-xs">{text ?? "Loading…"}</pre>}
          {sel && <p className="mt-2 text-xs text-fg3">{sel.area === "delivered" ? "Delivered and verified." : "Temporary lab file, not verified."} {bytes(sel.bytes)}, modified {ago(sel.modified)}.</p>}
        </Card>
      </div>
    </div>
  );
}
