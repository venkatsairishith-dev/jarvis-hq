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
        useChatStreamStore().updateActiveTurns([{ sessionId: 's1', turnId: 't1', message: { id: 'a-stream', role: 'assistant', content: '', slices: [], tool_results: [] } } as any])
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


/**
 * The orb and the minimal bar (D78): the REAL OrbShell (and with it OrbView, MinimalBar, OrbMenu, OrbSpeechHost and the HUD / look stores), with
 * the main process replaced by a snapshot that the harness works out itself (computeOrbWindow, the same function the main process uses).
 *   orb:<state>           the orb, size M, in one of the seven states      orb:size-S / orb:size-L   idle, other sizes
 *   orb:working-L         a task under way, large                          orb:card-indic            a Telugu and Hindi form open beside the orb
 *   orb:menu              the right-click menu open                        orb:card                  an ask form open beside the orb
 *   orb:badge             after an unhide: the count of what waited and the single quiet pulse
 *   bar:<S|M|L>           the minimal bar, idle                             bar:M-listening / bar:M-working / bar:L-needs-you
 *   avatar:idle           the avatar page's own idle behaviour, for the cost comparison (see avatarBaseline)
 */
async function orbScreen(look: 'orb' | 'minimal') {
  const { default: OrbShell } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/orb/OrbShell.vue')
  const { useJarvisLookStore } = await import('../../../face/apps/stage-tamagotchi/src/renderer/stores/settings/jarvis-look')
  const { useJarvisHudStore } = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/use-jarvis-hud')
  const { reduceStatusPoll } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/hud/hud-model')
  const { computeOrbWindow, zoneRect } = await import('../../../face/apps/stage-tamagotchi/src/shared/orb-layout')
  const { DEFAULT_LOOK_SETTINGS, zoneSize } = await import('../../../face/apps/stage-tamagotchi/src/shared/jarvis-look')
  const { useVoiceControlsStore } = await import('@proj-airi/stage-ui/stores/voice-controls')
  const { useSpeakingStore } = await import('@proj-airi/stage-ui/stores/audio')
  backdrop()
  const pinia = createPinia()
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } as any, missingWarn: false, fallbackWarn: false })

  const spec = look === 'orb' ? name : name.replace(/^([SML])-?/, '$1:')
  const [sizeLetter, stateName] = look === 'minimal' ? spec.split(':') : ['M', name]
  let size: 'S' | 'M' | 'L' = look === 'minimal' ? (sizeLetter as any) : 'M'
  let state = look === 'minimal' ? (stateName || 'idle') : name
  let menu = false
  let card = false
  let indic = false
  let badge = false
  if (look === 'orb') {
    if (name === 'size-S') { size = 'S'; state = 'idle' }
    else if (name === 'size-L') { size = 'L'; state = 'idle' }
    else if (name === 'working-L') { size = 'L'; state = 'working' }
    else if (name === 'card-indic') { state = 'needs-you'; card = true; indic = true }
    else if (name === 'menu') { state = 'idle'; menu = true }
    else if (name === 'card') { state = 'needs-you'; card = true }
    else if (name === 'badge') { state = 'idle'; badge = true }
  }

  const base = { ok: true, console_up: true, kernel_up: true, kill_switch_on: false, approvals_waiting: 0, messages_unread: 0, pc_busy: false, pc_kind: 'none', pc_age_s: 0 }
  const working = { pc_busy: true, pc_kind: 'task', pc_age_s: 40, goal_state: 'running', goal_n: 2, goal_total: 3, goal_exec: 'screen_read', pc_line: 'I am typing into notepad' }

  const Root = {
    setup() {
      const hud = useJarvisHudStore()
      const voiceControls = useVoiceControlsStore()
      const speaking = useSpeakingStore()
      const result = (d: Record<string, unknown>) => reduceStatusPoll({ status: null, failures: 0 }, { structuredContent: { ...base, ...d } })
      if (state === 'listening') { hud.listening = true; voiceControls.level = 0.55 }
      else if (state === 'thinking') hud.thinking = true
      else if (state === 'speaking') { hud.speaking = true; speaking.mouthOpenSize = 0.5; hud.level = 0.5 }
      else if (state === 'working') hud.setPoll(result(working))
      else if (state === 'needs-you') hud.setPoll(result({ ...working, approvals_waiting: 1, goal_state: 'waiting' }))
      else if (state === 'error') hud.setPoll({ status: null, failures: 3 })
      else hud.setPoll(result({}))

      const lookStore = useJarvisLookStore()
      const settings = { ...DEFAULT_LOOK_SETTINGS, look, size }
      const W = window.innerWidth
      const H = window.innerHeight
      const z = zoneSize(look, size)
      const zone = zoneRect(look, size, { x: Math.round((W - z.width) / 2), y: Math.round(H / 2 - z.height / 2) - (menu || card ? 0 : 30) })
      const layout = computeOrbWindow({ zone, look, pill: state === 'working' || state === 'needs-you', card: card ? { width: 460, height: Math.min(560, H - 24) } : null, menu, workArea: { x: 0, y: 0, width: W, height: H } })
      const shift = (r: any) => r && { x: r.x + layout.window.x, y: r.y + layout.window.y, width: r.width, height: r.height }
      lookStore.setSnapshot({
        settings,
        layout: { look, size, window: layout.window, zone: shift(layout.zone), pill: shift(layout.pill), card: shift(layout.card), cardSide: layout.cardSide, menu: shift(layout.menu) },
        shortcuts: { hide: { accelerator: 'Control+Alt+J', ok: true }, reset: { accelerator: 'Control+Alt+Shift+J', ok: true } },
        hidden: { hidden: false, queued: 0, forgotten: false, unhideSeq: 0 },
      })
      // after an unhide: the count of what waited and the single quiet pulse (a change of the hidden state, as the main process sends it)
      if (badge)
        setTimeout(() => lookStore.setSnapshot({ ...lookStore.snapshot!, hidden: { hidden: false, queued: 3, forgotten: true, unhideSeq: 1 } }), 40)
      return () => h(OrbShell)
    },
  }
  const vue = createApp(Root)
  vue.use(pinia).use(i18n).use(MotionPlugin).use(PiniaColada)
  vue.mount(app)
  await nextTick()
  if (menu) {
    // the menu opens from the orb: the right-click path of the real component
    await new Promise(r => setTimeout(r, 50))
    document.querySelector('[data-testid="orb"] .orb-disc')?.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }))
    await new Promise(r => setTimeout(r, 120))
  }
  if (card) {
    const { default: AskPanel } = await import('../../../face/apps/stage-tamagotchi/src/renderer/components/ask/AskPanel.vue')
    const { JARVIS_ASK_CLOSE_TOOL, JARVIS_ASK_SUBMIT_TOOL, JARVIS_ASK_WANTED_TOOL } = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/jarvis-ask-logic')
    const { useAskController } = await import('../../../face/apps/stage-tamagotchi/src/renderer/composables/use-jarvis-ask')
    const form = fixtures.ask[indic ? 'indic-choose' : 'confirm'] ?? Object.values(fixtures.ask)[0]
    const controller = useAskController({
      waitS: 0,
      call: async (tool: string) => {
        if (tool === JARVIS_ASK_WANTED_TOOL)
          return { structuredContent: { version: 1, form: (form as any).structuredContent.form } }
        if (tool === JARVIS_ASK_SUBMIT_TOOL)
          return { structuredContent: { ok: true, done: true } }
        if (tool === JARVIS_ASK_CLOSE_TOOL)
          return { structuredContent: { ok: true } }
        throw new Error(`unexpected call ${tool}`)
      },
    } as any)
    const host = document.createElement('div')
    document.body.append(host)
    createApp(AskPanel, { controller }).mount(host)
    await controller.pollOnce()
    await new Promise(r => setTimeout(r, 200))
  }
}

