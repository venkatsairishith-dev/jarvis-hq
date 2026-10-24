"""Provider signup helper (D40): JARVIS gets its own free model-API accounts with its own Gmail and stops where the owner is needed.

Three entries: `status` (which providers have a key file, by existence only), `plan` (the steps, and which are the owner's) and
`verification` (the code or same-site link from the newest verification mail). The browser work itself is done by the desktop
agent on the Agent PC following `plan`; this plugin never opens a page, never reads a key and never returns mail text.
The mailbox is read over IMAP with an app password kept in a file under the secrets folder (named in mail/gmail.json).
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from jarvis.pluginapi import Err, Ok, PluginBase, SdkError, jarvis_plugin, lifecycle, plugin_entry

from . import _mail
from ._registry import PROVIDERS, steps

SECRETS = Path(os.environ.get("JARVIS_SECRET_REF_ROOTS", r"D:\jarvis-secrets").split(os.pathsep)[0])
PROVIDERS_JSON = Path(__file__).resolve().parents[2] / "privacy" / "providers.json"
IMAP_HOST = "imap.gmail.com"


def _enabled() -> dict[str, bool]:
    try:
        return {p["name"]: bool(p.get("enabled")) for p in json.loads(PROVIDERS_JSON.read_text(encoding="utf-8"))["providers"]}
    except (OSError, ValueError, KeyError):
        return {}


def _has_key(rel: str) -> bool:
    try:
        return (SECRETS / rel).stat().st_size > 0            # existence and size only; the content is never read
    except OSError:
        return False


def _mail_config() -> tuple[str, Path] | None:
    try:
        cfg = json.loads((SECRETS / "mail" / "gmail.json").read_text(encoding="utf-8"))
        user = str(cfg["user"])
        pw = (SECRETS / str(cfg.get("app_password_file", "mail/gmail.app-password")))
        return (user, pw) if pw.is_file() else None
    except (OSError, ValueError, KeyError):
        return None


@jarvis_plugin
class ProviderSignupPlugin(PluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @plugin_entry(id="status", name="Provider accounts", description="Which free model-API providers have a key file yet and which are switched on. Key files are checked by existence only.",
                  llm_result_fields=["summary"], timeout=10.0, input_schema={"type": "object", "properties": {}})
    async def status(self, **_: Any):
        on, rows = _enabled(), []
        for pid, p in PROVIDERS.items():
            key = _has_key(p["key_file"])
            rows.append({"id": pid, "name": p["name"], "key_file_present": key, "enabled": on.get(pid, False), "limits": p["limits"],
                         "next": "switch on after the comparison run" if key and not on.get(pid) else ("done" if key else "needs an account and a key"),
                         "note": p.get("note", "")})
        mail = _mail_config() is not None
        summary = "\n".join(f"- {r['name']}: key {'present' if r['key_file_present'] else 'missing'}, {'on' if r['enabled'] else 'off'} ({r['next']})" for r in rows)
        summary += "\nMailbox: " + ("configured" if mail else "not configured (the owner adds mail/gmail.json and an app-password file)")
        return Ok({"providers": rows, "mailbox_configured": mail, "summary": summary})

    @plugin_entry(id="plan", name="Signup steps", description="The signup steps for one provider, marking which ones only the owner may do (CAPTCHA, phone, card, identity, terms).",
                  llm_result_fields=["summary"], timeout=10.0,
                  input_schema={"type": "object", "properties": {"provider": {"type": "string", "description": "groq, cloudflare, zai or nvidia"}}, "required": ["provider"]})
    async def plan(self, provider: str = "", **_: Any):
        pid = str(provider or "").strip().lower()
        if pid not in PROVIDERS:
            return Err(SdkError(f"{pid or 'That'} is not on the owner's provider list (groq, cloudflare, zai, nvidia). Ask the owner before signing up anywhere else."))
        st = steps(pid)
        return Ok({"provider": pid, "steps": st, "summary": "\n".join(f"{i}. [{s['kind'].upper()}] {s['do']}" for i, s in enumerate(st, 1))})

    @plugin_entry(id="verification", name="Verification mail", description="The code or the same-site link from the newest verification e-mail of one provider (last 15 minutes). Returns nothing else from the mail.",
                  llm_result_fields=["summary"], timeout=60.0,
                  input_schema={"type": "object", "properties": {"provider": {"type": "string"}}, "required": ["provider"]})
    async def verification(self, provider: str = "", **_: Any):
        pid = str(provider or "").strip().lower()
        if pid not in PROVIDERS:
            return Err(SdkError("That provider is not on the owner's list."))
        cfg = _mail_config()
        if cfg is None:
            return Err(SdkError("The JARVIS mailbox is not set up yet: ask the owner to add mail/gmail.json and the app-password file."))
        user, pw_file = cfg
        try:
            password = pw_file.read_text(encoding="utf-8").strip()
            got = await asyncio.to_thread(_mail.fetch_latest, IMAP_HOST, user, password, PROVIDERS[pid]["mail_domains"])
        except Exception as exc:                                                  # the kind only: the message could carry the login
            return Err(SdkError(f"The mailbox could not be read ({type(exc).__name__})."))
        if not got:
            return Ok({"found": False, "summary": "No verification mail from " + ", ".join(PROVIDERS[pid]["mail_domains"]) + " yet (it must also pass DKIM). Wait a minute and ask again."})
        what = " and ".join(x for x in (f"code {got['code']}" if got["code"] else "", f"link {got['link']}" if got["link"] else "") if x)
        return Ok({"found": True, **got, "summary": f"Verification mail \"{got['subject']}\": {what}. Use only this code or link."})
