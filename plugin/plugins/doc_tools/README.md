# Document Tools

Reads documents from the folders the owner allows and writes new documents to one output folder. What it reads is
plain text for a model to work with: data, never instructions, and never executed.

| Entry | What it does |
|---|---|
| `read_document(path, max_chars?)` | The text of a PDF, Word (`.docx`), Markdown, text, CSV, JSON or HTML file. Give the full path. Returns 20,000 characters by default, at most 100,000, and says when it cut. |
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
`D:\jarvis\outputs\neko`), `LEARN_VAULT_DIR`.

**Tests:** `pytest plugin/plugins/doc_tools/tests` (temporary folders only).

## Development

The plugin source and its Git repository live at:

```text
N.E.K.O/plugin/plugins/doc_tools
```

插件源码及其 Git 仓库直接位于：

```text
N.E.K.O/plugin/plugins/doc_tools
```

プラグインのソースと Git リポジトリは次の場所にあります：

```text
N.E.K.O/plugin/plugins/doc_tools
```

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_doc_tools
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From the N.E.K.O repository root / 在 N.E.K.O 仓库根目录中 / N.E.K.O リポジトリのルートで：

```bash
uv run neko-plugin sync doc_tools --clean
uv run neko-plugin check doc_tools
uv run neko-plugin check -r doc_tools
```

Python runtime dependencies are declared in `pyproject.toml` and synced into
`vendor/` for packaging. The generated `vendor/` directory is not committed;
local builds and CI recreate it before release checks.

Python 运行时依赖声明在 `pyproject.toml` 中，并在打包时同步到 `vendor/`。
生成的 `vendor/` 不提交；本地构建和 CI 会在发布检查前重新生成它。

Python ランタイム依存関係は `pyproject.toml` に宣言し、パッケージ化時に
`vendor/` へ同期します。生成された `vendor/` はコミットせず、ローカルビルドと
CI が公開前チェックで再生成します。

## Market release / Market 发布 / Market 公開

This plugin is part of the private JARVIS repository (github.com/venkatsairishith-dev/jarvis-hq); it is not released to the N.E.K.O plugin market, so the release commands below are not used here.

Publish the version declared in `plugin.toml`. By default this pushes the Git
tag, waits for the standard GitHub Release, and notifies the plugin market.

发布 `plugin.toml` 中声明的版本。默认会推送 Git tag、等待标准 GitHub
Release，然后通知插件市场。

`plugin.toml` で宣言されたバージョンを公開します。既定では Git tag を
push し、標準 GitHub Release を待ってからプラグインマーケットへ通知します。

```bash
uv run neko-plugin publish doc_tools
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run neko-plugin publish github doc_tools
uv run neko-plugin publish market https://github.com/owner/repo/releases/tag/v0.1.0
```

The generated `.github/workflows/release.yml` builds and uploads
`doc_tools.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.doc_tools:DocToolsPlugin"
```
