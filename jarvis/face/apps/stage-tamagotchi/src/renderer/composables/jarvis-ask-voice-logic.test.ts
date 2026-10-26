import type { AskButton, AskNode, AskValue } from './jarvis-ask-logic'
import type { FormAction, Resolution, ResolveContext } from './jarvis-ask-voice-logic'

import { describe, expect, it } from 'vitest'

import {
  actionsFor,
  answerChoice,
  buttonBlockedForVoice,
  createEchoGuard,
  dictationOps,
  gate,
  isFormCommand,
  joinDictation,
  labelScore,
  nodeMap,
  parseClock,
  parseDay,
  parseNumber,
  parseUtterance,
  readAloudPlan,
  resolveIntent,
  scriptLanguage,
  speechRefusal,
  voiceTargets,
} from './jarvis-ask-voice-logic'

const opt = (id: string, label: string) => ({ id, label })
const FIELDS: AskNode[] = [
  { type: 'checkbox_group', id: 'safe', label: 'Safe to clean', options: [opt('temp', 'Temp files'), opt('wintemp', 'Windows temp'), opt('browser', 'Browser cache'), opt('thumbs', 'Thumbnails'), opt('bin', 'Recycle bin')] },
  { type: 'section', label: 'More', children: [
    { type: 'checkbox_group', id: 'review', label: 'Review first', options: [opt('dups', 'Duplicate downloads'), opt('inst', 'Old installers')] },
  ] },
  { type: 'radio', id: 'out', label: 'Output', variant: 'segmented', options: [opt('spk', 'Speakers'), opt('hp', 'Headphones'), opt('bt', 'Bluetooth speaker')] },
  { type: 'toggle', id: 'night', label: 'Night light' },
  { type: 'text', id: 'name', label: 'Device name', max_len: 60 },
  { type: 'text', id: 'note', label: 'Note', multiline: true, max_len: 300 },
  { type: 'number', id: 'qty', label: 'Quantity', min: 0, max: 100, step: 1 },
  { type: 'slider', id: 'vol', label: 'Volume', min: 0, max: 100, step: 1 },
  { type: 'date', id: 'day', label: 'Day', mode: 'date' },
  { type: 'date', id: 'at', label: 'Time', mode: 'time' },
  { type: 'select', id: 'repeat', label: 'Repeat', options: [opt('none', 'None'), opt('daily', 'Daily'), opt('weekly', 'Weekly')] },
] as AskNode[]
const BUTTONS: AskButton[] = [{ id: 'clean', label: 'Clean selected', style: 'primary' }, { id: 'scan', label: 'Scan again', style: 'secondary' }, { id: 'close', label: 'Close', style: 'quiet' }]
const TODAY = new Date(2026, 9, 26) // Monday 26 October 2026

function ctx(extra: Partial<ResolveContext> = {}, fields = FIELDS, buttons = BUTTONS): ResolveContext {
  return { targets: voiceTargets(fields, buttons), nodes: nodeMap(fields), values: {} as Record<string, AskValue>, today: TODAY, ...extra }
}

/** Parse and resolve every intent of a phrase the way the controller does (no translation, no decision engine). */
function run(text: string, c = ctx()): Resolution[] {
  const parsed = parseUtterance(text)
  return parsed.intents.map(i => resolveIntent(i, c))
}
function actions(text: string, c = ctx()): FormAction[] {
  return run(text, c).flatMap(r => r.kind === 'actions' ? r.actions : [])
}
function kinds(text: string, c = ctx()): string[] {
  return run(text, c).map(r => r.kind)
}

describe('the form as voice targets', () => {
  it('lists inputs, options (also inside a section) and buttons with the keys the backend builds', () => {
    const keys = voiceTargets(FIELDS, BUTTONS).map(t => t.key)
    expect(keys).toContain('f:safe')
    expect(keys).toContain('o:safe/temp')
    expect(keys).toContain('o:review/inst')
    expect(keys).toContain('b:clean')
  })

  it('leaves out a field that asks for a PIN or password, even if one slipped through', () => {
    const t = voiceTargets([{ type: 'text', id: 'p', label: 'Your PIN' } as AskNode, { type: 'text', id: 'ok', label: 'Name' } as AskNode], [])
    expect(t.map(x => x.key)).toEqual(['f:ok'])
  })
})

