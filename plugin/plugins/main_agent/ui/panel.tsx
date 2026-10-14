import { ActionButton, ActionForm, Alert, Card, DataTable, Inline, Page, Stack, StatusBadge, Text } from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

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

  return (
    <Page
      title={t("panel.title", { defaultValue: "Main Agent" })}
      subtitle={t("panel.subtitle", { defaultValue: "One task in, the right helpers chosen, one answer out." })}
    >
      <Stack>
        <Card title={t("panel.new", { defaultValue: "New task" })}>
          {run ? (
            <ActionForm action={run} submitLabel={t("panel.start", { defaultValue: "Plan and run" })} />
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
