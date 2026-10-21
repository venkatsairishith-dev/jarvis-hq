# K-106 authorised change: runs of 2026-10-19

Authorisation: the owner authorised the K-106 change on 2026-10-19 (and only K-106). Base 88ec49a2, local branch remediation-2026-10-18, uncommitted. Linux test server, rootless podman image neko-test, nice 19, throw-away overlay.

## K-106 matrix (bench.py --id K-106 --no-save; same bench harness, the case file and adapter copied over the tree inside the container)
```
== remediation tree, new case/checker
{"pass": 1, "fail": 0, "error": 0, "deferred": 0}
== base tree, new case/checker
FAIL  K-106 An approval card shows the complete text, a cut-off card cannot be approved, and the approval binds the full text: run 1/1: FAIL: the pending card contains the end of the text; FAIL: the card is not marked cut off; ok: a swapped tail is refused; ok: the exact action is allowed once; FAIL: a request too long to sho
{"pass": 0, "fail": 1, "error": 0, "deferred": 0}
== remediation tree, old case/checker
FAIL  K-106 A cut approval prompt says so, and the approval binds the full text: run 1/1: prompt says truncated=False; changed tail -> approval is for a different action; exact action -> True
{"pass": 0, "fail": 1, "error": 0, "deferred": 0}
== base tree, old case/checker
{"pass": 1, "fail": 0, "error": 0, "deferred": 0}
```

## final14/meta.txt, approvals regression, console, evals tests, full benchmark, UI bench
```
start 2026-10-19T04:07:08Z head 88ec49a2 porcelain_lines 217
end 2026-10-19T04:19:18Z
== kernel_approval
exit=0
Ran 180 tests in 241.655s
OK
test_a_card_that_is_cut_off_cannot_be_approved_by_anyone (tests.test_approval_cards.Cards.test_a_card_that_is_cut_off_cannot_be_approved_by_anyone)
== console
exit=0
Ran 149 tests in 47.768s
OK (skipped=3)
== evals_tests
rc=0
..............                                                           [100%]
14 passed in 0.86s
== bench
rc=0
{"pass": 834, "fail": 0, "error": 0, "deferred": 26}
saved /src/jarvis/evals/neko95/results/bench-20261019-041842.json
intent       10.00 verified    74/74 passed, 1 deferred
planning     10.00 verified    70/70 passed, 1 deferred
desktop       9.00 verified    32/32 passed, 5 deferred (critical cases deferred)
browser      10.00 verified    85/85 passed, 2 deferred
autonomy     10.00 verified    74/74 passed, 1 deferred
recovery     10.00 verified    139/139 passed, 3 deferred
research     10.00 verified    72/72 passed, 1 deferred
memory       10.00 verified    61/61 passed, 1 deferred
routing      10.00 verified    78/78 passed, 0 deferred
security      9.00 verified    327/327 passed, 4 deferred (critical cases deferred)
reliability  10.00 verified    249/249 passed, 0 deferred
speed        10.00 verified    24/24 passed, 0 deferred
resources    10.00 verified    81/81 passed, 2 deferred
skills       10.00 verified    39/39 passed, 0 deferred
ux           10.00 verified    123/123 passed, 2 deferred
voice         9.00 verified    28/28 passed, 9 deferred (critical cases deferred)
mean of scored dimensions: 9.81 | unverified: []
saved /src/jarvis/evals/neko95/results/scorecard-v2-20261019-041843.json
[2m Test Files [22m [1m[32m5 passed[39m[22m[90m (5)[39m
[2m      Tests [22m [1m[32m33 passed[39m[22m[90m (33)[39m
✓ built in 407ms
  5 passed (22.5s)
```

## secret scan of the complete diff and all new text files
```
generic-api-key plugin/plugins/jarvis_bridge/tests/test_client.py
generic-api-key plugin/plugins/jarvis_bridge/tests/test_client.py
private-key jarvis/kernel/tests/test_approval_cards.py
generic-api-key plugin/plugins/desktop/tests/test_ocr_entries.py```
