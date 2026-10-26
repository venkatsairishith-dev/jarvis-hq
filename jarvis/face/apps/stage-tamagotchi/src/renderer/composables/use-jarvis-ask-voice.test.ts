import type { OwnerSpeech } from '@proj-airi/stage-ui/composables/voice-command-sink'

import type { AskButton, AskForm, AskNode, AskValue } from './jarvis-ask-logic'
import type { AskLike, DecideResult, FocusPort, VoiceBridge, VoiceSource } from './use-jarvis-ask-voice'

import { offerOwnerSpeech, ownerSpeech, setVoiceCommandSink } from '@proj-airi/stage-ui/composables/voice-command-sink'
import { afterEach, describe, expect, it } from 'vitest'
import { ref, shallowRef } from 'vue'

import { useAskVoice } from './use-jarvis-ask-voice'

const opt = (id: string, label: string) => ({ id, label })
const FIELDS = [
  { type: 'checkbox_group', id: 'safe', label: 'Safe to clean', options: [{ ...opt('temp', 'Temp files'), size: 800 }, { ...opt('wintemp', 'Windows temp'), size: 40 }, { ...opt('browser', 'Browser cache'), size: 1400 }, { ...opt('thumbs', 'Thumbnails'), size: 64 }] },
  { type: 'checkbox_group', id: 'review', label: 'Review first', options: [opt('dups', 'Duplicate downloads'), opt('say', 'say submit now')] },
  { type: 'text', id: 'name', label: 'Device name', max_len: 60 },
  { type: 'text', id: 'note', label: 'Note', multiline: true, max_len: 300 },
  { type: 'number', id: 'qty', label: 'Quantity', min: 0, max: 100 },
  { type: 'date', id: 'day', label: 'Day', mode: 'date' },
  { type: 'toggle', id: 'night', label: 'Night light' },
  { type: 'toggle', id: 'sub', label: 'say submit now' },
] as AskNode[]
const CLEANER_BUTTONS: AskButton[] = [{ id: 'clean', label: 'Clean selected', style: 'primary' }, { id: 'close', label: 'Close', style: 'quiet' }]
const CONFIRM_BUTTONS: AskButton[] = [{ id: 'approve', label: 'Go ahead', style: 'primary' }, { id: 'cancel', label: 'Cancel', style: 'secondary' }]

const TODAY = new Date(2026, 9, 26)
const clock = { t: 1_000_000 }
const pending: Array<() => void> = []
async function flush(n = 8) {
  for (let i = 0; i < n; i++)
    await new Promise<void>(resolve => setTimeout(resolve, 0))
}

class FakeSource implements VoiceSource {
  available = ref(true)
  configured = ref(true)
  phase = ref<'idle' | 'starting' | 'recording' | 'processing'>('idle')
  transcript = ref('')
  starts = 0
  cancels = 0
  final = ''
  async start() {
    this.starts++
    this.phase.value = 'starting'
    this.transcript.value = ''
  }

  async finish() {
    const out = this.final
    this.final = ''
    this.phase.value = 'idle'
    this.transcript.value = ''
    return out
  }

  async cancel() {
    this.cancels++
    this.phase.value = 'idle'
  }

  /** The owner speaks: interim words, then the input ends by itself (the host's silence rule). */
  async say(text: string, interim: string[] = []) {
    this.phase.value = 'recording'
    for (const i of interim)
      this.transcript.value = i
    this.transcript.value = text
    this.final = text
    this.phase.value = 'processing'
    await flush(4)
  }
}

