# Document Reader

Reads the one scanned document, PDF or picture of a page the owner names, on this PC, with a local OCR model.
**English documents only** (Hindi was not tested and is not needed). Decision: `jarvis/docs/03_DECISIONS.md` D48.

| Entry | What it does |
|---|---|
| `read_document(path, max_pages?)` | Draws the pages, asks the local model for the text of each, cleans it, and returns `pages`, `pages_total`, `chars`, `text`, `took_s`, plus `removed` and `note` when something was taken out or cut. 10 pages by default, 30 at most. |

For a PDF or Word file that already has text, `doc_tools` `read_document` is the right tool (it has no OCR; this one does).

**How it works**
- The model is LightOnOCR-3-0.8B (Q4_K_M + mmproj-f16) in `llama-server`, behind llama-swap at `http://127.0.0.1:8080/v1`, model alias
  `docreader` (`jarvis/local_models/llama-swap.yaml`, entry `lightonocr-docreader`). One request per page: the image as a data URL on
  `/v1/chat/completions`, the call shape measured in `jarvis/evals/quality95/bench/lightonocr_eval.py`. The text may come back in
  `reasoning_content`; both are read, `<think>` blocks are removed. About 4 to 11 s a page on the owner's CPU laptop; the first call
  also loads the model.
- PDF pages are drawn with pypdfium2 (already in the agent venv; PyMuPDF is AGPL and is not used). The longer side is 1540 px (about
  140 dpi on a letter page) so a page and its answer fit the model's 4096-token window; a multi-page TIFF gives one page per frame.
- Files: `D:\jarvis\models\gguf\lightonocr-3-0.8b\LightOnOCR-3-0.8B.Q4_K_M.gguf` and `...mmproj-f16.gguf`, pinned with SHA-256 in
  `jarvis/tools/jarvis.lock.json` (fetch with `jarvis/tools/fetch.py`).

**Safety rules (core.py)**
- Only a file the owner names: an absolute path; no `..`, no network (UNC) path, no `:` stream, no Windows device names; no link,
  junction or reparse point in any part of the path (checked part by part, the file is opened without following a link and checked again).
- Refused: names that look like credentials (secret, passw, credential, token, private, api key, `.env`, `id_rsa`, `.pem`, `.key`, `.ppk`,
  wallet, seed phrase, the word "key"), and anything under `jarvis-secrets`, `.ssh`, `.gnupg`, `.aws`, `.azure`, `.kube`, AppData, Windows, Program Files.
- Types: pdf, png, jpg, jpeg, tif, tiff, webp, bmp, and the first bytes must match the name. At most 25 MB; a picture of more than 120
  million pixels is refused before it is decoded. A damaged or password-protected file gives a plain refusal, never the parser's text.
- Refusals never repeat the path.
- The only network call is to the llama-swap address, and `_shared/loopback_http` refuses anything but 127.0.0.1 / ::1 / localhost, ignores
  proxy settings and follows no redirect (`DOC_READER_URL` can move the port, nothing else). No telemetry.

**Privacy of the text**
1. `_shared/untrusted.clean_untrusted`: invisible and direction-override characters out, instruction-shaped text defused. The text is data, never instructions.
2. `_shared/screen_text.scrub_text` (the screen-text cleaner): API keys, tokens, passwords, PINs, card numbers and real IBANs are replaced;
   a page with a private key is withheld whole. The reply lists the kinds removed in `removed` and says them in words in `note`; never the values.
3. As for `desktop` `read_window_text`: the text reaches a model only when `JARVIS_SCREEN_TEXT_TO_MODEL=1` and the privacy gateway has
   hidden names and places (`screen_text.release_safely`, page by page, all or nothing). Otherwise the reply has `chars` and `pages` and
   `text_note` says the text stayed on this PC.
4. The face's tool server cuts every string it hands its chat model to 2,000 characters (`screen_text.STRING_LIMIT`), so a long document
   arrives in part until that limit is changed for document text.

**Settings:** `DOC_READER_URL` (default `http://127.0.0.1:8080/v1`, loopback only).

**Not tested here:** the real model (none on the build machine). Tests use a fake model call and a throw-away HTTP server on 127.0.0.1.

**Tests:** `pytest jarvis/plugins/doc_reader/tests` (temporary files only).
