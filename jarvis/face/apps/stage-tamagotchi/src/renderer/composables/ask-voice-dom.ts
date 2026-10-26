/**
 * The panel's side of voice focus (D63): which field has the keyboard focus, which comes next, and bringing a control that voice just changed
 * into view. Fields mark themselves with `data-voice-input="<field id>"`, controls with `data-voice-key="<key>"`.
 */
import type { FocusPort } from './use-jarvis-ask-voice'

const FOCUSABLE = '.ask-body input:not(:disabled):not([type=hidden]), .ask-body select:not(:disabled), .ask-body textarea:not(:disabled), .ask-body button:not(:disabled), .ask-body [role=radio][tabindex="0"], .ask-body [role=tab][tabindex="0"], .ask-body [role=row][tabindex="0"], .ask-foot button:not(:disabled)'

function visible(el: HTMLElement): boolean {
  return el.getClientRects().length > 0
}

function nameOf(el: HTMLElement): string {
  const own = el.getAttribute('aria-label')
  if (own)
    return own.replace(/\s*\(Alt\+\w\)\s*$/, '')
  const id = el.getAttribute('id')
  const label = id ? el.ownerDocument.querySelector(`label[for="${CSS.escape(id)}"]`) : null
  return (label?.textContent ?? el.closest('label')?.textContent ?? el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 60)
}

export function createDomFocus(root: () => HTMLElement | null): FocusPort {
  const inputs = (): HTMLElement[] => [...(root()?.querySelectorAll<HTMLElement>('[data-voice-input]') ?? [])].filter(visible)
  return {
    focusField(id) {
      const el = inputs().find(x => x.dataset.voiceInput === id)
      if (!el)
        return false
      el.focus({ preventScroll: false })
      return true
    },
    adjacentField(from, direction) {
      const ids = inputs().map(x => x.dataset.voiceInput!)
      const at = from ? ids.indexOf(from) : -1
      const to = direction === 'next' ? at + 1 : at - 1
      return to >= 0 && to < ids.length ? ids[to] : null
    },
    moveFocus(direction) {
      const items = [...(root()?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [])].filter(visible)
      if (!items.length)
        return ''
      const at = items.indexOf(root()!.ownerDocument.activeElement as HTMLElement)
      const to = at < 0 ? 0 : (at + (direction === 'next' ? 1 : -1) + items.length) % items.length
      items[to].focus()
      return nameOf(items[to])
    },
    focusedField() {
      const active = root()?.ownerDocument.activeElement
      const el = active instanceof HTMLElement ? active.closest<HTMLElement>('[data-voice-input]') : null
      return el?.dataset.voiceInput ?? null
    },
    reveal(key) {
      const el = root()?.querySelector<HTMLElement>(`[data-voice-key="${CSS.escape(key)}"]`)
      el?.scrollIntoView?.({ block: 'nearest' })
    },
  }
}
