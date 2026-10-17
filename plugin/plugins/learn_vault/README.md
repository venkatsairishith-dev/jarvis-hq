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

**Tests:** `pytest plugin/plugins/learn_vault/tests` (temporary folders only).

## Development

The plugin source and its Git repository live at:

```text
N.E.K.O/plugin/plugins/learn_vault
```

插件源码及其 Git 仓库直接位于：

```text
N.E.K.O/plugin/plugins/learn_vault
```

プラグインのソースと Git リポジトリは次の場所にあります：

```text
N.E.K.O/plugin/plugins/learn_vault
```

When publishing to the plugin market, use this GitHub repository name:

发布到插件市场时，请使用以下 GitHub 仓库名：

プラグインマーケットへ公開する際は、次の GitHub リポジトリ名を使用してください：

```text
n.e.k.o_plugin_learn_vault
```

From this plugin repository root:

```bash
uvx ruff==0.12.4 check --ignore-noqa --config ruff.toml .
```

From the N.E.K.O repository root / 在 N.E.K.O 仓库根目录中 / N.E.K.O リポジトリのルートで：

```bash
uv run neko-plugin sync learn_vault --clean
uv run neko-plugin check learn_vault
uv run neko-plugin check -r learn_vault
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
uv run neko-plugin publish learn_vault
```

To run only one half explicitly / 如需仅执行一部分 / 一方のみを実行する場合:

```bash
uv run neko-plugin publish github learn_vault
uv run neko-plugin publish market https://github.com/owner/repo/releases/tag/v0.1.0
```

The generated `.github/workflows/release.yml` builds and uploads
`learn_vault.neko-plugin`. The market independently verifies that Release
before publishing it.

生成的 `.github/workflows/release.yml` 会构建并上传插件包；Market 会独立验证
该 Release 后再发布。

生成された `.github/workflows/release.yml` がプラグインパッケージをビルドして
アップロードし、Market はその Release を独立検証してから公開します。

## Entry

```toml
entry = "plugin.plugins.learn_vault:LearnVaultPlugin"
```