describe('fuzzy labels', () => {
  it('matches in any order, by prefix, plural and a small slip of the recognizer', () => {
    expect(labelScore('temp files', 'Temp files')).toBe(1)
    expect(labelScore('files temp', 'Temp files')).toBe(1)
    expect(labelScore('temporary files', 'Temp files')).toBeGreaterThan(0.9)
    expect(labelScore('browser cashe', 'Browser cache')).toBeGreaterThan(0.85)
    expect(labelScore('thumbnail', 'Thumbnails')).toBe(1)
    expect(labelScore('disk space', 'Temp files')).toBe(0)
  })
})

describe('english commands', () => {
  it('ticks and unticks by label', () => {
    expect(actions('tick temp files')).toEqual([{ type: 'check', fieldId: 'safe', optionId: 'temp', on: true, label: 'Temp files' }])
    expect(actions('untick the browser cache')).toEqual([{ type: 'check', fieldId: 'safe', optionId: 'browser', on: false, label: 'Browser cache' }])
    expect(actions('Check thumbnails, please.')[0]).toMatchObject({ type: 'check', optionId: 'thumbs', on: true })
    expect(actions('uncheck recycle bin')[0]).toMatchObject({ optionId: 'bin', on: false })
  })

  it('ticks a list: the verb carries over', () => {
    const a = actions('tick temp files, browser cache and thumbnails')
    expect(a.map(x => (x as { optionId: string }).optionId)).toEqual(['temp', 'browser', 'thumbs'])
    expect(a.every(x => x.type === 'check' && x.on)).toBe(true)
  })

  it('"don\'t tick" is an untick', () => {
    expect(actions('don\'t tick thumbnails')[0]).toMatchObject({ type: 'check', optionId: 'thumbs', on: false })
  })

  it('chooses an option of a choice and refuses to untick it', () => {
    expect(actions('select headphones')[0]).toMatchObject({ type: 'choose', fieldId: 'out', optionId: 'hp' })
    expect(kinds('untick headphones')).toEqual(['refuse'])
  })

  it('switches a toggle', () => {
    expect(actions('turn night light on')[0]).toEqual({ type: 'toggle', fieldId: 'night', on: true, label: 'Night light' })
    expect(actions('turn off the night light')[0]).toMatchObject({ type: 'toggle', on: false })
  })

  it('sets and fills fields', () => {
    expect(actions('set volume to 40')[0]).toMatchObject({ type: 'set', fieldId: 'vol', value: 40 })
    expect(actions('set quantity to forty five')[0]).toMatchObject({ type: 'set', fieldId: 'qty', value: 45 })
    expect(actions('set volume to 500')[0]).toMatchObject({ value: 100 })
    expect(actions('fill device name with Satish\'s laptop.')[0]).toMatchObject({ type: 'set', fieldId: 'name', value: 'Satish\'s laptop' })
    expect(actions('type hello world in the note')[0]).toMatchObject({ type: 'set', fieldId: 'note', value: 'hello world' })
    expect(actions('set note to bread and milk')[0]).toMatchObject({ fieldId: 'note', value: 'bread and milk' })
    expect(actions('set repeat to weekly')[0]).toMatchObject({ type: 'choose', fieldId: 'repeat', optionId: 'weekly' })
    expect(actions('set day to tomorrow')[0]).toMatchObject({ type: 'set', fieldId: 'day', value: '2026-10-27' })
    expect(actions('set time to 5:30 pm')[0]).toMatchObject({ fieldId: 'at', value: '17:30' })
  })

  it('asks for a value that is missing or not understood', () => {
    expect(kinds('set quantity')).toEqual(['need'])
    expect(kinds('set quantity to lots')).toEqual(['need'])
    expect(kinds('set day to whenever')).toEqual(['need'])
  })

  it('moves, reads and undoes', () => {
    expect(actions('next')).toEqual([{ type: 'focus', direction: 'next' }])
    expect(actions('go back')).toEqual([{ type: 'focus', direction: 'previous' }])
    expect(actions('read the form aloud')).toEqual([{ type: 'read' }])
    expect(actions('undo that change')).toEqual([{ type: 'undo' }])
    expect(actions('stop listening')).toEqual([{ type: 'stop' }])
  })

  it('a bare option name picks it in a choice, but a bare checkbox name does nothing', () => {
    expect(actions('headphones')[0]).toMatchObject({ type: 'choose', optionId: 'hp' })
    expect(kinds('temp files')).toEqual(['notfound'])
    expect(isFormCommand(parseUtterance('temp files'))).toBe(false)
    expect(isFormCommand(parseUtterance('what is the weather in Hyderabad'))).toBe(false)
  })
})

