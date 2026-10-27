// The render harness page. render.mjs opens it as  index.html?screen=<name>&theme=<light|dark>  and waits for window.__ready.
// It draws the face's REAL components (chat window, HUD, ask panel, timeline, preview) with stand-ins only for what Electron and the JARVIS
// tool server would supply: the Electron bridge (electron-vueuse-mock.ts) and the tool results (fixtures.json, built by make_fixtures.py
// from the real Python backend). No network, no Live2D: the avatar is a flat placeholder shape.
import { PiniaColada } from '@pinia/colada'
import { MotionPlugin } from '@vueuse/motion'
import { createPinia } from 'pinia'
import { createApp, h, nextTick, ref } from 'vue'
import { createI18n } from 'vue-i18n'

import en from '@proj-airi/i18n/locales/en'

import '@unocss/reset/tailwind.css'
import 'vue-sonner/style.css'
import 'uno.css'
import '../../../face/apps/stage-tamagotchi/src/renderer/styles/main.css'
import '../../../face/packages/font-indic/index.css'
import '@fontsource-variable/dm-sans/index.css'
import '@fontsource-variable/nunito/index.css'

const q = new URLSearchParams(location.search)
const screen = q.get('screen') || 'chat-mixed'
const theme = q.get('theme') === 'dark' ? 'dark' : 'light'
document.documentElement.classList.toggle('dark', theme === 'dark')
document.documentElement.style.colorScheme = theme

const fixtures = await (await fetch('./fixtures.json')).json()
const [kind, ...rest] = screen.split(':')
const name = rest.join(':')
const app = document.getElementById('app')!
const errors: string[] = []
;(window as any).__errors = errors
window.addEventListener('error', e => errors.push(String(e.message)))
window.addEventListener('unhandledrejection', e => errors.push(String(e.reason)))

/** What sits behind the panels: the desktop colour with a flat stand-in for the avatar. */
function backdrop() {
  const stage = document.getElementById('stage')!
  stage.style.background = theme === 'dark' ? 'linear-gradient(160deg,#1c2740,#0e1524)' : 'linear-gradient(160deg,#9db4dc,#6b7fa8)'
  stage.innerHTML = '<div style="position:absolute;left:50%;bottom:0;transform:translateX(-50%);width:42%;height:78%;border-radius:45% 45% 0 0/30% 30% 0 0;background:rgba(255,255,255,.18)"></div>'
}

function ready() {
  requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(() => { (window as any).__ready = true }, 350)))
}

async function chatScreen() {
  const { default: InteractiveArea } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/InteractiveArea.vue')
  const { default: ChatPageShell } = await import('../../../face/apps/stage-tamagotchi/src/renderer/pages/chat-page-shell.vue')
  const { default: TitleBar } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/Window/TitleBar.vue')
  const { useChatSessionStore } = await import('@proj-airi/stage-ui/stores/chat/session-store')
  const { useChatStreamStore } = await import('@proj-airi/stage-ui/stores/chat/stream-store')
  const { useChatStore } = await import('@proj-airi/stage-ui/stores/chat')
  const { useConsciousnessStore } = await import('@proj-airi/stage-ui/stores/modules/consciousness')
  const { useAiriCardStore } = await import('@proj-airi/stage-ui/stores/modules/airi-card')
  const pinia = createPinia()
  const offline = name === 'offline'
  // Stores use vue-i18n, which needs a component: they are created and seeded in the root component's setup, as the app's own components do.
  const Root = {
    setup() {
      const consciousness = useConsciousnessStore()
      if (!offline) {
        consciousness.activeProvider = 'openai-compatible' as any
        consciousness.activeModel = 'zai-org/GLM-5.3-Flash' as any
      }
      const session = useChatSessionStore()
      const histories = fixtures.chat
      const which = offline ? 'empty' : (name || 'mixed')
      session.activeSessionId = 's1'
      session.sessionMessages.s1 = JSON.parse(JSON.stringify(histories[which] ?? histories.mixed))
      useAiriCardStore()
      if (name === 'waiting') {
        // a turn is under way and nothing has been written yet: the history shows its three-dot loader
        useChatStore().activeTurns = [{ sessionId: 's1', turnId: 't1' }]
        useChatStreamStore().updateActiveTurns([{ sessionId: 's1', turnId: 't1', message: { role: 'assistant', content: '', slices: [], tool_results: [] } } as any])
      }
      return () => h(ChatPageShell, null, { default: () => [h(TitleBar, { title: 'JARVIS', icon: 'i-solar:chat-line-bold', clickable: true }), h(InteractiveArea, { class: 'interaction-area block' })] })
    },
  }
  const vue = createApp(Root)
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } as any, missingWarn: false, fallbackWarn: false })
  vue.use(pinia).use(i18n).use(MotionPlugin).use(PiniaColada)
  vue.mount(app)
  await nextTick()
}

