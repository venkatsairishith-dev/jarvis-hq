# 09. Connectors: external services and MCP servers under the kernel's authority (2026-10-11)

Written for the owner and for whoever adds the next connector. It records what was researched (from repositories and the
specification, not marketing pages), what was built, where the trust boundaries are, how to set one up and how to recover.
Test evidence is in `07-integration-decisions.md` section 8.

## 1. What a connector is here

A connector is a named link to something outside JARVIS: a local program that speaks the Model Context Protocol (MCP) over
stdio, or a remote MCP endpoint over HTTPS. The kernel holds the **definition**, the **owner's decision for every tool** and
the **audit**; it is the only authority. An agent never talks to a connector server: it asks the kernel (`connector_call`), the
kernel decides, runs the call through the laptop node (local program) or itself (remote endpoint), and returns scrubbed,
size-capped, clearly untrusted data.

```
agent (Hermes / lab) --connector_call--> kernel policy --job--> laptop node --stdio--> local MCP server
                                              |--https (SSRF-checked, address-pinned)--> remote MCP endpoint
owner (browser) --> console --owner token--> kernel owner plane (catalogue, tool decisions, test, connect, try)
                \--> console's local allowlist file (the only place a command line is accepted)
```

## 2. Research (verified 2026-10-11)

| Project | What it is | Licence, activity | Fit |
|---|---|---|---|
| MCP specification 2026-07-28 | Stateless: every request carries `_meta` (version, capabilities); `server/discover`; legacy `initialize` handshake still common. stdio: newline-delimited JSON-RPC, close stdin then terminate then kill | n/a | Implemented dual-era (probe `server/discover`, fall back to `initialize`) |
| `modelcontextprotocol/python-sdk` (`mcp` 2.3.0) | Official client and server SDK | MIT, pushed 2026-10-05 | **Not imported.** 13 or more runtime dependencies (pydantic, httpx2, starlette, uvicorn, pyjwt, pywin32 ...) into a kernel that is standard-library only by design; the protocol client needed here is under 400 lines |
| `hoophq/mcpproxy` | Go security gateway: allow/deny tools by glob, approvals over REST, rug-pull fingerprints, sampling and elicitation denied by default, JSONL audit | MIT, 6 stars, pushed 2026-08-06 | **Not adopted** as a component: it is a second policy and approval engine outside the kernel (authority split), keeps state in memory (single replica), and refuses the 2026-07-28 revision. **Ideas adopted:** tool-declaration fingerprints (a changed tool switches itself off), server requests refused, per-connector call budgets, result truncation, structured audit |
| `modelcontextprotocol/servers` (`mcp-server-time`) | Official reference servers; time and time-zone tools, no account | repo licence NOASSERTION (per-package); `mcp-server-time` MIT, release 2026.10.10 | **Used and tested** as the first connector |
| `github/github-mcp-server` | Official GitHub server, Go binary | MIT, v2.0.2 (2026-10-08), 33k stars; Windows and Linux, x86-64 and arm64 builds | Template (needs your token). `--read-only` keeps write tools out entirely |
| `taylorwilsdon/google_workspace_mcp` | Gmail, Calendar, Drive and more | MIT, pushed 2026-10-09 | Template (needs your Google OAuth client and consent) |
| `makenotion/notion-mcp-server` | Notion pages and databases | MIT, pushed 2026-09-20; README says the self-hosted server is no longer actively maintained | Template with that caveat |
| DeepWiki MCP (`https://mcp.deepwiki.com/mcp`) | Hosted, no account, documentation about public repositories | hosted service | **Used and tested** as the remote example (discovery on this system; one real tool call, read only) |
| Nango, Composio, Pipedream | Integration platforms with their own credential stores | Nango: Elastic License 2.0 and its free self-host lacks MCP/functions; Composio: hosted, backend closed; Pipedream: not verified | **Not adopted**: they would hold the owner's credentials and make their own authorization decisions outside the kernel |

What real servers did when probed here: `mcp-server-time` answered `server/discover` with a JSON-RPC error and `initialize`
normally (legacy era, protocol 2025-06-18) and wrote 94 warning lines to stderr during the probe (so stderr is drained and never
trusted); DeepWiki is also legacy era. No server asked the client for sampling or elicitation; the client would have refused.

## 3. Trust boundaries