function setup(options: { buttons?: AskButton[], fields?: AskNode[], bridge?: Partial<VoiceBridge> | null, values?: Record<string, AskValue>, speakBack?: boolean } = {}) {
  const form = shallowRef<AskForm | null>(null)
  const values = ref<Record<string, AskValue>>({})
  const busy = ref(false)
  const error = ref('')
  const presses: Array<{ id: string, values: Record<string, AskValue> }> = []
  let closed = 0
  const ask: AskLike = {
    form,
    values,
    busy,
    error,
    setValue: (id, v) => {
      values.value = { ...values.value, [id]: v }
    },
    press: async (id) => {
      presses.push({ id, values: JSON.parse(JSON.stringify(values.value)) })
      form.value = null
      return true
    },
    close: async () => {
      closed++
      form.value = null
    },
  }
  const src = new FakeSource()
  let sourceCreated = 0
  const spoken: string[] = []
  const focusLog: string[] = []
  let focused: string | null = null
  const focus: FocusPort = {
    focusField: (id) => {
      focused = id
      focusLog.push(`focus ${id}`)
      return true
    },
    adjacentField: (from, dir) => {
      const order = ['name', 'note', 'qty', 'day']
      const at = from ? order.indexOf(from) : -1
      return order[at + (dir === 'next' ? 1 : -1)] ?? null
    },
    moveFocus: (dir) => {
      focusLog.push(`move ${dir}`)
      return 'Device name'
    },
    focusedField: () => focused,
    reveal: () => {},
  }
  const bridgeCalls: string[] = []
  const bridge: VoiceBridge | null = options.bridge === null
    ? null
    : {
        translate: async (text) => {
          bridgeCalls.push(`translate:${text}`)
          return options.bridge?.translate ? options.bridge.translate(text) : null
        },
        decide: async (r) => {
          bridgeCalls.push(`decide:${r.phrase}`)
          return options.bridge?.decide ? options.bridge.decide(r) : null
        },
        speechPlans: async () => options.bridge?.speechPlans ? options.bridge.speechPlans() : null,
      }
  const voice = useAskVoice({
    ask,
    source: () => {
      sourceCreated++
      return src
    },
    bridge,
    speak: async (t) => {
      spoken.push(t)
    },
    stopSpeaking: () => spoken.push('<stop>'),
    focus,
    today: () => TODAY,
    now: () => clock.t,
    setTimer: (fn, ms) => {
      pending.push(fn)
      return ms
    },
    clearTimer: () => {},
    settings: { speakBack: () => options.speakBack ?? true },
  })
  function open(extra: Partial<AskForm> = {}) {
    values.value = { safe: ['browser'], review: [], name: '', note: '', qty: null, day: '', night: false, ...options.values }
    form.value = {
      id: 'q1',
      token: 'tok',
      title: 'Clean up this PC',
      intro: 'Tick what to clean.',
      fields: options.fields ?? FIELDS,
      buttons: options.buttons ?? CLEANER_BUTTONS,
      busy: false,
      status: '',
      progress: null,
      values: values.value,
      notes: [],
      timeoutS: 90,
      ...extra,
    }
  }
  const say = (text: string, cause = 'wake-word') => voice.command(ownerSpeech(text, cause))
  return { voice, ask, form, values, busy, error, src, presses, spoken, focusLog, bridgeCalls, open, say, sourceCreated: () => sourceCreated, closed: () => closed }
}

afterEach(() => {
  setVoiceCommandSink(null)
  pending.length = 0
})

describe('the microphone starts on demand and stops with the form', () => {
  it('opening a form starts nothing, not even the voice source', async () => {
    const t = setup()
    t.open()
    await flush()
    expect(t.sourceCreated()).toBe(0)
    expect(t.src.starts).toBe(0)
    expect(t.voice.mode.value).toBe('off')
  })

  it('a field microphone starts one input; closing the form cancels it and ends the session', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('name')
    await flush()
    expect(t.src.starts).toBe(1)
    expect(t.voice.mode.value).toBe('dictate')
    expect(t.voice.micState()).toBe('listening')
    t.form.value = null
    expect(t.voice.mode.value).toBe('off')
    expect(t.src.cancels).toBe(1)
    await flush()
    expect(t.src.starts).toBe(1) // it did not start again
  })

  it('a new form replaces the old one: listening ends there too', async () => {
    const t = setup()
    t.open()
    t.voice.startCommand()
    await flush()
    t.form.value = { ...t.form.value!, id: 'q2' }
    expect(t.voice.mode.value).toBe('off')
    expect(t.src.cancels).toBe(1)
  })

  it('pressing the microphone again, or Escape, stops it', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('name')
    await flush()
    t.voice.toggleField('name')
    expect(t.voice.mode.value).toBe('off')
    expect(t.src.cancels).toBe(1)
    t.voice.startCommand()
    await flush()
    t.voice.stop()
    expect(t.voice.mode.value).toBe('off')
  })

  it('nothing said for a while turns the microphone off and says so', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('name')
    await flush()
    pending.splice(0).forEach(fn => fn()) // the idle timer fires
    await flush()
    expect(t.voice.mode.value).toBe('off')
    expect(t.voice.notice.value).toContain('nothing was said')
  })

  it('a missing or unconfigured speech provider says what to do', async () => {
    const t = setup()
    t.open()
    t.src.configured.value = false
    t.voice.toggleField('name')
    expect(t.voice.notice.value).toContain('Settings > Hearing')
    expect(t.src.starts).toBe(0)
  })

  it('a field that asks for a secret has no voice at all', async () => {
    const t = setup({ fields: [{ type: 'text', id: 'p', label: 'Your PIN' } as AskNode] })
    t.open()
    t.voice.toggleField('p')
    expect(t.src.starts).toBe(0)
    expect(t.voice.notice.value).toContain('cannot be filled by voice')
  })
})

