# JARVIS reliability: where each thing stands against 90%

Written 2026-10-27 20:35. A bar fills to the measured pass rate; the : is the 90% line. 'not measured yet' means no run has been made: it is not a good or a bad number.

**0 of 16 capabilities are at 90% or better** with zero unsafe actions.

| capability | my guess before | measured | | status | runs | from |
|---|---:|---:|---|---|---:|---|
| Open, type, save and click | 75% | 25% | `[#####.............:..]` | incomplete | 20 | reliability-2026-10-27.json |
| Reading the screen (text) | 80% | not measured yet | `[....................]` | - | 0 | - |
| Reading pictures on the screen (OCR / vision) | 50% | not measured yet | `[....................]` | - | 0 | - |
| The final answer arrives | 80% | not measured yet | `[....................]` | - | 0 | - |
| Delete, restore and safe cleanup | 85% | 0% | `[..................:..]` | incomplete | 12 | reliability-2026-10-27.json |
| Cleaner panel | 85% | not measured yet | `[....................]` | - | 0 | - |
| Generated decks, reports and the Preview panel | 80% | not measured yet | `[....................]` | - | 0 | - |
| Window responsiveness | 60% | 100% | `[##################:##]` | incomplete | 30 | reliability-2026-10-26.json |
| Telugu and Hindi in their own scripts | 60% | not measured yet | `[....................]` | - | 0 | - |
| Tenglish and Hinglish (Telugu / Hindi in English letters) | 40% | not measured yet | `[....................]` | - | 0 | - |
| The local MoE model as the only brain | 30% | not measured yet | `[....................]` | - | 0 | - |
| Ambiguous requests are asked about, not guessed | - | not measured yet | `[....................]` | - | 0 | - |
| Class-C requests stop for the PIN | - | not measured yet | `[....................]` | - | 0 | - |
| Simple everyday tasks (catalogue) | - | 25% | `[#####.............:..]` | incomplete | 12 | reliability-2026-10-27.json |
| Solving a problem shown on the screen | - | not measured yet | `[....................]` | - | 0 | - |
| Problem recovery: the PC fights back | 50% | not measured yet | `[....................]` | - | 0 | - |

## What is holding each one back

- **Open, type, save and click**: 0 of 13 goals have 5+ valid runs. Top failure causes: timeout x5, no_tool_call x4, file_missing x3. Lever: latency report (evals/reliability/latency.py): thinking off, prompt cache, no second request, parallel independent steps
- **Delete, restore and safe cleanup**: 0 of 12 goals have 5+ valid runs. Top failure causes: timeout x6, file_still_exists x3, wrong_file_count x1. Lever: latency report (evals/reliability/latency.py): thinking off, prompt cache, no second request, parallel independent steps
- **Window responsiveness**: 0 of 10 goals have 5+ valid runs. Top failure causes: 0 of 10 goals have 5+ valid runs.
- **Simple everyday tasks (catalogue)**: 0 of 12 goals have 5+ valid runs. Top failure causes: timeout x4, no_tool_call x2, asked_when_it_should_act x2. Lever: latency report (evals/reliability/latency.py): thinking off, prompt cache, no second request, parallel independent steps

## Not measured yet

Reading the screen (text), Reading pictures on the screen (OCR / vision), The final answer arrives, Cleaner panel, Generated decks, reports and the Preview panel, Telugu and Hindi in their own scripts, Tenglish and Hinglish (Telugu / Hindi in English letters), The local MoE model as the only brain, Ambiguous requests are asked about, not guessed, Class-C requests stop for the PIN, Solving a problem shown on the screen, Problem recovery: the PC fights back.

The final message arrived in 62% of 42 task runs. False claims of success: 2.