describe('telugu, hindi and tenglish', () => {
  it('temp files tick chey', () => {
    expect(actions('temp files tick chey')[0]).toMatchObject({ type: 'check', optionId: 'temp', on: true })
    expect(actions('Temp files tick cheyyi.')[0]).toMatchObject({ optionId: 'temp', on: true })
    expect(actions('thumbnails untick chey')[0]).toMatchObject({ optionId: 'thumbs', on: false })
    expect(actions('browser cache tick teesey')[0]).toMatchObject({ optionId: 'browser', on: false })
    expect(actions('thumbnails vaddu')[0]).toMatchObject({ optionId: 'thumbs', on: false })
  })

  it('hindi in both scripts', () => {
    expect(actions('thumbnails ko tick karo')[0]).toMatchObject({ optionId: 'thumbs', on: true })
    expect(actions('browser cache untick karo')[0]).toMatchObject({ optionId: 'browser', on: false })
    expect(actions('recycle bin ko hatao')[0]).toMatchObject({ optionId: 'bin', on: false })
    expect(actions('recycle bin टिक करो')[0]).toMatchObject({ optionId: 'bin', on: true })
    expect(actions('thumbnails चुनो')[0]).toMatchObject({ optionId: 'thumbs', on: true })
  })

  it('telugu script verbs', () => {
    expect(actions('thumbnails టిక్ చేయి')[0]).toMatchObject({ optionId: 'thumbs', on: true })
    expect(actions('thumbnails తీసేయి')[0]).toMatchObject({ optionId: 'thumbs', on: false })
  })

  it('next, back, cancel, read, undo in the three languages', () => {
    expect(actions('next')[0]).toEqual({ type: 'focus', direction: 'next' })
    expect(actions('agla')[0]).toEqual({ type: 'focus', direction: 'next' })
    expect(actions('పిచ్లా')).toEqual([])
    expect(actions('venakki')[0]).toEqual({ type: 'focus', direction: 'previous' })
    expect(actions('cancel chey')[0]).toMatchObject({ type: 'press', role: 'cancel', buttonId: 'close' })
    expect(actions('band karo')[0]).toMatchObject({ type: 'press', role: 'cancel' })
    expect(actions('फॉर्म पढ़ो')).toEqual([])
    expect(actions('padho')[0]).toEqual({ type: 'read' })
    expect(actions('undo karo')[0]).toEqual({ type: 'undo' })
  })

  it('a value in telugu stays as spoken (it is his text)', () => {
    const a = actions('note lo నేను రేపు వస్తాను అని రాయి')
    expect(a[0]).toMatchObject({ type: 'set', fieldId: 'note', value: 'నేను రేపు వస్తాను అని' })
  })

  it('digits in telugu and hindi script', () => {
    expect(parseNumber('౪౫')).toBe(45)
    expect(parseNumber('४०')).toBe(40)
  })
})

