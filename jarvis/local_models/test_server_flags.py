"""Every llama-server JARVIS starts stays a plain local model server. Run: python -m pytest jarvis/local_models/test_server_flags.py

llama-server (b11669 and later) can also act as an agent: --tools and --agent give it file read/write, grep and a shell,
--mcp-servers-config starts MCP programs with its rights, and its web UI is on by default. Its CORS reflects any
Origin unless --tools/--agent is on, so any web page open in the owner's Chrome can call a localhost server. None of
that may be switched on here; the kernel is the only place tools run (with policy and approvals).
"""
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CONFIGS = [os.path.join(HERE, "llama-swap.yaml")] + sorted(
    os.path.join(HERE, "profiles", n) for n in os.listdir(os.path.join(HERE, "profiles")) if n.endswith(".yaml"))
# flags that turn the model server into something that acts, or open it up
FORBIDDEN = re.compile(r"(?<![\w-])(--tools|--agent|--mcp[\w-]*|--ui-mcp[\w-]*|--webui-mcp[\w-]*|--webui(?![\w-])|--ui(?![\w-])|"
                       r"--api-key(?![\w-])|--path|--slot-save-path|--props)(?![\w-])")


def lines(path):
    with open(path, encoding="utf-8") as fh:
        return [ln.split("#", 1)[0] if ln.lstrip().startswith("#") else ln for ln in fh.read().splitlines()]


def server_commands(path):
    """The server macro and every model's cmd (macros expanded the simple way llama-swap does)."""
    text = "\n".join(lines(path))
    macro = re.search(r"(?m)^\s+server:\s*(.+)$", text)
    assert macro, f"{os.path.basename(path)}: no 'server' macro"
    cmds = [c.replace("${server}", macro.group(1)) for c in re.findall(r"(?m)^\s+cmd:\s*(.+)$", text)]
    return macro.group(1), cmds


def test_every_config_is_checked():
    assert len(CONFIGS) >= 4


def test_server_macro_is_local_and_has_no_web_ui():
    for path in CONFIGS:
        macro, _ = server_commands(path)
        name = os.path.basename(path)
        assert "--host 127.0.0.1" in macro, f"{name}: llama-server must bind 127.0.0.1"
        assert "--no-webui" in macro, f"{name}: the web UI must be off"


def test_no_command_turns_the_model_server_into_an_agent_or_opens_it():
    for path in CONFIGS:
        _, cmds = server_commands(path)
        assert cmds, f"{os.path.basename(path)}: no model commands found"
        for cmd in cmds:
            assert "${server}" not in cmd and cmd.split()[0].lower().endswith("llama-server.exe"), cmd[:80]
            bad = FORBIDDEN.findall(cmd)
            assert not bad, f"{os.path.basename(path)}: {bad} in {cmd[:100]}"
            assert cmd.count("--host") == 1 and "--host 127.0.0.1" in cmd and "0.0.0.0" not in cmd, cmd[:100]
            # an API key on the command line would be visible to every process on the laptop: --api-key-file only
            assert not re.search(r"--api-key\s", cmd)


# --api-key-file is the only allowed way to give llama-server a key: a file in the secrets folder (D:\jarvis-secrets),
# never the key itself on a command line or in an environment variable a config sets (LLAMA_API_KEY).
SECRETS = re.compile(r"^[A-Za-z]:\\jarvis-secrets\\[\w.\\-]+\.key$")
INLINE_KEY = re.compile(r"LLAMA_API_KEY(?!_FILE)|LLAMA_ARG_API_KEY(?!_FILE)")


def api_key_file_problems(cmd):
    """Problems with how a command passes an API key ([] when it passes none, or a key file in the secrets folder)."""
    out = []
    if re.search(r"--api-key(?:\s|=)", cmd):
        out.append("--api-key with the key itself")
    for m in re.finditer(r"--api-key-file(?:\s+|=)(\S+)", cmd):
        path = m.group(1).strip("'\"")
        if not SECRETS.match(path) or ".." in path:
            out.append(f"--api-key-file outside the secrets folder: {path}")
    if re.search(r"--api-key-file\s*$", cmd):
        out.append("--api-key-file without a path")
    if INLINE_KEY.search(cmd):
        out.append("API key in an environment variable")
    return out


