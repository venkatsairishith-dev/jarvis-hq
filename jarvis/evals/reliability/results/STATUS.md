# JARVIS reliability: where each thing stands against 90%

Written 2026-10-27 20:51. A bar fills to the measured pass rate; the : is the 90% line. 'not measured yet' means no run has been made: it is not a good or a bad number.

**0 of 16 capabilities are at 90% or better** with zero unsafe actions.

| capability | my guess before | measured | | status | runs | from |
|---|---:|---:|---|---|---:|---|
| Open, type, save and click | 75% | 0% | `[..................:..]` | incomplete | 2 | reliability-2026-10-27.json |
| Reading the screen (text) | 80% | not measured yet | `[....................]` | - | 0 | - |
| Reading pictures on the screen (OCR / vision) | 50% | not measured yet | `[....................]` | - | 0 | - |
| The final answer arrives | 80% | not measured yet | `[....................]` | - | 0 | - |
| Delete, restore and safe cleanup | 85% | not measured yet | `[....................]` | - | 0 | - |
| Cleaner panel | 85% | not measured yet | `[....................]` | - | 0 | - |
| Generated decks, reports and the Preview panel | 80% | not measured yet | `[....................]` | - | 0 | - |
| Window responsiveness | 60% | 100% | `[##################:##]` | incomplete | 30 | reliability-2026-10-26.json |
| Telugu and Hindi in their own scripts | 60% | not measured yet | `[....................]` | - | 0 | - |
| Tenglish and Hinglish (Telugu / Hindi in English letters) | 40% | not measured yet | `[....................]` | - | 0 | - |
| The local MoE model as the only brain | 30% | not measured yet | `[....................]` | - | 0 | - |
| Ambiguous requests are asked about, not guessed | - | not measured yet | `[....................]` | - | 0 | - |
| Class-C requests stop for the PIN | - | not measured yet | `[....................]` | - | 0 | - |
| Simple everyday tasks (catalogue) | - | not measured yet | `[....................]` | - | 0 | - |
| Solving a problem shown on the screen | - | not measured yet | `[....................]` | - | 0 | - |
| Problem recovery: the PC fights back | 50% | not measured yet | `[....................]` | - | 0 | - |

## What is holding each one back

- **Open, type, save and click**: 0 of 13 goals have 5+ valid runs; 71% of the runs were invalid (the harness could not run them). Top failure causes: no_tool_call x1, timeout x1. Lever: tool routing in the face persona (D52 request diet); language gate for Telugu/Hindi
- **Window responsiveness**: 0 of 10 goals have 5+ valid runs. Top failure causes: 0 of 10 goals have 5+ valid runs.

## Not measured yet

Reading the screen (text), Reading pictures on the screen (OCR / vision), The final answer arrives, Delete, restore and safe cleanup, Cleaner panel, Generated decks, reports and the Preview panel, Telugu and Hindi in their own scripts, Tenglish and Hinglish (Telugu / Hindi in English letters), The local MoE model as the only brain, Ambiguous requests are asked about, not guessed, Class-C requests stop for the PIN, Simple everyday tasks (catalogue), Solving a problem shown on the screen, Problem recovery: the PC fights back.

The final message arrived in 50% of 2 task runs. False claims of success: 0.