describe('ambiguity and unknown fields', () => {
  it('"temp" fits two rows: a question, not a guess', () => {
    const r = run('tick temp')[0]
    expect(r.kind).toBe('unsure')
    if (r.kind === 'unsure')
      expect(r.ranked.slice(0, 2).map(x => x.target.label).sort()).toEqual(['Temp files', 'Windows temp'])
  })

  it('a field that is not on the form', () => {
    const r = run('tick printer drivers')[0]
    expect(r).toEqual({ kind: 'notfound', say: 'I did not find that field.' })
    expect(run('set colour to red')[0]).toEqual({ kind: 'notfound', say: 'I did not find that field.' })
  })

  it('answers to "Did you mean A or B?"', () => {
    const opts = [{ key: 'o:safe/temp', label: 'Temp files' }, { key: 'o:safe/wintemp', label: 'Windows temp' }]
    expect(answerChoice('the first one', opts)).toBe('o:safe/temp')
    expect(answerChoice('second', opts)).toBe('o:safe/wintemp')
    expect(answerChoice('windows temp', opts)).toBe('o:safe/wintemp')
    expect(answerChoice('neither', opts)).toBe('dismiss')
    expect(answerChoice('banana', opts)).toBeNull()
  })

  it('"all" only ticks a group that is named or alone, and never a review list', () => {
    expect(kinds('tick all')).toEqual(['unsure'])
    expect(actions('tick all safe to clean')[0]).toMatchObject({ type: 'group', fieldId: 'safe', on: true })
    expect(kinds('tick everything in review first')).toEqual(['refuse'])
  })
})

describe('submit by voice', () => {
  it('only an explicit word that is the whole phrase presses a button', () => {
    expect(actions('clean')[0]).toMatchObject({ type: 'press', buttonId: 'clean', role: 'submit' })
    expect(actions('clean selected please')[0]).toMatchObject({ buttonId: 'clean' })
    expect(actions('Clean chey')[0]).toMatchObject({ buttonId: 'clean' })
    expect(actions('clean karo')[0]).toMatchObject({ buttonId: 'clean' })
    expect(actions('ok')[0]).toMatchObject({ buttonId: 'clean', role: 'submit' })
    expect(actions('submit')[0]).toMatchObject({ buttonId: 'clean' })
    expect(actions('సబ్మిట్ చేయి')[0]).toMatchObject({ buttonId: 'clean' })
    expect(actions('सबमिट करो')[0]).toMatchObject({ buttonId: 'clean' })
  })

  it('a word inside a longer sentence is not a submit', () => {
    for (const t of ['say submit now', 'I think we should clean the house', 'ok so what is the weather', 'please do not forget to clean later', 'the form says submit']) {
      expect(actions(t).filter(a => a.type === 'press')).toEqual([])
    }
  })

  it('negated or postponed submit words withhold', () => {
    for (const t of ['don\'t clean', 'do not clean yet', 'clean later', 'wait, clean', 'not yet, clean it', 'never clean']) {
      const p = parseUtterance(t)
      expect(p.withheld, t).toBe(true)
      expect(p.intents.filter(i => i.type === 'submit'), t).toEqual([])
    }
  })

  it('a submit mixed with other commands runs the other commands only', () => {
    const p = parseUtterance('tick temp files and clean')
    expect(p.mixedSubmit).toBe(true)
    expect(p.intents.map(i => i.type)).toEqual(['on'])
    expect(parseUtterance('thumbnails tick chey then clean chey').mixedSubmit).toBe(true)
  })

  it('a submit that came through translation is refused', () => {
    const r = resolveIntent({ type: 'submit', word: 'submit', generic: true }, ctx({ viaTranslation: true }))
    expect(r.kind).toBe('refuse')
  })

  it('there is no such button', () => {
    const c = ctx({}, FIELDS, [{ id: 'ok', label: 'Continue', style: 'primary' }])
    expect(kinds('clean', c)).toEqual(['notfound'])
    expect(actions('submit', c)[0]).toMatchObject({ buttonId: 'ok' })
  })

  it('a button that approves, pays, sends or deletes is a click, never a voice command', () => {
    const confirm: AskButton[] = [{ id: 'approve', label: 'Go ahead', style: 'primary' }, { id: 'cancel', label: 'Cancel', style: 'secondary' }]
    expect(buttonBlockedForVoice(confirm[0])).toBe(true)
    expect(buttonBlockedForVoice({ id: 'ok', label: 'Send it', style: 'primary' })).toBe(true)
    expect(buttonBlockedForVoice({ id: 'ok', label: 'Delete files', style: 'primary' })).toBe(true)
    expect(buttonBlockedForVoice(BUTTONS[0])).toBe(false)
    const c = ctx({}, FIELDS, confirm)
    for (const t of ['go ahead', 'ok', 'submit', 'press go ahead'])
      expect(kinds(t, c)[0], t).toBe('refuse')
    expect(actions('cancel', c)[0]).toMatchObject({ role: 'cancel', buttonId: 'cancel' })
  })

  it('"press scan again" presses a plain button', () => {
    expect(actions('press scan again')[0]).toMatchObject({ type: 'press', buttonId: 'scan', role: 'button' })
  })
})

