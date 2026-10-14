import { ActionButton, ActionForm, Alert, Card, DataTable, Inline, Page, Stack, StatusBadge, Text } from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type Step = { id: string; agent: string; goal: string; state: string; error?: string }
type Run = { id: string; state: string; summary: string; task: string; mode?: string; steps: Step[] }
type State = { active?: boolean; current?: Run | null; history?: { id: string; state: string; summary: string }[] }

const tone = (s: string) =>
  s === "done" ? "success" : s === "failed" ? "danger" : s === "partial" || s === "skipped" ? "warning" : s === "running" ? "info" : "default"

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
              <DataTable
                data={cur.steps}
                rowKey="id"
                columns={[
                  { key: "id", label: t("panel.step", { defaultValue: "Step" }) },
                  { key: "agent", label: t("panel.agent", { defaultValue: "Helper" }) },
                  { key: "state", label: t("panel.state", { defaultValue: "State" }) },
                  { key: "goal", label: t("panel.goal", { defaultValue: "Goal" }) },
                  { key: "error", label: t("panel.problem", { defaultValue: "Problem" }) },
                ]}
              />
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
