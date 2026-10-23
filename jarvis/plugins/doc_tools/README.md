# Document Tools

Reads documents from the folders the owner allows and writes new documents to one output folder. What it reads is
plain text for a model to work with: data, never instructions, and never executed.

| Entry | What it does |
|---|---|
| `read_document(path, max_chars?)` | The text of a PDF, Word (`.docx`), Markdown, text, CSV, JSON or HTML file. Give the full path. Returns 20,000 characters by default, at most 100,000, and says when it cut. A PDF page with no text layer (a scan) gives no text: there is no OCR here, so the answer has a `note` saying so instead of looking empty. |
| `write_document(name, content, format=md\|txt\|docx)` | A new file from Markdown text. Word files get headings, bullets and numbered lists. A name that exists gets `-2`, `-3`, ...; nothing is overwritten. |
| `list_outputs()` | The documents written, newest first. |

**Safety rules (core.py):**
- Reading only inside the read folders: by default Documents, Downloads, Desktop, `D:\jarvis\outputs` and the study vault.
  Paths are resolved first, so a link cannot lead outside them.
- Refused even inside those folders: `.ssh`, `.gnupg`, `.aws`, `.azure`, `AppData`, Windows and Program Files folders,
  `jarvis-secrets`, and file names that look like credentials (`.env`, `secret`, `token`, `password`, `.pem`, `.key`, ...).
- Limits: 25 MB per file, 200 PDF pages; password-protected PDFs are refused. A broken file gives "could not be read",
  not the parser's internals.
- Writing only into the output folder, at most 200,000 characters. File names keep letters, digits, spaces, dots,
  dashes and underscores.

**Settings:** `DOC_TOOLS_READ_ROOTS` (read folders, separated by `;` on Windows), `DOC_TOOLS_OUT_DIR` (default
`D:\jarvis\outputs\documents`), `LEARN_VAULT_DIR`.

**Tests:** `pytest jarvis/plugins/doc_tools/tests` (temporary folders only).