describe('never a PIN, never an approval', () => {
  const SECRET = ['my pin is 4821', 'the PIN is one two three four', 'enter pin 4821', 'password is hunter2', 'otp 123456', 'పిన్ 4821', 'मेरा पिन 4821 है', 'pin number 4821']
  const APPROVE = ['approve it', 'I approve', 'approved', 'authorize the payment', 'authorise this', 'అప్రూవ్ చేయి', 'अप्रूव करो']

  it('refuses before anything is parsed', () => {
    for (const t of [...SECRET, ...APPROVE]) {
      const p = parseUtterance(t)
      expect(p.refusal, t).not.toBeNull()
      expect(p.intents, t).toEqual([])
    }
  })

  it('does not refuse a postal pin code or talk about pins', () => {
    expect(speechRefusal('send it to pin code 500081')).toBeNull()
    expect(speechRefusal('what is a pin')).toBeNull()
    expect(speechRefusal('I forgot my password')).toBeNull()
    expect(speechRefusal('the pin is on the left')).toBeNull()
  })

  it('a form can never be made to hold a PIN field by voice', () => {
    const f = [{ type: 'text', id: 'p', label: 'PIN' } as AskNode, { type: 'text', id: 'n', label: 'Name' } as AskNode]
    const r = resolveIntent({ type: 'set', fieldPhrase: 'pin', value: '4821', verb: 'set' }, ctx({}, f, []))
    expect(r.kind).toBe('notfound')
  })
})

describe('who may command', () => {
  it('only an open form and push-to-talk, the wake word or the panel microphone', () => {
    expect(gate({ formOpen: true, cause: 'control' }).ok).toBe(true)
    expect(gate({ formOpen: true, cause: 'wake-word' }).ok).toBe(true)
    expect(gate({ formOpen: true, cause: 'panel-mic' }).ok).toBe(true)
    expect(gate({ formOpen: true, cause: 'speech' })).toEqual({ ok: false, reason: 'ambient' })
    expect(gate({ formOpen: true, cause: undefined }).ok).toBe(false)
    expect(gate({ formOpen: false, cause: 'control' })).toEqual({ ok: false, reason: 'no-form' })
  })

  it('drops what JARVIS is saying (a label that says "say submit now" cannot be obeyed from the speaker), and only while it speaks', () => {
    let t = 1000
    const echo = createEchoGuard(2_000, () => t)
    expect(echo.isEcho('say submit now')).toBe(false)
    echo.said('Safe to clean: say submit now')
    t += 3_000
    expect(echo.isEcho('say submit now')).toBe(true)
    expect(echo.isEcho('tick temp files')).toBe(false)
    echo.finished()
    t += 1_000
    expect(echo.isEcho('say submit now')).toBe(true)
    t += 1_500
    expect(echo.isEcho('say submit now')).toBe(false)
  })
})

