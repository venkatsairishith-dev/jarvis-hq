/**
 * What the owner must never say aloud, and what voice never does (D63). One place, used by the voice store's command hook (every spoken input)
 * and by the ask panel's voice grammar.
 *
 *   - A PIN or password SAID ("my pin is 4821", "pin 4821", "password is hunter2", "otp 123456") is a secret: it is dropped before it reaches a
 *     model, a log, a draft, a form field or a button. PINs are typed in the JARVIS console, which this code can not reach.
 *   - Speech that approves something ("approve it", "authorize the payment") is never taken as an approval. Approvals happen in the console.
 *   - "pin code 500081" (a postal code) and "what is a pin" are not secrets.
 * The backend has the same patterns (jarvis/bridge/ask_voice.py); a test keeps the two lists of examples equal.
 */
const JOINERS = /[\u200B-\u200D\u2060\uFEFF]/g

const SECRET_WORDS = '(?:pin|passcode|password|passwd|otp|one[- ]?time (?:code|password)|పిన్|పాస్వర్డ్|ఓటీపీ|पिन|पासवर्ड|पासकोड|ओटीपी)'
const PASSWORD_WORDS = '(?:password|passwd|passcode|పాస్వర్డ్|पासवर्ड|पासकोड)'
const NUMBER_WORDS = '(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|ten)'
// A letter, mark or digit on either side would make it part of another word (`\b` only knows ASCII).
const before = (words: string): string => `(?<![\\p{L}\\p{M}\\p{N}])${words}(?![\\p{L}\\p{M}\\p{N}])`
// a PIN or password SAID: the word followed (within a few words) by digits, spoken digits or "= ..."; "pin code 500081" (a postal code) and "what is a pin" are not.
const SECRET_SPEECH = new RegExp([
  `${before(SECRET_WORDS)}(?!\\s*code\\b)[^.!?]{0,24}?(?:\\d[\\d\\s-]{2,}|\\bis\\s+\\S*\\d|\\bis\\s+${NUMBER_WORDS}\\b|\\bequals\\b|=)`,
  `${before(PASSWORD_WORDS)}\\s+is\\s+\\S+`,
  `${before(SECRET_WORDS)}\\s+(?:number|నంబర్|नंबर)\\s*\\d`,
].join('|'), 'iu')
const APPROVAL_SPEECH = /\b(?:approve[ds]?|approval|authori[sz]e[ds]?)\b|అప్రూవ్|अप्रूव|मंज़ूर|मंजूर|అనుమతించు/iu
export const CONSOLE_ONLY = 'PINs and approvals are only given in the JARVIS console. Type the PIN there; never say it aloud.'

export function speechRefusal(text: string): 'secret' | 'approval' | null {
  const t = String(text ?? '').replace(JOINERS, '')
  if (SECRET_SPEECH.test(t))
    return 'secret'
  if (APPROVAL_SPEECH.test(t))
    return 'approval'
  return null
}
