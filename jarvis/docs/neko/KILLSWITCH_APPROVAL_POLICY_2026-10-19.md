# Kill switch and approvals granted before it: policy analysis (2026-10-19)

Status: ANALYSIS ONLY. The behaviour is unchanged (alternative A, "keep"). It is pinned by
`test_approval_hardening.py::KillSwitch::test_an_approval_given_before_the_kill_is_still_not_usable_during_it_and_usable_after`.
The decision is the owner's. No environment switch was added: the change is one statement (below) and a switch would only add a second
code path to a security control.

## What happens today

- While the kill switch is on, **no approval can be granted** (owner HTTP, Telegram, CLI and `Kernel.approve` all refuse; deny and
  cancel still work) and **no approval can be used** (`Kernel.authorize` refuses everything while killed).
- `resume()` withdraws approvals that were *decided during* the kill (a race or a hand-edited database) and audits
  `approvals_withdrawn`.
- An approval granted *before* the kill and not yet used is left alone. After `resume` it is usable again, once, for exactly the action,
  agent, lane and task it was bound to.
- Every approval expires `APPROVAL_TTL` = 600 s after it was **requested** (the grant does not extend it). So the exposure of
  alternative A is bounded: an approved-unused card survives a kill only for what remains of those ten minutes. A long kill (more than
  ten minutes) therefore already ends every earlier approval by expiry. The window that matters is a short kill: the owner presses Kill
  because something looks wrong and resumes within minutes.

## The alternatives

| | Rule at `kill()` | Attack it stops | What it costs the owner |
|---|---|---|---|
| A (today) | nothing is revoked | none beyond what the TTL already does | nothing: a short kill/resume loses no work |
| B | every unused approval (approved, and optionally pending) is denied | an agent that was *already* approved and is the thing the owner suspects cannot finish its action after resume; also covers "the approval was granted by someone who held the PIN for a moment" | after every kill the owner re-approves everything that was waiting; an accidental Kill costs every waiting card, each needs a new PIN + typed id |
| C | only approvals for side-effect classes (pc/ui/connector write+send/money/shell/project.apply_patch/lab share, i.e. anything that is not a read or a file save in the owner's outputs) are denied | the same attack, but only where it can do harm | reads and saves survive, so fewer re-approvals; needs a maintained list of "side-effect classes" (it must fail closed: an unknown tool counts as a side effect) |
| C+ | like C (or B) but only approvals granted more than N minutes before the kill (for example N = 2) | the "late kill" case: the owner noticed too late | very recent approvals (the owner clicked a second ago and then killed) survive; an attacker who acts inside the N minutes is not stopped; the number N is a judgement call |

## One-line changes (all in `Kernel.kill`, `jarvis_kernel/store.py`)

```sql
-- B
UPDATE approvals SET state='denied', decided=:now, reason='withdrawn: kill switch' WHERE state='approved';
-- C
... WHERE state='approved' AND level>=2 AND tool_class IN (...)   -- tool is inside the stored action JSON, so use json_extract(action,'$.tool')
-- C+
... WHERE state='approved' AND decided < :now - 120
```
Each also needs one audit row (`approvals_withdrawn`, the count) and one test that flips the pinned test above (the pinned test would
change on purpose: it is the test the owner must approve changing).

## Recommendation

Keep A while approvals stay at ten minutes. The risk A leaves is "a second ten-minute window after a short kill", for an action the
owner *already read and approved*, bound to the exact text and agent. If the owner wants a stricter default, take **B for approved-unused
cards** (simple, fail-closed, no list to maintain); the price is only re-approving after a kill, which is rare and is what a kill
means. C and C+ add rules to maintain for little gain over B.

## Owner decision needed

Choose A (nothing to do), B, C or C+. Until then A is in force and tested.
