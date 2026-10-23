import { useLocalStorageManualReset } from '@proj-airi/stage-shared/composables'
import { defineStore } from 'pinia'
import { computed } from 'vue'

/** One remembered fact. A character keeps it on its own card. A general memory reaches every card. */
export interface MemoryEntry {
  /** Short name in kebab case, unique within its scope. The index lists it, and tools address the entry by it. */
  name: string
  /** The index line: the fact itself when it fits in one line, or when the entry matters. */
  description: string
  body: string
  /** The character card that owns this memory. Without it, the owner made the memory general. */
  persona?: string
  updatedAt: number
  /**
   * JARVIS: a fact the model suggested. It waits for the owner, and neither the index nor a read shows it to the model
   * until the owner accepts it in Settings. This keeps text from a web page or an email from becoming a lasting "memory".
   */
  proposed?: boolean
}

/** Turns a free name into the kebab-case key that entries use. */
export function memoryName(name: string) {
  return name.trim().toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-+|-+$/g, '').slice(0, 64)
}

/**
 * Long-term memory as a small index and one entry per fact.
 *
 * Use when:
 * - A run reads the index or an entry, suggests a fact, or Settings lists, accepts, edits and removes memories.
 *
 * Expects:
 * - Callers pass the character card of the run. It reads its own accepted memories and the general ones.
 * - JARVIS: a run only suggests ({@link propose}); the owner accepts in Settings. A run can withdraw its own suggestions
 *   but cannot erase an accepted memory. Only the owner makes a memory general.
 *
 * Returns:
 * - Whether memory is on, entries, the index for a card, and actions that change entries.
 */
export const useMemoryStore = defineStore('memory', () => {
  const enabled = useLocalStorageManualReset<boolean>('memory/enabled', true)
  const entries = useLocalStorageManualReset<MemoryEntry[]>('memory/entries', [])

  const sorted = computed(() => [...entries.value].sort((left, right) => left.name.localeCompare(right.name)))

  /** Accepted memories only: the model never sees a suggestion that the owner has not accepted. */
  const accepted = computed(() => sorted.value.filter(entry => !entry.proposed))
  /** Suggestions that wait for the owner. */
  const proposals = computed(() => sorted.value.filter(entry => entry.proposed))

  /** General memories and the persona's own. */
  function visibleTo(personaId: string) {
    return accepted.value.filter(entry => !entry.persona || entry.persona === personaId)
  }

  /** The index for a card: its own memories, then general ones, each as a name and its description. */
  function indexFor(personaId: string) {
    const line = (entry: MemoryEntry) => `- ${entry.name}: ${entry.description}`
    const byName = (left: MemoryEntry, right: MemoryEntry) => left.name.localeCompare(right.name)
    const own = accepted.value.filter(entry => entry.persona === personaId).sort(byName).map(line)
    const general = accepted.value.filter(entry => !entry.persona).sort(byName).map(line)
    return [
      ...(own.length ? [`Your own:\n${own.join('\n')}`] : []),
      ...(general.length ? [`General:\n${general.join('\n')}`] : []),
    ].join('\n')
  }

  /** Reads an entry by name. The persona's own entry wins over a general one with the same name. */
  function read(name: string, personaId: string) {
    const key = memoryName(name)
    const matches = visibleTo(personaId).filter(entry => entry.name === key)
    return matches.find(entry => entry.persona === personaId) ?? matches[0]
  }

  function store(entry: Pick<MemoryEntry, 'name' | 'description' | 'body'>, personaId: string, proposed: boolean) {
    const name = memoryName(entry.name)
    if (!name || !entry.body.trim())
      return undefined
    const next: MemoryEntry = { name, description: entry.description.trim(), body: entry.body.trim(), persona: personaId, updatedAt: Date.now(), ...(proposed ? { proposed: true } : {}) }
    entries.value = [...entries.value.filter(existing => existing.name !== name || existing.persona !== personaId), next]
    return next
  }

  /** The owner saves a fact (from Settings): it is accepted at once. Returns the entry, or undefined for an empty name or body. */
  function write(entry: Pick<MemoryEntry, 'name' | 'description' | 'body'>, personaId: string) {
    return store(entry, personaId, false)
  }

  /**
   * A run suggests a fact. It waits for the owner. A suggestion never replaces an accepted memory of the same name:
   * it is refused, so a run cannot quietly rewrite what the owner accepted.
   */
  function propose(entry: Pick<MemoryEntry, 'name' | 'description' | 'body'>, personaId: string) {
    const name = memoryName(entry.name)
    if (entries.value.some(existing => existing.name === name && existing.persona === personaId && !existing.proposed))
      return undefined
    return store(entry, personaId, true)
  }

  /** The owner accepts a suggestion. */
  function accept(entry: Pick<MemoryEntry, 'name' | 'persona'>) {
    const target = entries.value.find(existing => existing.name === entry.name && existing.persona === entry.persona && existing.proposed)
    if (!target)
      return false
    const { proposed: _proposed, ...kept } = target
    entries.value = entries.value.map(existing => existing === target ? { ...kept, updatedAt: Date.now() } : existing)
    return true
  }

  /** Makes a card's memory general, so every card reads it. A general memory with the same name gives way. */
  function makeGeneral(entry: Pick<MemoryEntry, 'name' | 'persona'>) {
    const source = entries.value.find(existing => existing.name === entry.name && existing.persona === entry.persona && !existing.proposed)
    if (!source?.persona)
      return
    const { persona: _card, ...general } = source
    entries.value = [...entries.value.filter(existing => existing !== source && (existing.name !== source.name || existing.persona)), { ...general, updatedAt: Date.now() }]
  }

  /** Removes one entry, from a tool or from Settings. */
  function remove(entry: Pick<MemoryEntry, 'name' | 'persona'>) {
    entries.value = entries.value.filter(existing => existing.name !== entry.name || existing.persona !== entry.persona)
  }

  /**
   * A run withdraws one of its own suggestions by name. Accepted memories and general ones belong to the owner, so a run
   * cannot forget them: the owner removes them in Settings.
   */
  function forget(name: string, personaId: string) {
    const key = memoryName(name)
    const target = entries.value.find(entry => entry.name === key && entry.persona === personaId && entry.proposed)
    if (!target)
      return false
    remove(target)
    return true
  }

  function resetState() {
    enabled.reset()
    entries.reset()
  }

  return {
    enabled,
    entries: sorted,
    accepted,
    proposals,
    visibleTo,
    indexFor,
    read,
    write,
    propose,
    accept,
    makeGeneral,
    forget,
    remove,
    resetState,
  }
})
