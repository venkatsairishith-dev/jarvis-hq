"""The laptop's own allowlist of local MCP servers (config/connectors.local.json), and the only place a command line is accepted.

The kernel never sees a command: it holds a connector's name, policy and the owner's per-tool decisions. This file says which
program starts when the kernel asks the node for a connector of that name. It is written only here, from a request the owner
made in the console's own UI (the console refuses cross-site requests); no agent, model or MCP server can reach this module.

Validation is deliberately narrow: one absolute path to a real program (not a shell, an interpreter in eval mode, a script, or
something inside the secrets folder), a plain list of arguments (never a string a shell would parse), secrets only as references
to files, and a few plain environment variables.
"""
import json
import os
import re

from . import config as cfgmod

NAME = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,120}$")
SHELLS = {"cmd.exe", "powershell.exe", "pwsh.exe", "bash.exe", "sh.exe", "zsh.exe", "wsl.exe", "wscript.exe", "cscript.exe", "mshta.exe",
          "rundll32.exe", "regsvr32.exe", "msiexec.exe", "schtasks.exe", "reg.exe", "certutil.exe", "bitsadmin.exe", "curl.exe", "wget.exe",
          "ssh.exe", "scp.exe", "net.exe", "net1.exe", "sc.exe", "taskkill.exe", "runas.exe", "at.exe", "wmic.exe", "explorer.exe"}
INTERPRETERS = {"python.exe", "pythonw.exe", "python3.exe", "node.exe", "deno.exe", "bun.exe", "java.exe", "javaw.exe", "ruby.exe", "perl.exe",
                "php.exe", "uv.exe", "uvx.exe"}
EVAL_FLAGS = {"-c", "-e", "--eval", "-p", "--print", "-r", "--require", "-i", "--interactive", "--command", "-command", "/c", "-x", "--exec"}
BLOCKED_ENV = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
               "NODE_OPTIONS", "NODE_PATH", "LD_PRELOAD", "LD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA"}
SECRETISH = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH", re.I)
MAX_ARGS = 16


class ConfigError(ValueError):
    pass


def default_path() -> str:
    return os.path.join(cfgmod.HQ, "config", "connectors.local.json")


def secrets_dir() -> str:
    return os.environ.get("JARVIS_SECRETS_DIR") or r"D:\jarvis-secrets"


def load(path: str = None) -> dict:
    try:
        with open(path or default_path(), encoding="utf-8-sig") as fh:
            d = json.load(fh).get("connectors", {})
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}


