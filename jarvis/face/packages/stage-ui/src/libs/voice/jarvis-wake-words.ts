import type { WakeWord } from './wake-words'

import { kwsModel } from './kws-model-info'

/**
 * JARVIS answers to "Hey JARVIS" and to "JARVIS" alone.
 *
 * The pronunciations are tokens of the pinned keyword pack (English ARPAbet with stress marks, see `kws-vocabulary.json`). Each word has two:
 * "JARVIS" is said with a short "i" or a schwa in the second syllable, and the detector wakes on either.
 */
export const JARVIS_WAKE_WORDS: readonly WakeWord[] = Object.freeze([
  {
    text: 'Hey JARVIS',
    modelId: kwsModel.id,
    pronunciations: [
      ['HH', 'EY1', 'JH', 'AA1', 'R', 'V', 'IH0', 'S'],
      ['HH', 'EY1', 'JH', 'AA1', 'R', 'V', 'AH0', 'S'],
    ],
  },
  {
    text: 'JARVIS',
    modelId: kwsModel.id,
    pronunciations: [
      ['JH', 'AA1', 'R', 'V', 'IH0', 'S'],
      ['JH', 'AA1', 'R', 'V', 'AH0', 'S'],
    ],
  },
])
