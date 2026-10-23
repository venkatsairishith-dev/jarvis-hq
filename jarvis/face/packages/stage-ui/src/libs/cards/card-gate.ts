/**
 * JARVIS: the gate every imported character card passes.
 *
 * In 2025 AIRI rendered a character card's text as HTML without cleaning it, and a crafted card ran script in the app
 * (GHSA-9832-f8jx-hw6f). The rendering is cleaned now (DOMPurify), but a card's text should never need markup at all,
 * so on import every text in the card is reduced to plain text and capped in length. What is stored holds nothing that
 * a page could run, even if a later screen forgets to clean what it shows.
 *
 * Markdown, `{{char}}`-style placeholders, quotes, `<` and `>` used as comparison signs, and every language stay as they
 * are. Removed: HTML tags and comments, `javascript:`/`vbscript:`/`data:text/html` links, and control characters.
 */

/** The longest text one field of an imported card may hold. Longer text is cut, and counted. */
export const MAX_CARD_TEXT = 60_000
/** The most texts, objects and list entries one card may hold in total. A bigger card is refused. */
export const MAX_CARD_NODES = 5_000

export interface CardGateReport {
  /** HTML tags and comments taken out. */
  tagsRemoved: number
  /** Dangerous links taken out. */
  linksRemoved: number
  /** Texts cut to {@link MAX_CARD_TEXT}. */
  textsCut: number
}

export class CardGateError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'CardGateError'
  }
}

// A tag starts with `<` and a letter, `/`, `!` or `?`: "a < b" and "x<3" are not tags.
const TAG = /<\/?[a-z!?][^>]*>?/gi
const COMMENT = /<!--[\s\S]*?(?:-->|$)/g
const DANGEROUS_LINK = /\b(?:javascript|vbscript)\s*:|data\s*:\s*text\/html/gi
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F​-‏‪-‮⁦-⁩]/g

/** One text reduced to plain text. */
export function plainCardText(text: string, report?: CardGateReport): string {
  let next = text
  // Repeat until nothing changes, so "<scr<script>ipt>" cannot rebuild a tag after the inner one is removed.
  for (let pass = 0; pass < 8; pass++) {
    const before = next
    next = next.replace(COMMENT, () => {
      if (report)
        report.tagsRemoved++
      return ''
    }).replace(TAG, () => {
      if (report)
        report.tagsRemoved++
      return ''
    }).replace(DANGEROUS_LINK, () => {
      if (report)
        report.linksRemoved++
      return ''
    })
    if (next === before)
      break
  }
  next = next.replace(CONTROL, '')
  if (next.length > MAX_CARD_TEXT) {
    if (report)
      report.textsCut++
    next = next.slice(0, MAX_CARD_TEXT)
  }
  return next
}

/**
 * A copy of `card` with every text reduced to plain text. Keys, numbers, booleans and structure are unchanged.
 * Throws {@link CardGateError} for a card with more than {@link MAX_CARD_NODES} parts.
 */
export function gateImportedCard<T>(card: T): { card: T, report: CardGateReport } {
  const report: CardGateReport = { tagsRemoved: 0, linksRemoved: 0, textsCut: 0 }
  let nodes = 0
  const walk = (value: unknown, depth: number): unknown => {
    if (++nodes > MAX_CARD_NODES)
      throw new CardGateError('The card has too many parts.')
    if (depth > 24)
      throw new CardGateError('The card is nested too deeply.')
    if (typeof value === 'string')
      return plainCardText(value, report)
    if (Array.isArray(value))
      return value.map(item => walk(item, depth + 1))
    if (value && typeof value === 'object') {
      const copy: Record<string, unknown> = {}
      for (const [key, item] of Object.entries(value)) {
        if (key === '__proto__' || key === 'constructor' || key === 'prototype')
          continue
        copy[plainCardText(key)] = walk(item, depth + 1)
      }
      return copy
    }
    return value
  }
  return { card: walk(card, 0) as T, report }
}
