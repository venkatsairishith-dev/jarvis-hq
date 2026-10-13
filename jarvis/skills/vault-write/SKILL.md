---
name: vault-write
description: Write to the JARVIS vault. Agents may write only inbox/, daily/, decisions/.
---

1. Allowed folders: inbox/, daily/, decisions/. Refuse any other path, including .. and absolute paths outside the vault.
2. Never modify the user own notes. Never put secrets in the vault.
3. Plain Markdown, one idea per file, dated names in daily/.
4. Commit with a clear message and push; keep memory-map.md current.