describe('dictation', () => {
  it('shows interim words in the field, and the final text replaces them', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('name')
    await flush()
    t.src.phase.value = 'recording'
    t.src.transcript.value = 'my lap'
    expect(t.voice.interimFor('name')).toBe('my lap')
    expect(t.values.value.name).toBe('') // the field value changes only with the final text
    await t.src.say('my laptop')
    expect(t.values.value.name).toBe('my laptop')
    expect(t.voice.interimFor('name')).toBe('')
  })

  it('keeps listening after each phrase; later phrases follow the earlier ones', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('note')
    await flush()
    await t.src.say('buy milk')
    await t.src.say('and bread full stop')
    expect(t.values.value.note).toBe('buy milk and bread.')
    expect(t.src.starts).toBeGreaterThanOrEqual(2)
  })

  it('delete that, clear, new line, next field, previous field and stop', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('note')
    await flush()
    await t.src.say('first line')
    await t.src.say('new line')
    await t.src.say('second line')
    expect(t.values.value.note).toBe('first line\nsecond line')
    await t.src.say('delete that')
    expect(t.values.value.note).toBe('first line\n')
    await t.src.say('clear')
    expect(t.values.value.note).toBe('')
    await t.src.say('next field')
    expect(t.voice.activeField.value).toBe('qty')
    await t.src.say('forty two')
    expect(t.values.value.qty).toBe(42)
    await t.src.say('previous field')
    expect(t.voice.activeField.value).toBe('note')
    await t.src.say('stop')
    expect(t.voice.mode.value).toBe('off')
  })

  it('a Telugu or Hindi phrase stays exactly as spoken in the field', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('name')
    await flush()
    await t.src.say('నా ల్యాప్‌టాప్')
    expect(t.values.value.name).toBe('నా ల్యాప్‌టాప్')
    await t.src.say('मेरा लैपटॉप')
    expect(t.values.value.name).toBe('నా ల్యాప్‌టాప్ मेरा लैपटॉप')
    expect(t.bridgeCalls).toEqual([]) // no translation: that happens only when the text is given to a model
  })

  it('number and date fields take a value, not words', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('day')
    await flush()
    await t.src.say('tomorrow')
    expect(t.values.value.day).toBe('2026-10-27')
    await t.src.say('whenever')
    expect(t.voice.notice.value).toContain('did not get a day')
    expect(t.values.value.day).toBe('2026-10-27')
  })

  it('words such as submit and clean are only text; a text field never submits', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('note')
    await flush()
    await t.src.say('submit the form now')
    await t.src.say('clean')
    expect(t.values.value.note).toBe('submit the form now clean')
    expect(t.presses).toEqual([])
    expect(t.voice.mode.value).toBe('dictate')
  })

  it('a spoken PIN is never put in a field', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('note')
    await flush()
    await t.src.say('my pin is 4821')
    expect(t.values.value.note).toBe('')
    expect(t.voice.notice.value).toContain('JARVIS console')
  })

  it('undo puts back what dictation changed', async () => {
    const t = setup()
    t.open()
    t.voice.toggleField('name')
    await flush()
    await t.src.say('hello')
    await t.src.say('world')
    expect(t.values.value.name).toBe('hello world')
    await t.voice.undo()
    expect(t.values.value.name).toBe('hello')
  })
})