def validate(name: str, spec, secrets: str = None) -> dict:
    """The cleaned local spec for a connector, or ConfigError with a message for the owner."""
    secrets = secrets or secrets_dir()
    if not NAME.match(str(name)):
        raise ConfigError("name: 2 to 32 characters, lowercase letters, digits, - and _, starting with a letter")
    if not isinstance(spec, dict):
        raise ConfigError("the local setup must be an object")
    cmd = spec.get("command")
    if not isinstance(cmd, list) or not 1 <= len(cmd) <= MAX_ARGS or not all(isinstance(a, str) and 0 < len(a) <= 300 for a in cmd):
        raise ConfigError(f"command: a list of 1 to {MAX_ARGS} text items, the program first and then its arguments (not one string)")
    if any(any(c in a for c in "\0\r\n") for a in cmd):
        raise ConfigError("command: no line breaks or NUL characters")
    exe = cmd[0]
    if not os.path.isabs(exe):
        raise ConfigError("command: the program must be given as a full path")
    base = os.path.basename(exe).lower()
    if os.name == "nt" and not base.endswith(".exe"):
        raise ConfigError("command: the program must be an .exe (scripts and shell wrappers are refused; point to the program itself)")
    if base in SHELLS:
        raise ConfigError(f"command: {base} is a shell or system tool and cannot be a connector")
    real = os.path.realpath(exe)
    low = real.lower().replace("/", "\\")
    if "jarvis-secrets" in low or os.path.commonpath([os.path.realpath(secrets).lower(), low]) == os.path.realpath(secrets).lower():
        raise ConfigError("command: a program inside the secrets folder is refused")
    if re.search(r"\\windows\\", low):
        raise ConfigError("command: programs inside the Windows folder are refused")
    if not os.path.isfile(real):
        raise ConfigError("command: that program does not exist at this path (install it first)")
    if base in INTERPRETERS:
        if any(a.lower() in EVAL_FLAGS for a in cmd[1:]):
            raise ConfigError(f"command: {base} may not be given code to run (-c, -e, --eval and similar); give it the server's script or module")
    if any("jarvis-secrets" in a.lower() for a in cmd[1:]):
        raise ConfigError("command: arguments must not point into the secrets folder (use env_refs)")
    refs = spec.get("env_refs") or {}
    if not isinstance(refs, dict) or len(refs) > 12:
        raise ConfigError("env_refs: at most 12 entries of VARIABLE: secret-file reference")
    clean_refs = {}
    for k, v in refs.items():
        if not ENV_NAME.match(str(k)) or str(k) in BLOCKED_ENV:
            raise ConfigError(f"env_refs: '{str(k)[:30]}' is not an allowed variable name")
        if not isinstance(v, str) or not REF.match(v) or ".." in v:
            raise ConfigError(f"env_refs.{k}: a relative path inside the secrets folder (letters, digits, / . _ -)")
        clean_refs[str(k)] = v
    statics = spec.get("env_static") or {}
    if not isinstance(statics, dict) or len(statics) > 12:
        raise ConfigError("env_static: at most 12 plain settings")
    clean_static = {}
    for k, v in statics.items():
        if not ENV_NAME.match(str(k)) or str(k) in BLOCKED_ENV:
            raise ConfigError(f"env_static: '{str(k)[:30]}' is not an allowed variable name")
        if SECRETISH.search(str(k)):
            raise ConfigError(f"env_static.{k}: credentials go in env_refs (a secret file), never in this setting")
        if not isinstance(v, str) or len(v) > 200 or any(c in v for c in "\0\r\n"):
            raise ConfigError(f"env_static.{k}: a short text value")
        clean_static[str(k)] = v
    t = spec.get("timeout_s", 20)
    if isinstance(t, bool) or not isinstance(t, int) or not 3 <= t <= 120:
        raise ConfigError("timeout_s: a whole number from 3 to 120")
    return {"command": cmd, "env_refs": clean_refs, "env_static": clean_static, "timeout_s": t}


def _write(path: str, connectors: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"connectors": connectors}, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def save(name: str, spec: dict, path: str = None, secrets: str = None) -> dict:
    path = path or default_path()
    clean = validate(name, spec, secrets)
    cur = load(path)
    cur[name] = clean
    _write(path, cur)
    return clean


def remove(name: str, path: str = None) -> bool:
    path = path or default_path()
    cur = load(path)
    if name not in cur:
        return False
    del cur[name]
    _write(path, cur)
    return True


def describe(path: str = None, secrets: str = None) -> dict:
    """For the UI: each local spec with whether the program exists and which secret files exist (booleans, never contents)."""
    secrets = secrets or secrets_dir()
    out = {}
    for name, spec in load(path).items():
        if not isinstance(spec, dict) or not isinstance(spec.get("command"), list) or not spec["command"]:
            continue
        refs = spec.get("env_refs") or {}
        present = {}
        for var, ref in refs.items():
            ok = False
            if isinstance(ref, str) and REF.match(ref) and ".." not in ref:
                full = os.path.realpath(os.path.join(secrets, ref.replace("/", os.sep)))
                ok = os.path.isfile(full) and full.lower().startswith(os.path.realpath(secrets).lower())
            present[var] = ok
        out[name] = {"command": spec["command"], "installed": os.path.isfile(spec["command"][0]), "env_refs": refs, "secrets_present": present,
                     "env_static": sorted((spec.get("env_static") or {})), "timeout_s": spec.get("timeout_s", 20)}
    return out
