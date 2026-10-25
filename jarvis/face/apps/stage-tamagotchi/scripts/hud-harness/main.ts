// The HUD on its own, drawn from plain props. scripts/hud-check.mjs drives this page with Playwright (query: state, mode, link, notices,
// approvals, busy, model) and reads nothing from the app: no network, no store, no Electron.
import { createApp, h, reactive } from 'vue'

import HudLayer from '../../src/renderer/components/hud/HudLayer.vue'

import { hudAriaLabel, hudLook, hudStateLabel, parseJarvisStatus } from '../../src/renderer/components/hud/hud-model'

const q = new URLSearchParams(location.search)
const st = reactive({
  state: q.get('state') || 'idle',
  mode: q.get('mode') || 'subtle',
  link: q.get('link') || 'up',
  busy: q.get('busy') === '1',
  notices: Number(q.get('notices') || 0),
  approvals: Number(q.get('approvals') || 0),
  age: Number(q.get('age') || 72),
  model: q.get('model') ?? 'zai-org/GLM-5.3-Flash',
  level: 0,
})
;(window as any).hud = st

createApp({
  render() {
    const status = parseJarvisStatus({
      structuredContent: {
        ok: true,
        console_up: st.link !== 'down',
        kernel_up: st.link === 'up' || st.link === 'killed',
        kill_switch_on: st.link === 'killed',
        approvals_waiting: st.approvals,
        messages_unread: st.notices,
        pc_busy: st.busy,
        pc_kind: 'task',
        pc_age_s: st.age,
        pc_stopping: false,
      },
    })
    const link = st.link as any
    const state = st.state as any
    return h('div', { style: 'position:absolute;inset:0' }, [
      h('div', { class: 'bg' }),
      h('div', { class: 'char' }, [h('div', { class: 'body' }), h('div', { class: 'head' })]),
      h(HudLayer, {
        look: hudLook(st.mode as any),
        state,
        stateLabel: hudStateLabel(state, link),
        label: hudAriaLabel(state, link, status),
        link,
        status,
        statusAt: Date.now(),
        modelLabel: st.model.split('/').pop() || '',
        level: st.level,
      }),
    ])
  },
}).mount('#app')
