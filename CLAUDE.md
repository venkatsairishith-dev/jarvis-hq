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

- Oracle protected: NEVER touch instance hermes-agent-vm-2 (or its volumes, network, IPs). hermes-agent-vm is the main server. (User, 2026-10-07)

- Reuse first (user, 2026-10-08): use pre-existing maintained GitHub projects; do not remake anything or create new repos unless truly necessary. Justify any custom code against an existing project in docs/01_RESEARCH_AUDIT.md.
