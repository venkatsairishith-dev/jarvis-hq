# JARVIS: rules for every session

Read these first. Details live in the memory map and the latest daily note.

- Workspace: D:\jarvis only. Everything large goes on D:, nothing big on C:. Check free space before and after big downloads.
- Secrets: never ask for, read, print, log or commit one. Windows: D:\jarvis-secrets. macOS: ~/.jarvis/secrets.env.
- Oracle: Always Free only, one named compartment. List before deleting, offer a backup, delete one named resource at a time. Check Cost Analysis after each phase (must be $0).
- Gates: stop after Step 11 and wait for the go. After that, one phase at a time: test, report what passed and what could not be tested, wait.
- Memory: vault is the memory. Agents write only inbox/, daily/, decisions/.
- Docs: at most 5 files in docs/. Plan changes go into the Project JARVIS doc too.
- Say which OS (Windows or macOS) each step runs on and when to reboot.

Memory map: vault/decisions/memory-map.md
Latest daily note: vault/daily/ (newest file)

- Settings control (user, 2026-10-28): "I can turn on the permission by itself I am the admin I am give it permissions" -- JARVIS (on either side, cloud or laptop) may turn the laptop's own app settings on or off itself -- including privacy-leaning toggles inside the app, like the face's microphone switch -- without asking first each time. The owner is the machine's admin and is granting this directly. This does NOT widen the short standing list of things that still need the owner personally (typing his own PIN, a destructive server/VM operation naming the exact resource, reading a site's terms before automating it, safe mode's "yes" typed at his own keyboard, entering API keys into their own dashboards) -- those stay as they were.

- Oracle protected: NEVER touch instance hermes-agent-vm-2 (or its volumes, network, IPs). hermes-agent-vm is the main server. (User, 2026-10-07)

- Oracle protection lifted for ONE purpose (user, 2026-10-24, answered in the cloud session): "jarvis vm2" = hermes-agent-vm-2 may be wiped and rebuilt as the computer for the CUA (computer-use) plugin, and nothing else. Still required, from the oracle-delete skill: list first, offer a backup and wait, one named resource at a time, a yes that names the exact resource, cost check afterwards. The code-level guard (D:\jarvis-secrets\protected.json, checked by the kernel policy) stays until the owner edits that file himself; Claude does not edit or read the secrets folder. hermes-agent-vm (the main server), the shared VCN/subnet/security lists and every other resource stay protected. Plan: docs/reports/CUA_VM2_PLAN_2026-10-24.md.

- Reuse first (user, 2026-10-08): use pre-existing maintained GitHub projects; do not remake anything or create new repos unless truly necessary. Justify any custom code against an existing project in docs/01_RESEARCH_AUDIT.md.

- Verify before you state (user, 2026-10-24, after a wrong "this fits the free limit"): a fact that can change (a provider's free limit, a price, a version, a model name, a policy, an API's terms) is read from the vendor's own page TODAY, quoted with its source and the date, before it is stated or acted on. Memory, a search snippet, a forum post and an AI summary are leads, never proof. When two sources disagree, say so, check the account's own data, and do not act until it is settled. Before any step that depends on such a fact (resize, delete, sign-up, spend), restate the fact with its source. "Cost shows $0" is not proof of free: billing lags a day, so compare measured usage with the allowance (skills/cost-check, scripts/oci_free_pool.py). When you were wrong, say what was wrong and what changed, in one plain line.
