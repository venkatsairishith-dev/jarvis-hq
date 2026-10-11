# 08. Owner handoff (2026-10-11, final verification and provider audit)

Written for the owner of JARVIS. It says what is done and proven, what is only partly proven, what only you can do, what the model providers really allow, and how to recover. Evidence for every number is in `docs/research/07-integration-decisions.md` section 6 and section 7; decisions are in `docs/03_DECISIONS.md` D16.

## 1. Status at a glance

**Verified** (process output and exit status in hand, 2026-10-11)
- Kernel test suite on Windows, project Python (jarvis-venv 3.12): run 1 = 396 run, OK, 24 skipped, exit 0. Run 2 = 396 run, OK, 24 skipped, exit 0. The 24 skipped browser-job tests were run separately in the browser venv: 35 run, OK, 0 skipped, exit 0.
- Kernel suite on the server, in place, at the last deploy: 396 run, OK, 39 skipped (Linux-only and Telegram-off cases), exit 0.
- Browser suite 51 passed (exit 0); console suite 28 OK (exit 0, three consecutive runs after the fix in 7.3); evals 23/23 (exit 0); WSL repair-loop tests 3 OK (exit 0); Oracle cost this month 0.0. All re-run in this pass.
- Server and laptop kernel trees are byte-identical (22 files, SHA-256 compared); `providers.json` identical on both.
- Services: `jarvis-llm-proxy`, `jarvis-kernel`, `jarvis-hermes-gateway` active; no queued or running job, no running owner command, no open task, no pending approval.
- Telegram is off: `config/telegram.off` exists on the laptop repo and the server; no Telegram process.
- Console: one listening process on 127.0.0.1:8799. Laptop node: one process pair, last seen seconds ago.
- Wake word: not implemented in the console or the UI (no code path), so it cannot be enabled by accident. Measurements are in `bench/wakeword/`.
- Live, earlier today: owner token never present in any console response, foreign Origin / missing header / rebinding Host / API-plane and traversal attempts refused, kill switch one-way from the browser, a stopped-waiting or failed command frees the gate.

**Partially verified**
- Approvals: the whole path (typed id, PIN, single use, changed action refused, replay refused, expiry, kill switch, RED not approvable over HTTP, audit chain) is proven on a throwaway kernel and in the browser. Live, only the display and denial were exercised, because no PIN exists.
- Hindi and Telugu speech: synthetic round trips and the scoring tool are proven; real-microphone accuracy is not measured.
- Self-correction: the repaired-and-verified path is proven with the real runner and a scripted worker and by one natural live repair; the live deliberate-failure drill ended `failed` in 3 attempts with nothing delivered and no lesson, but its last verifier produced no verdict.
- Private lane capacity: behaviour when providers rest is verified; the lane itself has one usable provider with a 50 request/day cap (section 3).

**Unverified**
- That "DHAI" in your original brief means Dahl (probable, see 3.1; needs your one-word confirmation).
- The Dahl pool balance and ledger (they need an account sign-in, not an API key); only the balance of the key JARVIS holds is known.
- Whether your OpenRouter account's privacy toggles forbid training/logging endpoints (account setting; section 3.4).
- Real-microphone false-activation rate of any wake word.

**Owner action required** (section 4): confirm "DHAI = Dahl"; set the approval PIN and run the live approval once; speak the Voice check phrases in three languages; decide the private-lane provider.