/**
 * The cost baseline (D78): the avatar's real renderer, idle. The Live2D scene of the face (the component Stage.vue draws) with the sample model, the way the
 * app idles: 30 frames a second (D57). It needs what the face's build downloads (the Cubism core and the Hiyori sample model); render.mjs copies them to
 * ./avatar-baseline/ when --avatar-baseline DIR is given. WebGL here is the software one (SwiftShader), so only the JS-side numbers (frame callbacks,
 * heap, scripts fetched) mean something; the CPU and GPU cost of a real GPU is for the laptop to measure.
 */
async function avatarScreen() {
  const base = q.get('base') ? './avatar-baseline' : ''
  if (!base)
    throw new Error('avatar:idle needs --avatar-baseline')
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement('script')
    script.src = `${base}/live2dcubismcore.min.js`
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('no Cubism core in the baseline folder'))
    document.head.append(script)
  })
  const { Live2DScene, useSettingsLive2d } = await import('@proj-airi/stage-ui-live2d')
  const pinia = createPinia()
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } as any, missingWarn: false, fallbackWarn: false })
  const state = ref<'pending' | 'loading' | 'mounted'>('pending')
  const failure = ref('')
  const Root = {
    setup() {
      const settings = useSettingsLive2d()
      settings.live2dMaxFps = 30
      return () => h('div', { style: 'position:absolute;inset:0' }, [h(Live2DScene as any, {
        'modelSrc': new URL(`${base}/hiyori_free_zh.zip`, location.href).href,
        'modelId': 'hiyori',
        'paused': false,
        'state': state.value,
        'onUpdate:state': (v: any) => { state.value = v },
        'onError': (e: Error) => { failure.value = e.message },
      })])
    },
  }
  createApp(Root).use(pinia).use(i18n).mount(app)
  const started = Date.now()
  while (state.value !== 'mounted' && !failure.value && Date.now() - started < 45000)
    await new Promise(r => setTimeout(r, 200))
  if (failure.value)
    throw new Error(`the avatar did not start: ${failure.value}`)
}

if (kind === 'orb')
  await orbScreen('orb')
else if (kind === 'bar')
  await orbScreen('minimal')
else if (kind === 'avatar')
  await avatarScreen()
else if (kind === 'chat')
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
