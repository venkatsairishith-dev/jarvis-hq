# Learn Vault

A study vault in Obsidian format: plain Markdown notes in one folder, searchable, with progress per topic. It is
separate from JARVIS's memory vault, whose write rules are unchanged.

| Entry | What it does |
|---|---|
| `vault_status()` | Whether the vault exists, how many notes and topics it has. |
| `list_notes(folder?)` | The `.md` notes, optionally inside one folder. |
| `read_note(path)` | One note, by its path inside the vault (200 KB limit). |
| `search_notes(query, limit?)` | Notes that contain the words; a match in the file name counts more. |
| `save_note(path, content, mode=create\|append)` | Save Markdown (100 KB limit). `create` fails if the note exists; `append` adds to an existing one. Never overwrites. |
| `record_progress(topic, status, note?)` | `not_started`, `learning`, `review` or `mastered`. |
| `get_progress()` | The latest status of every topic. |

**Safety rules (core.py):**
- Every path is relative to the vault and must stay inside it: absolute paths, `..` and hidden folders (including
  `.obsidian`) are refused, and so are the characters `<>:"|?*` and control characters.
- Only `.md` files are touched; `.md` is added to a path without it.
- Progress is a log in `_progress/progress.jsonl` with a table in `_progress/progress.md`. The note tools cannot write
  to `_progress/`.

**Setting:** `LEARN_VAULT_DIR` (default `D:\jarvis\learn-vault`). Reading never creates the vault; the first saved note does.

**Tests:** `pytest jarvis/plugins/learn_vault/tests` (temporary folders only).