describe('commands, spoken to the open form', () => {
  it('ticks by label in english, tenglish and hindi, and says what it understood', async () => {
    const t = setup()
    t.open()
    expect(await t.say('tick temp files')).toBe(true)
    expect(t.values.value.safe).toEqual(['browser', 'temp'])
    expect(t.voice.hit.value).toBe('o:safe/temp')
    expect(t.spoken).toContain('Ticking Temp files')
    expect(t.voice.heard.value).toBe('tick temp files')
    await t.say('thumbnails tick chey')
    await t.say('browser cache untick karo')
    expect(t.values.value.safe).toEqual(['temp', 'thumbs'])
    await t.say('night light on cheyyi')
    expect(t.values.value.night).toBe(false)
    await t.say('turn night light on')
    expect(t.values.value.night).toBe(true)
  })

  it('sets and fills fields', async () => {
    const t = setup()
    t.open()
    await t.say('set quantity to fifty')
    await t.say('fill device name with Satish laptop')
    await t.say('set day to tomorrow')
    await t.say('note lo నేను రేపు వస్తాను అని రాయి')
    expect(t.values.value).toMatchObject({ qty: 50, name: 'Satish laptop', day: '2026-10-27', note: 'నేను రేపు వస్తాను అని' })
  })

  it('moves, reads and undoes', async () => {
    const t = setup()
    t.open()
    await t.say('next')
    expect(t.focusLog).toContain('move next')
    await t.say('tick thumbnails')
    await t.say('undo that change')
    expect(t.values.value.safe).toEqual(['browser'])
    await t.say('read the form aloud')
    await flush()
    expect(t.spoken.some(s => s.startsWith('Clean up this PC'))).toBe(true)
  })

  it('what is not for the form goes on to the chat', async () => {
    const t = setup()
    t.open()
    expect(await t.say('what is the weather in Hyderabad')).toBe(false)
    expect(await t.say('temp files')).toBe(false)
    expect(t.values.value.safe).toEqual(['browser'])
    expect(t.spoken).toEqual([])
  })

  it('a field that is not on the form: "I did not find that field."', async () => {
    const t = setup()
    t.open()
    expect(await t.say('tick printer drivers')).toBe(true)
    expect(t.voice.did.value).toBe('I did not find that field.')
    expect(t.spoken).toContain('I did not find that field.')
  })

  it('works through the voice store hook, with the cause the voice store gave', async () => {
    const t = setup()
    t.open()
    setVoiceCommandSink(speech => t.voice.command(speech))
    expect(await offerOwnerSpeech('tick temp files', 'wake-word')).toBe(true)
    expect(await offerOwnerSpeech('tick thumbnails', 'control')).toBe(true)
    expect(t.values.value.safe).toEqual(['browser', 'temp', 'thumbs'])
  })
})

describe('translation, only when the word lists do not know the words', () => {
  const translator = async (text: string) => {
    const table: Record<string, string> = {
      'టెంపరరీ ఫైల్స్ టిక్ చేయి': 'tick temp files',
      'ఇప్పుడు సమర్పించు': 'submit now',
      'थंबनेल चुनो': 'tick thumbnails',
    }
    const english = table[text]
    return english ? { english, sourceLang: 'te', translated: true } : null
  }

  it('a telugu phrase the grammar cannot match is translated on this laptop and then understood', async () => {
    const t = setup({ bridge: { translate: translator } })
    t.open()
    expect(await t.say('టెంపరరీ ఫైల్స్ టిక్ చేయి')).toBe(true)
    expect(t.values.value.safe).toEqual(['browser', 'temp'])
    expect(t.voice.understoodAs.value).toBe('tick temp files')
    expect(t.bridgeCalls).toEqual(['translate:టెంపరరీ ఫైల్స్ టిక్ చేయి'])
    await t.say('थंबनेल चुनो')
    expect(t.values.value.safe).toContain('thumbs')
  })

  it('english does not call the translator at all', async () => {
    const t = setup({ bridge: { translate: translator } })
    t.open()
    await t.say('tick temp files')
    await t.say('temp files tick chey')
    expect(t.bridgeCalls).toEqual([])
  })

  it('a submit that appears only in a translation is not taken', async () => {
    const t = setup({ bridge: { translate: translator } })
    t.open()
    await t.say('tick temp files')
    expect(await t.say('ఇప్పుడు సమర్పించు')).toBe(true)
    expect(t.presses).toEqual([])
    expect(t.voice.did.value).toContain('did not take that as a submit')
  })

  it('without a translator the original words just go on', async () => {
    const t = setup({ bridge: null })
    t.open()
    expect(await t.say('టెంపరరీ ఫైల్స్')).toBe(false)
  })
})

