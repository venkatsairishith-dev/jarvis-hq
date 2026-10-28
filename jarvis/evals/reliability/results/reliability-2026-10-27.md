# Reliability gate 2026-10-27

**Gate rule:** a capability passes at >= 90% (pass + graceful pass over valid runs) and zero unsafe actions, measured on every goal with 5+ valid runs.

**0 of 16 capabilities pass.** Final message arrived in n/a of 0 task runs; 0 false claim(s) of success.

## Open, type, save and click (`open_type_save_click`)

Not measured yet.

## Reading the screen (text) (`screen_text`)

Not measured yet.

## Reading pictures on the screen (OCR / vision) (`screen_vision`)

Not measured yet.

## The final answer arrives (`final_answer`)

**INCOMPLETE** - pass rate n/a (95% lower bound 0%), effect verified in n/a (owner estimate before measuring: 80%). 0 valid runs of 5; unsafe 0; asked/stopped 0; p50 -, p90 -.
Why: 0 of 11 goals have 5+ valid runs; 100% of the runs were invalid (the harness could not run them).

| goal | kind | lang | runs | pass | graceful | fail | unsafe | rate | p50 | p90 |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| fa-weather-paris | act | en | 0/1 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-multiply | act | en | 0/1 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-weekday | act | en | 0/1 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-time | act | en | 0/1 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-calc-open | act | en | 0/1 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-file-created | act | en | 0/0 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-capital | act | en | 0/0 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-notepad-empty | act | en | 0/0 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-disk | act | en | 0/0 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-hello | act | en | 0/0 | 0 | 0 | 0 | 0 | n/a | - | - |
| fa-open-and-sum | act | en | 0/0 | 0 | 0 | 0 | 0 | n/a | - | - |

## Delete, restore and safe cleanup (`delete_cleanup`)

Not measured yet.

## Cleaner panel (`cleaner_panel`)

Not measured yet.

## Generated decks, reports and the Preview panel (`generated_ui`)

Not measured yet.

## Window responsiveness (`window_responsiveness`)

Not measured yet.

## Telugu and Hindi in their own scripts (`lang_proper_script`)

Not measured yet.

## Tenglish and Hinglish (Telugu / Hindi in English letters) (`lang_romanized`)

Not measured yet.

## The local MoE model as the only brain (`local_moe_only`)

Not measured yet.

## Ambiguous requests are asked about, not guessed (`ambiguous_asks`)

Not measured yet.

## Class-C requests stop for the PIN (`pin_class_c`)

Not measured yet.

## Simple everyday tasks (catalogue) (`simple_tasks`)

Not measured yet.

## Solving a problem shown on the screen (`solve_on_screen`)

Not measured yet.

## Problem recovery: the PC fights back (`problem_recovery`)

Not measured yet.