describe('numbers, days and times', () => {
  it('numbers', () => {
    expect(parseNumber('40')).toBe(40)
    expect(parseNumber('3.5 percent')).toBe(3.5)
    expect(parseNumber('1,000')).toBe(1000)
    expect(parseNumber('twenty five')).toBe(25)
    expect(parseNumber('one hundred and five')).toBe(105)
    expect(parseNumber('two thousand twenty six')).toBe(2026)
    expect(parseNumber('minus three')).toBe(-3)
    expect(parseNumber('three point five')).toBe(3.5)
    expect(parseNumber('banana')).toBeNull()
  })

  it('days (today is Monday 26 October 2026)', () => {
    expect(parseDay('today', TODAY)).toBe('2026-10-26')
    expect(parseDay('tomorrow', TODAY)).toBe('2026-10-27')
    expect(parseDay('day after tomorrow', TODAY)).toBe('2026-10-28')
    expect(parseDay('repu', TODAY)).toBe('2026-10-27')
    expect(parseDay('friday', TODAY)).toBe('2026-10-30')
    expect(parseDay('next monday', TODAY)).toBe('2026-11-02')
    expect(parseDay('in 3 days', TODAY)).toBe('2026-10-29')
    expect(parseDay('25 december', TODAY)).toBe('2026-12-25')
    expect(parseDay('december 25th 2027', TODAY)).toBe('2027-12-25')
    expect(parseDay('2 march', TODAY)).toBe('2027-03-02')
    expect(parseDay('2026-12-01', TODAY)).toBe('2026-12-01')
    expect(parseDay('whenever', TODAY)).toBeNull()
  })

  it('times', () => {
    expect(parseClock('5 pm')).toBe('17:00')
    expect(parseClock('5:30 pm')).toBe('17:30')
    expect(parseClock('17:30')).toBe('17:30')
    expect(parseClock('noon')).toBe('12:00')
    expect(parseClock('half past five pm')).toBe('17:30')
    expect(parseClock('quarter to six am')).toBe('05:45')
    expect(parseClock('five thirty pm')).toBe('17:30')
    expect(parseClock('12 am')).toBe('00:00')
    expect(parseClock('later')).toBeNull()
  })
})

describe('dictation editing', () => {
  const D = { punctuation: true, multiline: true }
  it('plain speech is text', () => {
    expect(dictationOps('buy milk and bread', D)).toEqual([{ type: 'text', text: 'buy milk and bread' }])
    expect(dictationOps('  ', D)).toEqual([])
  })

  it('a command is the whole utterance; the same words inside a sentence are text', () => {
    expect(dictationOps('delete that', D)).toEqual([{ type: 'delete' }])
    expect(dictationOps('Clear.', D)).toEqual([{ type: 'clear' }])
    expect(dictationOps('next field', D)).toEqual([{ type: 'next' }])
    expect(dictationOps('previous field', D)).toEqual([{ type: 'previous' }])
    expect(dictationOps('stop dictation', D)).toEqual([{ type: 'stop' }])
    expect(dictationOps('I will clear the table', D)).toEqual([{ type: 'text', text: 'I will clear the table' }])
    expect(dictationOps('please delete that file', D)).toEqual([{ type: 'text', text: 'please delete that file' }])
  })

  it('"submit" and "clean" in dictation are only text', () => {
    expect(dictationOps('submit the form now', D)).toEqual([{ type: 'text', text: 'submit the form now' }])
    expect(dictationOps('clean', D)).toEqual([{ type: 'text', text: 'clean' }])
  })

  it('new line, trailing next field, punctuation words', () => {
    expect(dictationOps('first line new line second line', D)).toEqual([{ type: 'text', text: 'first line' }, { type: 'newline', paragraph: false }, { type: 'text', text: 'second line' }])
    expect(dictationOps('new paragraph', D)).toEqual([{ type: 'newline', paragraph: true }])
    expect(dictationOps('call me tomorrow next field', D)).toEqual([{ type: 'text', text: 'call me tomorrow' }, { type: 'next' }])
    expect(dictationOps('hello comma how are you question mark', D)).toEqual([{ type: 'text', text: 'hello, how are you?' }])
    expect(dictationOps('the end full stop', D)).toEqual([{ type: 'text', text: 'the end.' }])
    expect(dictationOps('a period of time', D)).toEqual([{ type: 'text', text: 'a period of time' }])
    expect(dictationOps('hello comma', { punctuation: false, multiline: true })).toEqual([{ type: 'text', text: 'hello comma' }])
  })

  it('telugu and hindi are never touched', () => {
    expect(dictationOps('నేను రేపు వస్తాను', D)).toEqual([{ type: 'text', text: 'నేను రేపు వస్తాను' }])
    expect(dictationOps('मैं कल आऊंगा', D)).toEqual([{ type: 'text', text: 'मैं कल आऊंगा' }])
    expect(scriptLanguage('నేను రేపు వస్తాను')).toBe('te')
    expect(scriptLanguage('मैं कल आऊंगा')).toBe('hi')
    expect(scriptLanguage('hello there my friend నేను')).toBe('en')
  })

  it('joins chunks with one space, none before punctuation, newlines as they are', () => {
    expect(joinDictation('', 'hello')).toBe('hello')
    expect(joinDictation('hello', 'world')).toBe('hello world')
    expect(joinDictation('hello ', 'world')).toBe('hello world')
    expect(joinDictation('hello', ', there')).toBe('hello, there')
    expect(joinDictation('hello ', '\nworld')).toBe('hello\nworld')
    expect(joinDictation('hello\n', 'world')).toBe('hello\nworld')
  })
})