| Boundary | What enforces it |
|---|---|
| Who may add a program | Only the console's local endpoint, from the owner's browser (same-origin, loopback, header check). No agent, model, kernel route or MCP server can reach it. The kernel never holds or sends a command line |
| What may run | One absolute path to a real program in `config/connectors.local.json`; refused: shells and system tools, scripts, interpreters given code (`-c`, `-e`, `--eval` ...), anything in the Windows or secrets folder, arguments pointing into the secrets folder |
| Environment of a server | A handful of system variables, its own folder on PATH, a private working folder under `cache\connectors\`, plus only the variables the entry declares. Credentials only as references to files in the secrets folder; values are read at start, never returned or stored; a server echoing its own credential gets `[redacted]` |
| What a server says about itself | Stored as untrusted metadata, scrubbed, size-capped. Names must match `[A-Za-z0-9_.-]{1,128}`. Annotations (`readOnlyHint` ...) are shown as "claims" and never lower a class. The suggested class comes from the tool's NAME only |
| Which tools run | Only tools the OWNER classified read, write or send and switched on, whose declaration still matches the one reviewed (SHA-256 of name, description, schema). Unknown tool, unclassified tool, changed tool, disabled connector: refused (fail closed). destructive and credential can be recorded but never enabled |
| What a call needs | read: runs for a lane allowed to use the connector. write and send: an approval bound to the exact tool and arguments (single use, 10 minutes, replay and changed arguments refused), PIN and typed id over HTTP as for every other YELLOW action |
| Which lane | A connector lists its lanes (private by default). A public-lane agent can use it only if the owner added the public lane, and write and send still need approval. Private connector data cannot reach a public-lane model through a connector |
| Arguments | At most 16 KB; checked against the tool's declared schema (required, primitive types, enums); refused if they contain what looks like a credential or private key; the audit chain records their keys and size, the approval prompt shows them in full |
| Results | Text only (images and binary replaced by a note), scrubbed twice, capped (default 16,000 characters), a private key withholds the whole reply, labelled `untrusted` with a note never to obey it |
| Remote endpoints | `https` only; host must not be or resolve to a loopback, private, link-local, CGNAT (Tailscale), reserved or multicast address; the connection goes to the address that was checked (no second lookup), certificate verified against the real host name; redirects never followed; 1 MiB response cap; bearer token read from a file by reference and sent only to that endpoint |
| Server-initiated requests | Sampling, elicitation and roots requests are answered "not supported", never granted. A tool result asking for more input (`input_required`) is refused |
| Limits | Calls per hour (default 60), per-call timeout (cancel notification sent, then the server is stopped), output cap, one call in flight per connector, kill switch stops everything, an idle local server is stopped after 10 minutes |
| Audit | `connector_created/updated/deleted/enabled/disabled`, `connector_tool_set`, `connector_test/connect/disconnect`, `connector_call_done` (connector, tool, outcome, class, ms, bytes), `connector_call_refused`. No arguments, no result text, no credential, no endpoint secret |

## 4. Configuration format

**Kernel record** (owner plane, created from the Add form; no command, no secret value):

```json
{ "name": "github", "label": "GitHub", "purpose": "Repositories, issues, pull requests (read only)",
  "transport": "stdio", "node": "laptop",
  "env_refs": { "GITHUB_PERSONAL_ACCESS_TOKEN": "connectors/github.token" },
  "lanes": ["private"], "max_calls_per_hour": 60, "timeout_s": 30, "max_output": 12000 }
```

or `"transport": "https", "endpoint": "https://example.com/mcp", "secret_ref": "connectors/service.token"` (reference to a file in the
kernel server's secrets folder; omit for none).

**Local allowlist** (`config/connectors.local.json`, this computer only, git-ignored; written by the console):

```json
{ "connectors": { "github": {
    "command": ["D:\\jarvis\\tools\\github-mcp-server\\github-mcp-server.exe", "stdio", "--read-only", "--toolsets", "repos,issues,pull_requests,users"],
    "env_refs": { "GITHUB_PERSONAL_ACCESS_TOKEN": "connectors/github.token" },
    "env_static": {}, "timeout_s": 30 } } }
```

Secret files live under `D:\jarvis-secrets\connectors\` (laptop programs) or the kernel's secrets folder (remote endpoints): one value
per file, created by you, never typed into the app, never shown by it (the app shows only whether the file exists).

## 5. Add a connector

1. Open Connectors (Alt+0), press Add connector, pick a template or fill the form.
2. Create the secret file(s) it names, if any.
3. Add. Press Test connection: the server's handshake and tool list are fetched, nothing else is sent. Tools appear **off and unclassified**.
4. For each tool you want: choose its class (read, write, send) and switch it on. Use Try on a read tool to see a real result.
5. Agents now see it in `connector_list`. Anything that changes or sends shows up in the Command Center for your approval, with the arguments.

States: **configured** (defined, never tested), **installed** (reached, tools found, nothing running), **connected** (answered recently
and presumed running), **degraded** (worked before, latest test or call failed), **disabled** (nothing can use it). "Needs setup" means a
secret file or program is missing; a failure is never shown as connected.

## 6. Tested and template

| Connector | Status | Evidence |
|---|---|---|
| Time and time zones (`mcp-server-time`) | **Tested live**: real server, real node, real kernel | `07` section 8 |
| DeepWiki (remote, https, no credential) | **Tested live**: discovery and one read-only call | `07` section 8 |
| Fake MCP server (fixtures) | Tested in automated tests: both eras, crash, timeout, oversize, hostile output, changed tools, sampling | `kernel/tests/test_connectors.py`, `ui/e2e/connectors.spec.ts` |
| GitHub, Google Workspace, Notion | **Templates, not run here**: each needs your account or credential. Commands and flags come from each project's README as read on 2026-10-11 | `console/jarvis_console/connector_templates.json` |

## 7. Recovery

- **A connector misbehaves:** Disable it (nothing can call it), or Remove. Disabling is read back from the kernel.
- **A program is stuck:** Disconnect; the node stops it (close stdin, terminate, kill). The node also stops idle servers after 10 minutes and on restart.
- **Allowlist file damaged:** delete `config/connectors.local.json`; connectors then report "not configured" and nothing starts. Re-add from the UI.
- **Everything:** the kill switch refuses all connector calls, as it does every other action.
- **Roll back the feature on the server:** restore the previous files from `~/downloads/sync<N>-<ts>/` (the new tables are additive; an older kernel ignores them).
