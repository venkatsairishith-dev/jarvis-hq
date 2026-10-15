import { ActionButton, Alert, Button, Card, DataTable, Field, Inline, Input, Page, Select, Stack, StatusBadge, Text, useEffect, useForm, useToast } from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

// The panel runs in a sandboxed frame that does not allow native form submission, so the task is sent with a plain button and
// props.api.call (the same pattern the other bundled panels use), never with a <form> submit.
const MODES = ["chat", "work", "code", "learn"]
function unwrap(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

type Step = { id: string; agent: string; goal: string; state: string; error?: string; attempts?: number; held?: boolean; waited?: number; effect?: string }
type Run = {
  id: string; state: string; summary: string; task: string; mode?: string; steps: Step[]
  waited_s?: number; held?: string[]; resumable?: boolean
}
type State = { active?: boolean; current?: Run | null; history?: { id: string; state: string; summary: string }[] }

// Four step outcomes: success, failure (did not work), unknown (may or may not have happened), not_executed (never started).
const tone = (s: string) =>
  s === "done" || s === "success" ? "success"
    : s === "failed" || s === "failure" ? "danger"
      : s === "unknown" || s === "held" || s === "interrupted" || s === "partial" ? "warning"
        : s === "running" ? "info" : "default"

export default function Panel(props: PluginSurfaceProps<State>) {
  const { actions, state, t } = props
  const run = actions.find((a) => a.id === "run_task") as HostedAction | undefined
  const stop = actions.find((a) => a.id === "stop_task") as HostedAction | undefined
  const cur = state.current || null
  const form = useForm<{ task: string; mode: string }>({ task: "", mode: "work" })
  const toast = useToast()

  // While a task is running the panel refreshes itself every few seconds, so the steps and the final state appear without a reload.
  useEffect(() => {
    if (!state.active) return undefined
    const timer = setInterval(() => { void props.api.refresh() }, 4000)
    return () => clearInterval(timer)
  }, [state.active])

  async function start() {
    const task = form.values.task.trim()
    if (!run) return
    if (!task) {
      toast.error(t("panel.needTask", { defaultValue: "Type the task first." }))
      return
    }
    try {
      const res = unwrap(await props.api.call(run.id, { task, mode: form.values.mode || "work" }))
      if (res.error || res.status === "failed") throw new Error(String(res.error || res.message || "The task was not started."))
      form.setField("task", "")
      toast.success(String(res.message || t("panel.started", { defaultValue: "Started." })))
      await props.api.refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <Page
      title={t("panel.title", { defaultValue: "Main Agent" })}
      subtitle={t("panel.subtitle", { defaultValue: "One task in, the right helpers chosen, one answer out." })}
    >
      <Stack>
        <Card title={t("panel.new", { defaultValue: "New task" })}>
          {run ? (
            <Stack>
              <Field label={t("panel.taskLabel", { defaultValue: "The task, in the user's own words with their details." })}>
                <Input
                  value={form.values.task}
                  placeholder={t("panel.taskPlaceholder", { defaultValue: "For example: find the capital of Italy and save it to a document" })}
                  onChange={(value) => form.setField("task", String(value))}
                />
              </Field>
              <Field label={t("panel.modeLabel", { defaultValue: "chat (no tools), work (tools), code (JARVIS lab agents in the bound project), learn (study vault and documents)." })}>
                <Select value={form.values.mode} options={MODES} onChange={(value) => form.setField("mode", String(value))} />
              </Field>
              <Button tone="primary" onClick={start}>{t("panel.start", { defaultValue: "Plan and run" })}</Button>
            </Stack>
          ) : (
            <Alert tone="warning" message={t("panel.noAction", { defaultValue: "The task action is not available (is the plugin running?)." })} />
          )}
        </Card>

        <Card title={t("panel.current", { defaultValue: "Current task" })}>
          {cur ? (
            <Stack>
              <Inline>
                <StatusBadge tone={tone(cur.state)} label={cur.state} />
                {cur.mode ? <StatusBadge tone="info" label={cur.mode} /> : null}
                <Text>{cur.summary}</Text>
              </Inline>
              {cur.held && cur.held.length ? (
                <Alert
                  tone="warning"
                  message={t("panel.heldAlert", {
                    defaultValue: "Held back: a step may or may not have happened, so it was not retried and nothing after it ran. Check, then resume with the step id to run it again.",
                  }) + " (" + cur.held.join(", ") + ")"}
                />
              ) : cur.state === "interrupted" ? (
                <Alert tone="warning" message={t("panel.interrupted", { defaultValue: "This task was interrupted when the app stopped. Finished steps were kept. Resume it to continue." })} />
              ) : null}
              <DataTable
                data={cur.steps}
                rowKey="id"
                columns={[
                  { key: "id", label: t("panel.step", { defaultValue: "Step" }) },
                  { key: "agent", label: t("panel.agent", { defaultValue: "Helper" }) },
                  { key: "state", label: t("panel.outcome", { defaultValue: "Outcome" }) },
                  { key: "attempts", label: t("panel.attempts", { defaultValue: "Tries" }) },
                  { key: "goal", label: t("panel.goal", { defaultValue: "Goal" }) },
                  { key: "error", label: t("panel.problem", { defaultValue: "Problem" }) },
                ]}
              />
              {cur.waited_s ? (
                <Text>{t("panel.waited", { defaultValue: "Time spent waiting for you (not counted against the time limit):" })} {cur.waited_s}s</Text>
              ) : null}
              {state.active && stop ? <ActionButton action={stop} /> : null}
            </Stack>
          ) : (
            <Text>{t("panel.none", { defaultValue: "Nothing has run yet." })}</Text>
          )}
        </Card>

        <Card title={t("panel.history", { defaultValue: "Earlier tasks" })}>
          {(state.history || []).length ? (
            <DataTable
              data={state.history || []}
              rowKey="id"
              columns={[
                { key: "state", label: t("panel.state", { defaultValue: "State" }) },
                { key: "summary", label: t("panel.summary", { defaultValue: "Summary" }) },
              ]}
            />
          ) : (
            <Text>{t("panel.noHistory", { defaultValue: "No earlier tasks." })}</Text>
          )}
        </Card>
      </Stack>
    </Page>
  )
}
