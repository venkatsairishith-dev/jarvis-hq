"""systemd rendering for the VM (plan P1 step 4): one hardened unit per manifest component. Renders text only; nothing is installed."""
from __future__ import annotations

import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import manifest  # noqa: E402


def render_unit(c: manifest.Component, user: str = "jarvis", token_file: str = "/run/jarvis/boot.token") -> str:
    lines = ["[Unit]", f"Description=JARVIS {c.name}"]
    if c.depends_on:
        deps = " ".join(f"jarvis-{d}.service" for d in c.depends_on)
        lines += [f"After=network-online.target {deps}", f"Requires={deps}"]
    else:
        lines += ["After=network-online.target"]
    lines += ["", "[Service]", f"User={user}", f"ExecStart={' '.join(shlex.quote(a) for a in c.argv)}"]
    if c.cwd:
        lines.append(f"WorkingDirectory={c.cwd}")
    for k, v in sorted(c.env.items()):
        lines.append(f"Environment={shlex.quote(f'{k}={v}')}")
    lines.append(f"Environment=JARVIS_BOOT_TOKEN_FILE={token_file}")
    lines += ["Restart=on-failure" if c.restart == "on-failure" else "Restart=no",
              "NoNewPrivileges=yes", "ProtectSystem=strict", "ProtectHome=read-only", "PrivateTmp=yes",
              "RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX", "", "[Install]", "WantedBy=multi-user.target", ""]
    return "\n".join(lines)


def render_all(comps: list[manifest.Component], **kw) -> dict[str, str]:
    return {f"jarvis-{c.name}.service": render_unit(c, **kw) for c in comps}