**Failed or skipped tests, all of them**
- Windows kernel run 1 and run 2: no failures. 24 skipped in jarvis-venv (all `test_browser`, "playwright not installed in this Python"); the same file ran in the browser venv with 0 skipped.
- Server in place: 39 skipped (tests that need Windows, psutil or the Windows adapter, plus Telegram-off cases); 0 failed.
- Earlier today one Windows run had one error in `test_vault` (temporary-folder cleanup, WinError 32). It was reproduced and explained in `docs/research/07` section 7.2: a race with the IDE's workspace watcher when the temp folder is inside `D:\jarvis`, 6 failures in 25 isolated runs there and 0 in 37 runs elsewhere. It is not an application defect, and no test was changed for it.
- In this pass one console test (new this pass, a POST to a page path) failed once in the verification run with a connection abort. That exposed a real console defect (refusals answered without reading the request body, which resets the connection on Windows); it is fixed (section 7.3), reproduced deterministically first, and the console suite is 28 OK three times running.
- Live: the live approval part B was skipped by design (no PIN). Live reliability case F failed once while the private-lane providers were out of quota (the next command waited out the agent's back-off) and passed on the rerun. The voice-task spec passed earlier today (spoken text equal to the displayed text); three later reruns did not complete because the providers were resting and the console refused the command at once with the retry time, as designed. Those reruns are failures of the spec's environment, and I have not re-proven that spec since the quota ran out.

## 2. What is implemented

The kernel is the only authority. Everything the owner or an agent does passes its policy, audit chain and kill switch. The console (Windows, loopback only) holds the owner token and talks to the owner plane; the browser never sees it. Voice is local (Parakeet, IndicConformer, Kokoro, Piper; audio never leaves the laptop). Automations, agents, memory, the Windows adapter, the lab and the file-arrival triggers are as described in `docs/02_ARCHITECTURE.md` and D15/D16. Nothing is pushed: the repository is three commits ahead of `origin/main` (`f6fdb3f`, `73f8a32`, `d190c3e`) plus the commit that holds this handoff.

## 3. Provider identity and usable quota

### 3.1 "DHAI" and Dahl
- Your brief calls it "the existing authorized DHAI interface" with "an allowance of 100 million tokens". The string "DHAI" appears nowhere in the code, configuration or vault.
- The only integration in the system that is owner-allocated, token-pooled and advertises a 100M-token allowance is Dahl (`inference.dahl.global`). Its own docs say a new account receives 100 million tokens into a pool, to be allocated to API keys. In this session's earlier turns you also wrote "for now do all tests using dahl".
- Conclusion: **probable, not proven.** If "DHAI" is something else, change only the entry in `config/providers.json`. Please confirm in one word.

### 3.2 What Dahl's allowance actually is (sources fetched 2026-10-11)
| Question | Finding | Source |
|---|---|---|
| Does the 100M exist? | Yes, as a new-account gift into a shared pool; it does not go to a key | Dahl docs, Tokens & keys |
| Is it usable directly? | No: "Inference spends only what you allocate to a key"; a key with 0 tokens gets HTTP 402 | same |
| What does this key hold? | **6,720,752 tokens available** (one `GET /tokens/current`, no model call, 2026-10-11) | measured |
| Pool balance, ledger, history | Need an account sign-in; **unverified** | docs list `/v1/account/*` |
| Price after the gift | $0.03 per million tokens, prepaid, no expiry; the pricing page does not mention the gift | Dahl pricing page |
| Conditions of the gift | Homepage: "First 100 million tokens free", no conditions; Terms: promotional allocations may carry separate expiry and eligibility rules; free allocations are revocable for abuse or duplication | homepage, Terms s7-8 |
| Models and context | MiniMax-M2.7 180K, DeepSeek-V4-Flash 400K, GLM-5.3-Flash 400K (enough for the agent's 10k prompt) | docs, Models |
| Rate and per-request limits | Not published ("reasonable rate, concurrency, context and spending limits") | Terms s9 |
| Data handling | Prompts, tool definitions and outputs are recorded; independent GPU operators process them; no zero-retention, anonymity or end-to-end confidentiality guarantee; no right to train on content | Privacy policy |
| Verdict | **Public lane only.** The 100M cannot relieve the private lane without changing your privacy rule | |

### 3.3 The private lane today
The lane has two enabled providers. For the owner's agent (about 10,000 to 12,000 tokens before you type a word), only one can serve:
| Provider (lane) | Verified limits | Fits a 10-12k request? | Data policy | Status |
|---|---|---|---|---|
| Groq `gpt-oss-120b` (private, enabled) | Free plan 30 RPM, 1,000 RPD, **8,000 TPM**, 200,000 TPD per organization | **No** (8,000 tokens per minute caps one request) | Not retained by default; up to 30 days for abuse/reliability logs; Zero Data Retention switch in Data Controls; training not stated on the page | enabled, unusable for the agent |
| OpenRouter `apodex/apodex-1.1-mini:free` (private, enabled) | **50 requests/day** under $10 of purchased credits, 1,000/day at 10 or more; 20 RPM; day resets 00:00 UTC | Yes (context 262K per OpenRouter's listing) | OpenRouter stores no prompts unless you opt in; it avoids providers that log or train unless your account allows it (setting unverified) | enabled, exhausted by testing |
| Local Qwen3 on the VM (local) | 4,096 context | No | stays on your machines | enabled, too small |

### 3.4 Candidates, none enabled (all need your decision; none was called)
| Option | Limits found | Privacy found | Fit | Next step |
|---|---|---|---|---|
| OpenRouter, buy $10 of credits (credits stay usable) | 1,000 free-model requests/day | as above; check your privacy toggles | Best quick fix, but it is billing | Your decision; then verify https://openrouter.ai/settings/privacy keeps training/logging endpoints off |
| Cloudflare Workers AI (entry exists, disabled) | 10,000 neurons/day free, resets 00:00 UTC; error when exceeded | Does not use customer content to train (docs) | Large-context models such as gpt-oss-120b (128K per third-party listings) | Needs your Cloudflare API token in `D:\jarvis-secrets`; then enable the entry and measure neurons per request |
| Ollama Cloud (entry exists, disabled) | Free plan: 1 concurrent request; monthly usage allowance not published | Not used to train; retention not stated | Context per model unverified | Read the privacy policy first |
| Z.ai GLM-4.7-Flash (entry exists, disabled) | Free, 1 concurrency per secondary sources | API DPA not read; processing location uncertain | Context 200K per a directory | Lowest priority |
| Cerebras | Free trial reported to need a card; context 8K to 65K reported inconsistently | Retention stated; training unclear | Unclear | Not recommended now |
| Gemini free tier | n/a | Free tier prompts may be used to improve Google products and read by reviewers | Excluded by your privacy rule | Do not use for the private lane |
| Make the agent's prompt under about 6,000 tokens | Would make Groq's 1,000 requests/day usable | Groq: see above | Large change to the agent's tools | A design option, not done |

**Recommended next step:** decide between (a) buying OpenRouter credits (1,000 requests a day, simplest) and (b) adding Cloudflare Workers AI (free, no card, needs a token). Neither was done.

### 3.5 Router and lanes
`gateway.allowed()` permits a private task only on private or local providers and a public task only on public or local ones; the lane comes from the token, never from the request. Fallback never widens the lane (a private task with only public providers up fails closed). Tests: `test_fallback_stays_in_lane`, `test_private_task_never_planned_on_public`, `test_only_public_available_means_private_task_fails_closed`, `test_paid_or_bad_lane_provider_never_used`, `test_priority_orders_providers_but_never_widens_the_lane`, plus the eval `lane_enforcement`. A config test refuses a `dahl*`, `gonka*`, `kaggle*`, `colab*` or `omniroute*` provider outside the public lane.

## 4. Owner-only steps (do these locally; I never see the PIN)
1. **Confirm the provider name.** Reply "DHAI is Dahl", or name the provider you meant.
2. **Set the approval PIN** (typed on your terminal, not echoed):
   `ssh -t -i D:\jarvis-secrets\ssh\jarvis_vm_ed25519 ubuntu@100.125.91.51 "cd ~/jarvis/kernel && python3 -m jarvis_kernel set-approval-pin"` then type a 6 to 64 character PIN twice. Nothing needs restarting.
3. **Run the live approval once**, in PowerShell on the laptop, with the PIN only in this process (it is not stored, logged or in history):
   ```
   cd D:\jarvis\hq\ui
   $s = Read-Host "Approval PIN" -AsSecureString
   $env:JARVIS_APPROVAL_PIN = [System.Net.NetworkCredential]::new("", $s).Password
   npx playwright test -c playwright.live.config.ts approval.spec.ts
   Remove-Item Env:JARVIS_APPROVAL_PIN
   ```
   Expected: part A (denial) and part B (a harmless Notepad launch approved once, run once, replay refused, the node's result read back) both pass; screenshot `22-live-approval-used.png`.
4. **Voice check**: open http://127.0.0.1:8799/#/settings, find "Voice check", pick English, press Talk and read each listed phrase; repeat for Hindi and Telugu, then Save. Results: per phrase word and character error rate and latency, saved under `D:\jarvis\cache\voice-checks\`. Record separately per language: how many phrases were exact, the mean error rate, the slowest latency, and any phrase that came back empty. Mute the speakers or use a headset, so JARVIS's own voice is not picked up.
5. **Decide the private lane** (3.4).

## 5. Recovery and rollback
- **Code on the server:** every sync kept the replaced files in `~/downloads/sync<N>-<timestamp>/` (names have `_` for `/`); restore with `cp`, run `python3 -m unittest discover -s tests` in `~/jarvis/kernel`, then `sudo systemctl restart jarvis-llm-proxy jarvis-kernel` only when no job is queued or running. The full tree from the first deploy is `~/jarvis/kernel.old.<ts>`.
- **Agent MCP tool text:** `/opt/jarvis-mcp/jarvis_mcp.py.bak-<ts>`; takes effect at the next Hermes start.
- **Console:** stop the `pythonw ... -m jarvis_console` pair and run `powershell -ExecutionPolicy Bypass -File D:\jarvis\hq\scripts\start-console.ps1`. A second start fails to bind and exits.
- **Node:** `Stop-ScheduledTask JarvisNode; Start-ScheduledTask JarvisNode`.
- **Kill switch:** the console's Kill button; lifting it is only at the kernel console (`python3 -m jarvis_kernel resume`).
- **If the agent says providers are resting:** that is the free-tier limit, not a fault. It prints the time to retry. OpenRouter's daily count resets at 00:00 UTC.
- **Git:** nothing was pushed. To undo this pass locally: `git reset --hard 73f8a32` (this discards `d190c3e` and this handoff; the server would then need the earlier files restored from the sync backups).

## 6. Current health (2026-10-11, read from the system, not remembered)
Console and node running; kernel and proxy active; no active job, command, task or approval; PIN not set; kill switch off; Oracle cost this month 0.0 (checked); no Telegram process; `hermes-agent-vm-2` never contacted in this or the previous pass (the kernel's protected set refuses it by name; tests `test_hardening` and `test_telegram` cover the refusal).
