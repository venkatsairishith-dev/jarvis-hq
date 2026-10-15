import { ActionButton, Alert, Button, Card, DataTable, Field, Inline, Input, Page, Stack, StatusBadge, Text, useForm, useToast } from "@neko/plugin-ui"
import type { HostedAction, PluginSurfaceProps } from "@neko/plugin-ui"

// The panel runs in a sandboxed frame that does not allow native form submission, so the task is sent with a plain button and
// props.api.call (the same pattern the other bundled panels use), never with a <form> submit.
function unwrap(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

type Recent = { id: string; state: string; when: string; task: string }
type Approval = { id: string; level: string; what: string; seconds_left: number; reason: string }
type Connector = { name: string; label: string; status: string; enabled: boolean; transport: string; tools_enabled: number }
type State = {
  console_url?: string
  ready?: boolean
  busy?: boolean | null
  kill_switch_on?: boolean
  message?: string
  recent?: Recent[]
  approvals?: Approval[]
  connectors?: Connector[]
}

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
  const approvals = state.approvals || []
  const connectors = state.connectors || []
  const console_url = state.console_url || "http://127.0.0.1:8799"
  const form = useForm<{ task: string }>({ task: "" })
  const toast = useToast()

  async function handOver() {
    const task = form.values.task.trim()
    if (!send) return
    if (!task) {
      toast.error(t("panel.needTask", { defaultValue: "Type the task first." }))
      return
    }
    try {
      const res = unwrap(await props.api.call(send.id, { task }))
      if (res.error || res.status === "failed") throw new Error(String(res.error || res.message || "JARVIS did not accept the task."))
      form.setField("task", "")
      toast.success(String(res.message || t("panel.handedOver", { defaultValue: "Handed over to JARVIS." })))
      await props.api.refresh()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

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

        <Card title={t("panel.approvals", { defaultValue: "Waiting for your approval" })}>
          <Stack>
            {approvals.length ? (
              <>
                <Alert
                  tone="warning"
                  message={t("panel.approvalsAlert", { defaultValue: "JARVIS is waiting for you. Approve or deny in the JARVIS console (it needs your PIN):" }) + " " + console_url}
                />
                <DataTable
                  data={approvals}
                  rowKey="id"
                  columns={[
                    { key: "level", label: t("panel.level", { defaultValue: "Risk" }) },
                    { key: "what", label: t("panel.what", { defaultValue: "Action" }) },
                    { key: "seconds_left", label: t("panel.secondsLeft", { defaultValue: "Seconds left" }) },
                    { key: "reason", label: t("panel.reason", { defaultValue: "Why" }) },
                  ]}
                />
              </>
            ) : (
              <Text>{t("panel.noApprovals", { defaultValue: "Nothing is waiting for your approval." })}</Text>
            )}
            <Text>{t("panel.readOnly", { defaultValue: "This view is read only: N.E.K.O cannot approve or deny anything." })}</Text>
          </Stack>
        </Card>

        <Card title={t("panel.send", { defaultValue: "Give JARVIS a task" })}>
          {send ? (
            <Stack>
              <Field label={t("panel.taskLabel", { defaultValue: "The task, in plain words" })}>
                <Input
                  value={form.values.task}
                  placeholder={t("panel.taskPlaceholder", { defaultValue: "For example: open my downloads folder" })}
                  onChange={(value) => form.setField("task", String(value))}
                />
              </Field>
              <Button tone="primary" onClick={handOver}>{t("panel.sendButton", { defaultValue: "Hand over" })}</Button>
            </Stack>
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
        </Card>

        <Card title={t("panel.connectors", { defaultValue: "Connected services" })}>
          {connectors.length ? (
            <DataTable
              data={connectors}
              rowKey="name"
              columns={[
                { key: "label", label: t("panel.connector", { defaultValue: "Service" }) },
                { key: "status", label: t("panel.state", { defaultValue: "State" }) },
                { key: "transport", label: t("panel.transport", { defaultValue: "Kind" }) },
                { key: "tools_enabled", label: t("panel.toolsOn", { defaultValue: "Tools on" }) },
              ]}
            />
          ) : (
            <Text>{t("panel.noConnectors", { defaultValue: "No connectors are set up." })}</Text>
          )}
          <Text>
            {t("panel.manage", { defaultValue: "Add, test or switch tools on in the JARVIS console (Connectors screen):" })} {console_url}
          </Text>
        </Card>
      </Stack>
    </Page>
  )
}