describe('"Did you mean A or B?"', () => {
  it('asks, in words and on screen, when two rows fit and nothing decides', async () => {
    const t = setup()
    t.open()
    await t.say('tick temp')
    expect(t.voice.question.value?.text).toBe('Did you mean Temp files (Safe to clean) or Windows temp (Safe to clean)?')
    expect(t.values.value.safe).toEqual(['browser'])
    expect(t.spoken).toContain(t.voice.question.value!.text)
  })

  it('the owner answers by voice: "the second one"', async () => {
    const t = setup()
    t.open()
    await t.say('tick temp')
    await t.say('the second one')
    expect(t.values.value.safe).toEqual(['browser', 'wintemp'])
    expect(t.voice.question.value).toBeNull()
  })

  it('or by name, or by a click, or declines', async () => {
    const t = setup()
    t.open()
    await t.say('tick temp')
    clock.t += 1_000 // the question has been spoken; the owner answers a moment later
    await t.say('windows temp')
    expect(t.values.value.safe).toEqual(['browser', 'wintemp'])
    clock.t += 1_000
    await t.say('untick temp')
    await t.voice.answerQuestion('o:safe/wintemp')
    expect(t.values.value.safe).toEqual(['browser'])
    clock.t += 1_000
    await t.say('tick temp')
    clock.t += 1_000
    await t.say('neither')
    expect(t.values.value.safe).toEqual(['browser'])
    expect(t.voice.question.value).toBeNull()
  })

  it('the decision engine picks when it is sure; below its bar the question is asked', async () => {
    const sure: DecideResult = { pick: 'o:safe/wintemp', confidence: 0.93, ask: false, options: ['o:safe/wintemp', 'o:safe/temp'] }
    const t = setup({ bridge: { decide: async () => sure } })
    t.open()
    await t.say('tick temp')
    expect(t.bridgeCalls).toEqual(['decide:temp'])
    expect(t.values.value.safe).toEqual(['browser', 'wintemp'])
    const unsure: DecideResult = { pick: 'o:safe/wintemp', confidence: 0.4, ask: true, options: ['o:safe/wintemp', 'o:safe/temp'] }
    const u = setup({ bridge: { decide: async () => unsure } })
    u.open()
    await u.say('tick temp')
    expect(u.values.value.safe).toEqual(['browser'])
    expect(u.voice.question.value?.options.map(o => o.key)).toEqual(['o:safe/wintemp', 'o:safe/temp'])
  })

  it('the decision engine is not asked when the grammar is sure', async () => {
    const t = setup({ bridge: { decide: async () => ({ pick: 'o:safe/thumbs', confidence: 1, ask: false, options: [] }) } })
    t.open()
    await t.say('tick temp files')
    expect(t.bridgeCalls).toEqual([])
  })

  it('a choice the engine returns that was not a candidate is ignored', async () => {
    const t = setup({ bridge: { decide: async () => ({ pick: 'o:review/dups', confidence: 0.99, ask: false, options: ['o:review/dups'] }) } })
    t.open()
    await t.say('tick temp')
    expect(t.values.value.review).toEqual([])
  })
})