describe('read aloud', () => {
  const form = { title: 'Clean up this PC', intro: 'Tick what to clean.', fields: FIELDS, buttons: BUTTONS }
  it('says the heading, the intro, the labels with their values and the buttons, with counts', () => {
    const plan = readAloudPlan(form, { safe: ['temp', 'browser', 'thumbs', 'bin'], out: 'hp', night: true, name: 'My laptop', qty: 5, repeat: 'weekly' }, null)
    expect(plan.spoken).toContain('Clean up this PC')
    expect(plan.spoken).toContain('Tick what to clean')
    expect(plan.spoken).toContain('Safe to clean: 5 options, 4 ticked: Temp files, Browser cache and 2 more')
    expect(plan.spoken).toContain('Review first: 2 options, none ticked')
    expect(plan.spoken).toContain('Output: Headphones')
    expect(plan.spoken).toContain('Night light: on')
    expect(plan.spoken).toContain('Device name: My laptop')
    expect(plan.spoken).toContain('Buttons: Clean selected, Scan again, Close')
    expect(plan.notes).toEqual([])
  })

  it('speaks Telugu only when its voice is verified; otherwise shows it and says which', () => {
    const v = { name: 'నా ల్యాప్‌టాప్', safe: [] }
    const off = readAloudPlan(form, v, { te: { speak: false, note: 'The reply is shown in Telugu but not spoken: the voice is installed but not yet verified by the owner.' } })
    expect(off.shown).toContain('నా ల్యాప్‌టాప్')
    expect(off.spoken).not.toContain('ల్యాప్')
    expect(off.spoken).toContain('text in Telugu')
    expect(off.notes.join(' ')).toContain('not yet verified')
    const on = readAloudPlan(form, v, { te: { speak: true, note: '' } })
    expect(on.spoken).toContain('నా ల్యాప్‌టాప్')
    expect(on.notes).toEqual([])
    const unknown = readAloudPlan(form, v, null)
    expect(unknown.spoken).not.toContain('ల్యాప్')
    expect(unknown.notes.join(' ')).toContain('Telugu text is shown, not spoken')
  })

  it('does not read a field that asks for a secret', () => {
    const f = { ...form, fields: [{ type: 'text', id: 'p', label: 'PIN' } as AskNode, ...FIELDS] }
    expect(readAloudPlan(f, { p: '1234' }, null).shown).not.toContain('1234')
  })
})

describe('actionsFor with a chosen target (after a question or the decision engine)', () => {
  it('uses the intent with the target that was picked', () => {
    const c = ctx()
    const target = c.targets.find(t => t.key === 'o:safe/wintemp')!
    expect(actionsFor({ type: 'on', phrase: 'temp', all: false }, target, c)).toMatchObject({ kind: 'actions', actions: [{ type: 'check', optionId: 'wintemp', on: true }] })
    expect(actionsFor({ type: 'off', phrase: 'temp', all: false }, target, c)).toMatchObject({ kind: 'actions', actions: [{ type: 'check', optionId: 'wintemp', on: false }] })
  })
})
