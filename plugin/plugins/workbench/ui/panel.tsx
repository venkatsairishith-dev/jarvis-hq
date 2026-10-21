import {
  Alert, Button, Card, DataTable, Field, Inline, Input, Markdown, Page, Select, Stack, StatusBadge, Tabs, Text, Textarea,
  useClipboard, useConfirm, useForm, useState, useToast,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

// Sandboxed frame: no native form submit; every action goes through props.api.call (same pattern as the other panels).
// A generated prompt is a SUGGESTION until "Run" is pressed and confirmed; a run is "submitted", never "done", until its
// result is checked. The panel shows both states separately.
const KINDS = ["single", "multi_agent", "research", "coding", "client_deliverable"]
const ROUTES = ["auto", "main_agent", "jarvis"]

function unwrap(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    if (envelope.data && typeof envelope.data === "object") return envelope.data
    return envelope
  }
  return {}
}
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean)

type State = {
  projects?: { id: string; name: string; client: string; sources: number }[]
  prompts?: { id: string; objective: string; kind: string; versions: number; status: string; executions: number }[]
  workflows?: { name: string; current: string | null; task: string; versions: number }[]
  local_model?: boolean
  local_model_note?: string
}

export default function Panel(props: PluginSurfaceProps<State>) {
  const { state, t } = props
  const toast = useToast()
  const confirm = useConfirm()
  const clip = useClipboard()
  const gen = useForm({ objective: "", kind: "single", target_agent: "jarvis", target_model: "", constraints: "", exclusions: "",
    budget: "", deadline: "", output_format: "", acceptance: "", project_id: "", source_paths: "" })
  const [current, setCurrent] = useState<Record<string, any> | null>(null)
  const [draft, setDraft] = useState("")
  const [route, setRoute] = useState("auto")
  const proj = useForm({ name: "", client: "", owner_names: "", paths: "", project_id: "", answers: "" })
  const [projResult, setProjResult] = useState<Record<string, any> | null>(null)
  const learnForm = useForm({ task: "", examples: "", import_path: "" })
  const [title, setTitle] = useState("")
  const [compareOut, setCompareOut] = useState<Record<string, any> | null>(null)
  const [skillOut, setSkillOut] = useState<Record<string, any> | null>(null)
  const [brief, setBrief] = useState<string>("")
  const [learnResult, setLearnResult] = useState<Record<string, any> | null>(null)

  async function call(id: string, args: Record<string, any>) {
    const res = unwrap(await props.api.call(id, args, { userInitiated: true }))
    if (res.error) throw new Error(String(res.error))
    return res
  }
  const guard = (fn: () => Promise<void>) => async () => {
    try { await fn() } catch (err) { toast.error(err instanceof Error ? err.message : String(err)) }
  }

  const generate = guard(async () => {
    const v = gen.values
    if (!v.objective.trim()) throw new Error(t("panel.needObjective", { defaultValue: "Write the objective first." }))
    const res = await call("generate_prompt", {
      objective: v.objective, kind: v.kind, target: { agent: v.target_agent, model: v.target_model },
      constraints: lines(v.constraints), exclusions: lines(v.exclusions), budget: v.budget, deadline: v.deadline,
      output_format: v.output_format, acceptance: lines(v.acceptance), project_id: v.project_id || undefined,
      source_paths: lines(v.source_paths),
    })
    await open(res.id, v.project_id)
    toast.success(t("panel.saved", { defaultValue: "Saved as a suggestion. Nothing has run." }))
    await props.api.refresh()
  })
  async function open(id: string, projectId = "") {
    const res = await call("get_prompt", { prompt_id: id, project_id: projectId || undefined })
    setCurrent({ ...res, project_id: projectId })
    setDraft(String(res.text || ""))
  }
  const saveEdit = guard(async () => {
    if (!current) return
    await call("edit_prompt", { prompt_id: current.id, project_id: current.project_id || undefined, text: draft })
    await open(current.id, current.project_id)
    toast.success(t("panel.edited", { defaultValue: "Saved as a new version." }))
  })
  const regenerate = guard(async () => {
    if (!current) return
    await call("regenerate_prompt", { prompt_id: current.id, project_id: current.project_id || undefined })
    await open(current.id, current.project_id)
  })
  const copy = guard(async () => {
    if (!current) return
    const ok = await clip.write(draft)
    toast[ok ? "success" : "error"](ok ? t("panel.copied", { defaultValue: "Copied." }) : t("panel.copyFailed", { defaultValue: "Could not copy." }))
  })
  const rename = guard(async () => {
    if (!current || !title.trim()) return
    await call("rename_prompt", { prompt_id: current.id, project_id: current.project_id || undefined, title })
    await open(current.id, current.project_id)
    await props.api.refresh()
  })
  const duplicate = guard(async () => {
    if (!current) return
    const res = await call("duplicate_prompt", { prompt_id: current.id, project_id: current.project_id || undefined })
    await open(res.id, current.project_id)
    await props.api.refresh()
  })
  const compare = guard(async () => {
    if (!current || Number(current.version) < 2) throw new Error("Only one version so far.")
    setCompareOut(await call("compare_prompt", { prompt_id: current.id, project_id: current.project_id || undefined, a: Number(current.version) - 1, b: Number(current.version) }))
  })
  const exportPrompt = guard(async () => {
    if (!current) return
    const res = await call("export_prompt", { prompt_id: current.id, project_id: current.project_id || undefined })
    toast.success(`Exported: ${res.file}`)
  })
  const skillAction = (id: string, extra: Record<string, any> = {}) => guard(async () => {
    const name = String(extra.name || "")
    if (["enable_skill", "rollback_skill"].includes(id)) {
      const yes = await confirm({ title: "Change what can run?", message: `${id.replace("_", " ")}: ${name}`, tone: "danger" })
      if (!yes) return
      extra = { ...extra, confirm: true }
    }
    setSkillOut(await call(id, extra))
    await props.api.refresh()
  })
  const importSkill = guard(async () => {
    const path = learnForm.values.import_path.trim()
    if (!path) throw new Error("Give the full path of the SKILL.md.")
    const yes = await confirm({ title: "Import as untrusted?", message: "It is rated and stored quarantined; it is never activated here.", tone: "danger" })
    if (!yes) return
    setSkillOut(await call("import_skill", { path, confirm: true }))
  })
  const loadBriefing = guard(async () => {
    const res = await call("briefing", {})
    setBrief(String(res.text || ""))
  })
  const execute = guard(async () => {
    if (!current) return
    const yes = await confirm({ title: t("panel.runTitle", { defaultValue: "Run this prompt?" }), tone: "danger",
      message: t("panel.runMsg", { defaultValue: "JARVIS will act on it. Its approvals and the kill switch still apply. The run is reported as submitted, not done." }) })
    if (!yes) return
    const res = await call("execute_prompt", { prompt_id: current.id, project_id: current.project_id || undefined, via: route, confirm: true })
    toast[res.state === "submitted" ? "success" : "error"](String(res.message || res.state))
    await open(current.id, current.project_id)
  })

  const createProject = guard(async () => {
    const v = proj.values
    const res = await call("create_project", { name: v.name, client: v.client, owner_names: lines(v.owner_names.replace(/,/g, "\n")) })
    proj.setField("project_id", res.project_id)
    toast.success(`Project ${res.project_id}`)
    await props.api.refresh()
  })
  const addFiles = guard(async () => {
    setProjResult(await call("add_project_files", { project_id: proj.values.project_id, paths: lines(proj.values.paths) }))
  })
  const analyze = guard(async () => { setProjResult(await call("analyze_project", { project_id: proj.values.project_id })) })
  const answer = guard(async () => {
    const answers: Record<string, string> = {}
    for (const ln of lines(proj.values.answers)) { const m = ln.match(/^(Q\d+)\s*[:=]\s*(.+)$/); if (m) answers[m[1]] = m[2] }
    setProjResult(await call("answer_questions", { project_id: proj.values.project_id, answers }))
  })
  const build = guard(async () => { setProjResult(await call("build_deliverables", { project_id: proj.values.project_id })) })

  const learn = guard(async () => {
    let examples: any[] = []
    if (learnForm.values.examples.trim()) examples = JSON.parse(learnForm.values.examples)
    setLearnResult(await call("learn_task", { task: learnForm.values.task, examples }))
    await props.api.refresh()
  })

  const statusTone = (s: string) => s === "verified" || s === "validated" ? "success" : s === "needs review" || s === "suggestion" ? "warning"
    : s === "submitted" || s === "submitted for execution" ? "info" : s === "refused" || s === "failed" || s === "not validated" ? "danger" : "default"

  const promptTab = (
    <Stack>
      <Card title={t("panel.newPrompt", { defaultValue: "New prompt" })}>
        <Stack>
          <Field label={t("panel.objective", { defaultValue: "Objective (plain language)" })}>
            <Textarea value={gen.values.objective} onChange={(x) => gen.setField("objective", x)} />
          </Field>
          <Inline>
            <Field label="Kind"><Select value={gen.values.kind} options={KINDS} onChange={(x) => gen.setField("kind", String(x))} /></Field>
            <Field label="Target agent / tool"><Input value={gen.values.target_agent} onChange={(x) => gen.setField("target_agent", x)} /></Field>
            <Field label="Target model (empty = JARVIS decides)"><Input value={gen.values.target_model} onChange={(x) => gen.setField("target_model", x)} /></Field>
          </Inline>
          <Field label="Constraints (one per line)"><Textarea value={gen.values.constraints} onChange={(x) => gen.setField("constraints", x)} /></Field>
          <Field label="Do not (one per line)"><Textarea value={gen.values.exclusions} onChange={(x) => gen.setField("exclusions", x)} /></Field>
          <Inline>
            <Field label="Budget"><Input value={gen.values.budget} onChange={(x) => gen.setField("budget", x)} /></Field>
            <Field label="Deadline"><Input value={gen.values.deadline} onChange={(x) => gen.setField("deadline", x)} /></Field>
            <Field label="Output format"><Input value={gen.values.output_format} onChange={(x) => gen.setField("output_format", x)} /></Field>
          </Inline>
          <Field label="Acceptance criteria (one per line)"><Textarea value={gen.values.acceptance} onChange={(x) => gen.setField("acceptance", x)} /></Field>
          <Field label="Source files (full paths, one per line)"><Textarea value={gen.values.source_paths} onChange={(x) => gen.setField("source_paths", x)} /></Field>
          <Field label="Client project id (optional)"><Input value={gen.values.project_id} onChange={(x) => gen.setField("project_id", x)} /></Field>
          <Button tone="primary" onClick={generate}>{t("panel.generate", { defaultValue: "Generate (does not run)" })}</Button>
        </Stack>
      </Card>
      {current ? (
        <Card title={`${current.id} v${current.version}`}>
          <Stack>
            <Inline>
              <StatusBadge tone={statusTone(String(current.status))} label={String(current.status)} />
              <Text>{`${current.title || ""} · ${current.kind} · ${current.versions} version(s) · ${(current.executions || []).length} run(s)`}</Text>
            </Inline>
            <Text>{`Target: ${current.target?.runtime || "-"} → ${current.target?.route || "-"} (${current.target?.description || ""})`}</Text>
            <Text>{`Side effects: ${(current.target?.side_effects || []).join("; ") || "none listed"} · From requirements: ${(current.provenance?.requirements || []).join(", ") || "none"}`}</Text>
            <Inline>
              <Input value={title} placeholder="New title" onChange={(x) => setTitle(x)} />
              <Button onClick={rename}>Rename</Button>
              <Button onClick={duplicate}>Duplicate</Button>
              <Button onClick={compare}>Compare with previous</Button>
              <Button onClick={exportPrompt}>Export</Button>
            </Inline>
            {compareOut ? <Markdown source={(compareOut.summary || []).map((x: string) => "- " + x).join("\n") + "\n\n```diff\n" + String(compareOut.diff || "") + "\n```"} /> : null}
            <Textarea value={draft} onChange={(x) => setDraft(x)} />
            <Inline>
              <Button onClick={copy}>{t("panel.copy", { defaultValue: "Copy" })}</Button>
              <Button onClick={saveEdit}>{t("panel.saveEdit", { defaultValue: "Save edit as new version" })}</Button>
              <Button onClick={regenerate}>{t("panel.regenerate", { defaultValue: "Regenerate" })}</Button>
              <Select value={route} options={ROUTES} onChange={(x) => setRoute(String(x))} />
              <Button tone="danger" onClick={execute}>{t("panel.run", { defaultValue: "Run (asks first)" })}</Button>
            </Inline>
            {(current.executions || []).length ? (
              <DataTable data={current.executions} rowKey="at" columns={[
                { key: "version", label: "Version" }, { key: "via", label: "Via" }, { key: "state", label: "State" },
                { key: "ref", label: "Reference" }, { key: "verified", label: "Verified" }]} />
            ) : <Text>{t("panel.notRun", { defaultValue: "Not run: this is a suggestion." })}</Text>}
          </Stack>
        </Card>
      ) : null}
      <Card title={t("panel.saved", { defaultValue: "Saved prompts" })}>
        <DataTable data={state.prompts || []} rowKey="id" columns={[
          { key: "id", label: "Id", render: (r: any) => <Button onClick={guard(() => open(r.id))}>{r.id}</Button> },
          { key: "kind", label: "Kind" }, { key: "status", label: "Status" }, { key: "versions", label: "Versions" },
          { key: "objective", label: "Objective" }]} />
      </Card>
    </Stack>
  )

  const projectTab = (
    <Stack>
      <Card title={t("panel.newProject", { defaultValue: "New client project" })}>
        <Inline>
          <Field label="Project name"><Input value={proj.values.name} onChange={(x) => proj.setField("name", x)} /></Field>
          <Field label="Client"><Input value={proj.values.client} onChange={(x) => proj.setField("client", x)} /></Field>
          <Field label="Your name(s) in the chats"><Input value={proj.values.owner_names} onChange={(x) => proj.setField("owner_names", x)} /></Field>
          <Button tone="primary" onClick={createProject}>Create</Button>
        </Inline>
      </Card>
      <Card title={t("panel.work", { defaultValue: "Work on a project" })}>
        <Stack>
          <Field label="Project id"><Select value={proj.values.project_id}
            options={(state.projects || []).map((p) => ({ value: p.id, label: `${p.name} (${p.sources} files)` }))}
            onChange={(x) => proj.setField("project_id", String(x))} /></Field>
          <Field label="Files to add (full paths, one per line)"><Textarea value={proj.values.paths} onChange={(x) => proj.setField("paths", x)} /></Field>
          <Inline>
            <Button onClick={addFiles}>Add files</Button>
            <Button tone="primary" onClick={analyze}>Understand the client's needs</Button>
          </Inline>
          <Field label="Answers (Q1: answer, one per line)"><Textarea value={proj.values.answers} onChange={(x) => proj.setField("answers", x)} /></Field>
          <Inline>
            <Button onClick={answer}>Save answers</Button>
            <Button onClick={build}>Make and check deliverables</Button>
          </Inline>
          {projResult ? <Markdown source={"```\n" + JSON.stringify(projResult, null, 1) + "\n```"} /> : null}
        </Stack>
      </Card>
    </Stack>
  )

  const learnTab = (
    <Stack>
      {!state.local_model ? <Alert tone="info" message={`Local model: ${state.local_model_note || "not available"}. Learning still runs with a workflow you give.`} /> : null}
      <Card title={t("panel.learn", { defaultValue: "Learn a new task" })}>
        <Stack>
          <Field label="Task"><Textarea value={learnForm.values.task} onChange={(x) => learnForm.setField("task", x)} /></Field>
          <Field label='Examples (JSON: [{"name","inputs":{"file":"text"},"checks":[{"check":"equals","file":"out.txt","text":"..."}]}])'>
            <Textarea value={learnForm.values.examples} onChange={(x) => learnForm.setField("examples", x)} />
          </Field>
          <Button tone="primary" onClick={learn}>Learn (sandbox only)</Button>
          {learnResult ? (
            <Stack>
              <StatusBadge tone={statusTone(String(learnResult.status))} label={String(learnResult.status)} />
              <Text>{String(learnResult.why || "")}</Text>
              <Markdown source={"```\n" + JSON.stringify(learnResult, null, 1) + "\n```"} />
            </Stack>
          ) : null}
        </Stack>
      </Card>
      <Card title="Learned skills">
        <Stack>
          <DataTable data={state.workflows || []} rowKey="name" columns={[
            { key: "name", label: "Name" }, { key: "current", label: "Validated version" }, { key: "task", label: "Task" },
            { key: "actions", label: "", render: (r: any) => (
              <Inline>
                <Button onClick={skillAction("inspect_skill", { name: r.name })}>Inspect</Button>
                <Button onClick={skillAction("regress_skill", { name: r.name })}>Re-test</Button>
                <Button onClick={skillAction("disable_skill", { name: r.name, reason: "disabled from the panel" })}>Disable</Button>
                <Button onClick={skillAction("enable_skill", { name: r.name })}>Enable</Button>
                <Button onClick={skillAction("export_skill", { name: r.name })}>Export</Button>
              </Inline>) }]} />
          <Inline>
            <Input value={learnForm.values.import_path} placeholder="Full path of an external SKILL.md" onChange={(x) => learnForm.setField("import_path", x)} />
            <Button onClick={importSkill}>Import (untrusted)</Button>
          </Inline>
          {skillOut ? <Markdown source={"```\n" + JSON.stringify(skillOut, null, 1).slice(0, 6000) + "\n```"} /> : null}
        </Stack>
      </Card>
    </Stack>
  )

  return (
    <Page title={t("panel.title", { defaultValue: "Workbench" })}
      subtitle={t("panel.subtitle", { defaultValue: "Prompts, client projects and new skills. Nothing runs without your OK." })}>
      <Tabs items={[{ id: "briefing", label: "Briefing", content: (
          <Card title="What is pending (facts with sources; suggestions marked)">
            <Stack>
              <Button tone="primary" onClick={loadBriefing}>Refresh briefing</Button>
              {brief ? <Markdown source={"```\n" + brief + "\n```"} /> : <Text>Press refresh to read the records.</Text>}
            </Stack>
          </Card>) },
        { id: "prompts", label: "Prompt Generator", content: promptTab },
        { id: "projects", label: "Client projects", content: projectTab },
        { id: "learn", label: "Learn", content: learnTab }]} />
    </Page>
  )
}
