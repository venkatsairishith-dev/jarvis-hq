import { ActionButton, ActionForm, Alert, Card, DataTable, Inline, Page, Stack, StatusBadge, Text } from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

type Recent = { id: string; state: string; when: string; task: string }
type State = {
  console_url?: string
  ready?: boolean
  busy?: boolean | null
  kill_switch_on?: boolean
  message?: string
  recent?: Recent[]
}

const stateTone = (s: string) => (s === "done" ? "success" : s === "failed" ? "danger" : s === "running" ? "info" : "default")

export default function Panel(props: PluginSurfaceProps<State>) {
  const { actions, state, t } = props
  const send = actions.find((a) => a.id === "delegate_task") as HostedAction | undefined
  const check = actions.find((a) => a.id === "jarvis_status") as HostedAction | undefined
  const tone = state.kill_switch_on ? "danger" : state.ready ? "success" : "warning"
  const label = state.kill_switch_on
    ? t("panel.killSwitch", { defaultValue: "Kill switch on" })
    : state.ready
      ? state.busy
        ? t("panel.busy", { defaultValue: "Ready, busy" })
        : t("panel.ready", { defaultValue: "Ready" })
      : t("panel.notReady", { defaultValue: "Not ready" })

  return (
    <Page
      title={t("panel.title", { defaultValue: "JARVIS" })}
      subtitle={t("panel.subtitle", { defaultValue: "Hand real tasks to the main agent and its helpers. Approvals stay in JARVIS." })}
    >
      <Stack>
        <Card title={t("panel.status", { defaultValue: "Status" })}>
          <Stack>
            <Inline>
              <StatusBadge tone={tone} label={label} />
              <Text>{state.message || ""}</Text>
            </Inline>
            {check ? <ActionButton action={check} /> : null}
          </Stack>
        </Card>

        <Card title={t("panel.send", { defaultValue: "Give JARVIS a task" })}>
          {send ? (
            <ActionForm action={send} submitLabel={t("panel.sendButton", { defaultValue: "Hand over" })} />
          ) : (
            <Alert tone="warning" message={t("panel.noAction", { defaultValue: "The task action is not available (is the plugin running?)." })} />
          )}
        </Card>

        <Card title={t("panel.recent", { defaultValue: "Recent tasks from N.E.K.O" })}>
          {(state.recent || []).length ? (
            <DataTable
              data={state.recent || []}
              rowKey="id"
              columns={[
                { key: "when", label: t("panel.when", { defaultValue: "Time" }) },
                { key: "state", label: t("panel.state", { defaultValue: "State" }) },
                { key: "task", label: t("panel.task", { defaultValue: "Task" }) },
              ]}
            />
          ) : (
            <Text>{t("panel.noRecent", { defaultValue: "No task has been handed over from N.E.K.O yet." })}</Text>
          )}
          <Text>
            {t("panel.approvals", {
              defaultValue: "Results, approvals and the audit trail are in the JARVIS console:",
            })}{" "}
            {state.console_url || "http://127.0.0.1:8799"}
          </Text>
        </Card>
      </Stack>
    </Page>
  )
}