async function hudScreen() {
  const { default: HudLayer } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/hud/HudLayer.vue')
  const m = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/hud/hud-model')
  const base = { ok: true, console_up: true, kernel_up: true, kill_switch_on: false, approvals_waiting: 0, messages_unread: 0, pc_busy: false, pc_kind: 'none', pc_age_s: 0 }
  const cases: Record<string, { state: any, link: any, d: Record<string, unknown>, ack?: string }> = {
    'idle': { state: 'idle', link: 'up', d: {} },
    'acting': { state: 'acting', link: 'up', d: { pc_busy: true, pc_kind: 'task', pc_age_s: 72, pc_line: 'I am typing into notepad', messages_unread: 2 } },
    'safe': { state: 'idle', link: 'up', d: { safe_mode: true, pc_line: 'Safe mode' } },
    'pin': { state: 'acting', link: 'up', d: { pc_busy: true, pc_kind: 'task', pc_age_s: 31, approvals_waiting: 1, goal_state: 'waiting', goal_n: 2, goal_total: 3 } },
    'offline': { state: 'offline', link: 'down', d: { console_up: false, kernel_up: false } },
    'halted': { state: 'offline', link: 'killed', d: { kill_switch_on: true } },
    'listening': { state: 'listening', link: 'up', d: {} },
    'speaking': { state: 'speaking', link: 'up', d: {} },
  }
  const c = cases[name] ?? cases.idle
  const status = m.parseJarvisStatus({ structuredContent: { ...base, ...c.d } })
  backdrop()
  const level = ref(0.5)
  createApp({
    render: () => h('div', { style: 'position:absolute;inset:0' }, [h(HudLayer, {
      look: m.hudLook((q.get('mode') as any) || 'subtle'),
      state: c.state,
      stateLabel: m.hudStateLabel(c.state, c.link),
      label: m.hudAriaLabel(c.state, c.link, status),
      link: c.link,
      status,
      statusAt: Date.now(),
      modelLabel: 'GLM-5.3-Flash',
      line: m.hudLine(null, status),
      level: level.value,
    })]),
  }).mount(app)
}

async function askScreen() {
  const { default: AskPanel } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/ask/AskPanel.vue')
  const { JARVIS_ASK_CLOSE_TOOL, JARVIS_ASK_SUBMIT_TOOL, JARVIS_ASK_WANTED_TOOL } = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/jarvis-ask-logic')
  const { useAskController } = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/use-jarvis-ask')
  backdrop()
  const form = fixtures.ask[name]
  if (!form)
    throw new Error(`no ask fixture ${name}`)
  const controller = useAskController({
    waitS: 0,
    call: async (tool: string) => {
      if (tool === JARVIS_ASK_WANTED_TOOL)
        return { structuredContent: { version: 1, form: form.structuredContent.form } }
      if (tool === JARVIS_ASK_SUBMIT_TOOL)
        return { structuredContent: { ok: true, done: true } }
      if (tool === JARVIS_ASK_CLOSE_TOOL)
        return { structuredContent: { ok: true } }
      throw new Error(`unexpected call ${tool}`)
    },
  } as any)
  createApp(AskPanel, { controller }).mount(app)
  await controller.pollOnce()
  ;(window as any).__ask = controller
}

async function timelineScreen() {
  const { default: TimelinePanel } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/timeline/TimelinePanel.vue')
  const L = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/jarvis-timeline-logic')
  const { useTimelineController } = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/use-jarvis-timeline')
  backdrop()
  const fx = fixtures.timeline
  const view = fx.views[name]
  if (!view)
    throw new Error(`no timeline fixture ${name}`)
  const controller = useTimelineController({
    waitS: 0,
    call: async (tool: string, args: any) => {
      if (tool === L.JARVIS_TIMELINE_WANTED_TOOL)
        return { structuredContent: { version: 2, open_seq: 1, token: fx.token, view: view.structuredContent.view } }
      if (tool === L.JARVIS_TIMELINE_SHOT_TOOL)
        return fx.shots[args?.ref] ?? { structuredContent: { ok: false, message: 'gone' } }
      return fx.undo.ok
    },
  } as any)
  createApp(TimelinePanel, { controller }).mount(app)
  await controller.pollOnce()
  controller.setOpen(true)
  ;(window as any).__tl = controller
}

async function previewScreen() {
  const { default: PreviewPanel } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/PreviewPanel.vue')
  const L = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/jarvis-preview-logic')
  const { usePreviewController } = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/use-jarvis-preview')
  backdrop()
  const fx = fixtures.preview
  const controller = usePreviewController({
    call: async (tool: string, args: any) => {
      if (tool === L.JARVIS_OUTPUTS_TOOL)
        return { structuredContent: fx.outputs }
      if (tool === L.JARVIS_WANTED_TOOL)
        return { structuredContent: { count: 0, ids: [] } }
      if (tool === L.JARVIS_OPEN_OUTPUT_TOOL)
        return { structuredContent: { result: { opened: args?.path } } }
      const byPage = fx.get[String(args?.id)]
      const hit = byPage?.[String(args?.page ?? 0)] ?? byPage?.['0']
      const result = hit ? { ...hit } : { ok: false, message: 'I cannot find that file' }
      if (args?.thumbs !== true)
        delete result.thumbs
      return { structuredContent: result }
    },
  } as any)
  createApp(PreviewPanel, { controller }).mount(app)
  const ids = Object.fromEntries((fx.outputs.items as Array<{ id: string, title: string }>).map(i => [i.title, i.id]))
  ;(window as any).__preview = { controller, ids }
  if (name !== 'closed') {
    await new Promise(r => setTimeout(r, 50))
    ;(document.querySelector('.pv-launch') as HTMLElement | null)?.click()
    await new Promise(r => setTimeout(r, 150))
    if (name && name !== 'list')
      await controller.show(ids[name])
  }
}

if (kind === 'chat')
  await chatScreen()
else if (kind === 'hud')
  await hudScreen()
else if (kind === 'ask')
  await askScreen()
else if (kind === 'timeline')
  await timelineScreen()
else if (kind === 'preview')
  await previewScreen()
else
  throw new Error(`unknown screen ${screen}`)
ready()