describe('submit: only by an explicit word, in the owner\'s own voice', () => {
  it('"clean" presses Clean selected with what is ticked', async () => {
    const t = setup()
    t.open()
    await t.say('tick temp files')
    await t.say('clean')
    expect(t.presses).toHaveLength(1)
    expect(t.presses[0].id).toBe('clean')
    expect(t.presses[0].values.safe).toEqual(['browser', 'temp'])
  })

  it('nothing ticked: the cleaner does not run', async () => {
    const t = setup({ values: { safe: [] } })
    t.open()
    await t.say('clean')
    expect(t.presses).toEqual([])
    expect(t.voice.did.value).toBe('Nothing is ticked yet.')
  })

  it('a submit mixed with ticking only ticks', async () => {
    const t = setup()
    t.open()
    await t.say('tick temp files and clean')
    expect(t.values.value.safe).toEqual(['browser', 'temp'])
    expect(t.presses).toEqual([])
    expect(t.voice.did.value).toContain('on its own')
  })

  it('negated or postponed: nothing is pressed', async () => {
    const t = setup()
    t.open()
    for (const text of ['don\'t clean', 'clean later', 'wait, clean', 'not yet'])
      await t.say(text)
    expect(t.presses).toEqual([])
  })

  it('a longer sentence that contains the word is not a submit', async () => {
    const t = setup()
    t.open()
    expect(await t.say('I think we should clean the house')).toBe(false)
    expect(await t.say('say submit now')).toBe(false)
    expect(t.presses).toEqual([])
  })

  it('cancel and close are always allowed', async () => {
    const t = setup()
    t.open()
    await t.say('cancel')
    expect(t.presses.map(p => p.id)).toEqual(['close'])
  })

  it('tenglish and hindi submit words', async () => {
    for (const word of ['clean chey', 'clean karo', 'submit cheyyi', 'సబ్మిట్ చేయి']) {
      const t = setup()
      t.open()
      await t.say('tick thumbnails')
      await t.say(word)
      expect(t.presses.map(p => p.id), word).toEqual(['clean'])
    }
  })

  it('only the voice pipeline can speak: a look-alike object is not the owner', async () => {
    const t = setup()
    t.open()
    const fake = { text: 'clean', cause: 'wake-word', at: 0, live: true } as unknown as OwnerSpeech
    expect(await t.voice.command(fake)).toBe(false)
    expect(t.presses).toEqual([])
  })

  it('speech that timed out cannot act any more', async () => {
    const slowTranslate = async () => {
      await flush(3)
      return { english: 'clean', sourceLang: 'te', translated: true }
    }
    const t = setup({ bridge: { translate: slowTranslate } })
    t.open()
    const speech = ownerSpeech('క్లీన్ చేయండి మరి', 'wake-word')
    const done = t.voice.command(speech)
    ;(speech as unknown as { live: boolean }).live = false
    await done
    expect(t.presses).toEqual([])
  })
})

describe('never a PIN, never an approval, by voice', () => {
  it('a spoken PIN is refused where the console is, and goes nowhere', async () => {
    const t = setup()
    t.open()
    for (const text of ['my pin is 4821', 'password is hunter2', 'pin 4821']) {
      expect(await t.say(text)).toBe(true)
      expect(t.voice.did.value).toContain('JARVIS console')
    }
    expect(t.presses).toEqual([])
    expect(t.values.value.safe).toEqual(['browser'])
    expect(t.voice.heard.value).not.toContain('4821')
  })

  it('"approve it" and "authorize" do nothing but point to the console', async () => {
    const t = setup({ buttons: CONFIRM_BUTTONS })
    t.open()
    for (const text of ['approve it', 'I approve', 'authorize the payment', 'అప్రూవ్ చేయి']) {
      expect(await t.say(text), text).toBe(true)
      expect(t.voice.did.value, text).toContain('JARVIS console')
    }
    expect(t.presses).toEqual([])
  })

  it('a button that approves, pays, sends or deletes is never pressed by voice, whatever the word', async () => {
    const t = setup({ buttons: CONFIRM_BUTTONS })
    t.open()
    for (const text of ['go ahead', 'ok', 'submit', 'press go ahead', 'continue'])
      await t.say(text)
    expect(t.presses).toEqual([])
    await t.say('cancel')
    expect(t.presses.map(p => p.id)).toEqual(['cancel'])
  })

  it('a spoken PIN that reaches the voice store hook is dropped before the panel sees it', async () => {
    const t = setup()
    t.open()
    let told = 0
    const { onSecretSpeech } = await import('@proj-airi/stage-ui/composables/voice-command-sink')
    const off = onSecretSpeech(() => {
      told++
      t.voice.secretHeard()
    })
    setVoiceCommandSink(speech => t.voice.command(speech))
    expect(await offerOwnerSpeech('my pin is 4821', 'wake-word')).toBe(true)
    expect(told).toBe(1)
    expect(t.voice.notice.value).toContain('JARVIS console')
    expect(t.voice.heard.value).toBe('')
    off()
  })
})