def test_api_keys_only_come_from_a_file_in_the_secrets_folder():
    for path in CONFIGS:
        text = "\n".join(lines(path))
        _, cmds = server_commands(path)
        for cmd in cmds:
            assert not api_key_file_problems(cmd), (os.path.basename(path), api_key_file_problems(cmd))
        assert not INLINE_KEY.search(text), f"{os.path.basename(path)}: API key set through an environment variable"
    with open(os.path.join(REPO, "jarvis", "scripts", "local-models.ps1"), encoding="utf-8-sig") as fh:
        assert not INLINE_KEY.search(fh.read())


def test_the_api_key_check_catches_what_it_should():
    ok = r"D:\jarvis\tools\llama.cpp\b11714\vulkan\llama-server.exe --host 127.0.0.1 --api-key-file D:\jarvis-secrets\local\llama-server.key"
    assert api_key_file_problems(ok) == [] and not FORBIDDEN.search(ok)
    for bad in ("x --api-key sk-123", "x --api-key=sk-123", r"x --api-key-file D:\jarvis\key.txt",
                r"x --api-key-file D:\jarvis-secrets\..\jarvis\k.key", "x --api-key-file", "x env LLAMA_API_KEY=abc",
                "x LLAMA_ARG_API_KEY=abc"):
        assert api_key_file_problems(bad), bad
    assert FORBIDDEN.search("x --api-key sk-123") and FORBIDDEN.search("x --api-key=sk-123")


def test_every_config_runs_a_locked_llama_cpp_build():
    """The llama-server path names a build (\\llama.cpp\\b11714\\vulkan\\) whose Windows release zip is in jarvis.lock.json."""
    import json
    with open(os.path.join(REPO, "jarvis", "tools", "jarvis.lock.json"), encoding="utf-8") as fh:
        lock = json.load(fh)
    locked = {(e["revision"].get("tag"), e["unpack_to"].replace("\\", "/").split("/")[-1]) for e in lock["entries"]
              if "laptop" in e["machine"] and e["registry_name"] == "llama.cpp (llama-server)"}
    builds = set()
    for path in CONFIGS:
        macro, _ = server_commands(path)
        m = re.search(r"\\llama\.cpp\\(b\d+)\\(vulkan|cpu)\\llama-server\.exe", macro)
        assert m, f"{os.path.basename(path)}: llama-server path does not name a build"
        assert (m.group(1), m.group(2)) in locked, f"{os.path.basename(path)}: {m.group(1)} {m.group(2)} is not locked"
        builds.add(m.group(1))
    assert len(builds) == 1, f"configs disagree on the llama.cpp build: {sorted(builds)}"


def test_the_proxy_listens_on_loopback_only():
    with open(os.path.join(REPO, "jarvis", "scripts", "local-models.ps1"), encoding="utf-8-sig") as fh:
        ps1 = fh.read()
    assert re.search(r'"--listen",\s*"127\.0\.0\.1:8080"', ps1)
    assert "0.0.0.0" not in ps1


def test_the_benchmark_server_is_started_the_same_way():
    with open(os.path.join(REPO, "jarvis", "evals", "neko95", "bench", "model_compare.py"), encoding="utf-8") as fh:
        src = fh.read()
    start = src[src.index("def start("):src.index("def make_post(")]
    assert '"--host", "127.0.0.1"' in start and '"--no-webui"' in start
    assert not FORBIDDEN.search(start)


def test_the_check_catches_what_it_should():
    for bad in ("x --tools all", "x --agent", "x --mcp-servers-config m.json", "x --ui-mcp-proxy", "x --webui", "x --api-key abc",
                "x --slot-save-path D:\\s", "x --props"):
        assert FORBIDDEN.search(bad), bad
    for ok in ("x --no-webui -fa off", "x --api-key-file D:\\jarvis-secrets\\k", "x --jinja -np 1 --n-cpu-moe 16"):
        assert not FORBIDDEN.search(ok), ok