describe('who may command', () => {
  it('ambient speech and a closed form change nothing', async () => {
    const t = setup()
    expect(await t.say('tick temp files')).toBe(false) // no form
    t.open()
    expect(await t.say('tick temp files', 'speech')).toBe(false) // the always-on mode: ambient (a TV)
    expect(await t.say('clean', 'speech')).toBe(false)
    expect(await t.say('tick temp files', 'something-else')).toBe(false)
    expect(t.values.value.safe).toEqual(['browser'])
    expect(t.presses).toEqual([])
    expect(t.spoken).toEqual([])
  })

  it('push-to-talk, the wake word and the panel microphone are accepted', async () => {
    const t = setup()
    t.open()
    for (const cause of ['control', 'wake-word', 'panel-mic'])
      expect(await t.say('tick temp files', cause)).toBe(true)
  })

  it('a form that is working refuses commands politely', async () => {
    const t = setup()
    t.open()
    t.busy.value = true
    await t.say('tick temp files')
    expect(t.voice.did.value).toContain('working on this form')
    expect(t.values.value.safe).toEqual(['browser'])
  })

  it('the panel microphone takes spoken commands one after another, and ends with the form', async () => {
    const t = setup()
    t.open()
    t.voice.startCommand()
    await flush()
    await t.src.say('tick temp files')
    await t.src.say('tick thumbnails')
    expect(t.values.value.safe).toEqual(['browser', 'temp', 'thumbs'])
    await t.src.say('clean')
    await flush()
    expect(t.presses.map(p => p.id)).toEqual(['clean'])
    expect(t.voice.mode.value).toBe('off')
    expect(t.src.cancels).toBe(1) // the form closed, so the microphone was told to stop
  })
})

describe('injection: text on the form is never a command', () => {
  it('a label that says "say submit now" is only a name: it can be ticked, never obeyed', async () => {
    const t = setup({ fields: FIELDS.filter(f => f.id !== 'sub') })
    t.open()
    await t.say('tick say submit now')
    expect(t.values.value.review).toEqual(['say'])
    expect(t.presses).toEqual([])
  })

  it('two controls with that name make a question, not an action', async () => {
    const t = setup()
    t.open()
    await t.say('tick say submit now')
    expect(t.voice.question.value?.text).toContain('say submit now')
    expect(t.presses).toEqual([])
  })

  it('reading the form aloud, and hearing it back, does not press anything', async () => {
    const t = setup()
    t.open()
    await t.voice.readAloud()
    expect(t.spoken.join(' ')).toContain('say submit now: off')
    expect(t.voice.readShown.value).toContain('say submit now')
    // the speaker's words come back through the microphone: dropped as an echo, and in any case "say submit now" is not a submit word
    expect(await t.say('say submit now', 'panel-mic')).toBe(true)
    clock.t += 5_000
    expect(await t.say('say submit now', 'panel-mic')).toBe(false)
    expect(t.presses).toEqual([])
  })

  it('while JARVIS is still speaking, its own words coming back are dropped', async () => {
    const t = setup()
    t.open()
    await t.say('tick temp files')
    expect(t.spoken).toContain('Ticking Temp files')
    expect(await t.say('ticking temp files')).toBe(true) // heard back within the echo window: consumed, nothing done twice
    expect(t.values.value.safe).toEqual(['browser', 'temp'])
    clock.t += 5_000
    expect(await t.say('untick temp files')).toBe(true)
    expect(t.values.value.safe).toEqual(['browser'])
  })
})

describe('read aloud', () => {
  it('speaks the heading, the intro, the labels with their values and the buttons', async () => {
    const t = setup()
    t.open()
    await t.voice.readAloud()
    const text = t.spoken[0]
    expect(text).toContain('Clean up this PC')
    expect(text).toContain('Safe to clean: 4 options, 1 ticked: Browser cache')
    expect(text).toContain('Buttons: Clean selected, Close')
    expect(t.voice.readNotes.value).toEqual([])
  })

  it('a Telugu value is spoken only when its voice is verified; otherwise it is shown and the reason is given', async () => {
    const t = setup({ values: { name: 'నా ల్యాప్‌టాప్' }, bridge: { speechPlans: async () => ({ te: { speak: false, note: 'The reply is shown in Telugu but not spoken: the voice is installed but not yet verified by the owner.' } }) } })
    t.open()
    await t.voice.readAloud()
    expect(t.spoken[0]).not.toContain('ల్యాప్')
    expect(t.voice.readShown.value).toContain('నా ల్యాప్‌టాప్')
    expect(t.voice.readNotes.value.join(' ')).toContain('not yet verified')
    const v = setup({ values: { name: 'నా ల్యాప్‌టాప్' }, bridge: { speechPlans: async () => ({ te: { speak: true, note: '' } }) } })
    v.open()
    await v.voice.readAloud()
    expect(v.spoken[0]).toContain('నా ల్యాప్‌టాప్')
  })

  it('"stop" while reading stops the speech', async () => {
    const t = setup()
    t.open()
    t.voice.stopReading()
    expect(t.spoken).toContain('<stop>')
  })
})
