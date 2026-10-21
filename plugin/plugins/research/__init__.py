"""Research: bounded web research with a citation on every factual sentence, each checked against its source.

Search goes through N.E.K.O's own web_search plugin (after the question is redacted by the privacy gateway); pages are
fetched with SSRF and size limits; Dahl's MiniMax writes through the privacy gateway (local generalist if that fails);
a local NLI model checks each cited sentence. The result is a report spec that the deliverables plugin turns into a PDF
or a deck. Logic in core.py (tested without a network).
"""

from __future__ import annotations

import asyncio
import json
import urllib.request

from plugin.sdk.plugin import Err, NekoPluginBase, Ok, SdkError, lifecycle, neko_plugin, plugin_entry, tr
from plugin.plugins._shared import loopback_http  # noqa: E402

from . import core, fetch, verify
from . import search as searchmod
from .core import ResearchError

LOCAL = "http://127.0.0.1:8080/v1/chat/completions"
GATEWAY_BASE = "http://127.0.0.1:48950"
GATEWAY_REDACT = GATEWAY_BASE + "/redact"
GATEWAY_HEALTH = GATEWAY_BASE + "/health"


NO_GATEWAY = ("The privacy gateway (127.0.0.1:48950) is not answering, so nothing was searched: the question may contain private "
              "details and nothing may leave this PC unchecked. Start the gateway (neko-start.ps1) and ask again.")
NOT_READY = ("The privacy gateway is running but cannot hide names and places and check for secrets right now ({why}), so nothing "
             "was searched: the question may name people or places. Restore the gateway's PII model (see its /health) and ask again.")


def _gateway_state() -> tuple[bool, str]:
    """(ready, why not). The gateway must answer /health with ready=true AND its PII model loaded; an HTTP 200 from anything
    on that port is not enough (audit M5, 2026-10-18). Never raises, never echoes a payload."""
    try:
        with loopback_http.urlopen(GATEWAY_HEALTH, timeout=3) as r:
            if r.status != 200:
                return False, f"HTTP {r.status}"
            health = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__
    if not isinstance(health, dict) or health.get("ok") is not True:
        return False, "not the privacy gateway"
    if health.get("ready") is not True:
        return False, "degraded: " + ", ".join(str(x)[:40] for x in (health.get("degraded") or ["not ready"])[:3])
    if health.get("pii_model") is not True:
        return False, "the PII model is not loaded"
    return True, ""


def _gateway_up() -> bool:
    return _gateway_state()[0]


def _redact(text: str) -> str:
    """The question with private details AND names and places replaced, from the privacy gateway. Any failure refuses the run
    (fail closed): a question the gateway could not check is never searched as typed. The gateway's answer must itself say
    that names redaction and the PII model were active for this call; an older or weaker gateway is refused."""
    req = urllib.request.Request(GATEWAY_REDACT, data=json.dumps({"text": text, "names": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with loopback_http.urlopen(req, timeout=60) as r:
            answer = json.loads(r.read().decode())
        out = answer["text"]
    except Exception as e:  # noqa: BLE001 - say that it was refused, never the question
        raise ResearchError(f"The privacy gateway could not check the question ({type(e).__name__}), so nothing was searched.") from None
    if not isinstance(out, str) or not isinstance(answer, dict):
        raise ResearchError("The privacy gateway gave an unusable answer, so nothing was searched.")
    if answer.get("names") is not True or answer.get("deep") is not True or answer.get("ready") is False:
        raise ResearchError(NOT_READY.format(why="names or the PII model were not active for this check"))
    return out


def _redactor(allow_unredacted: bool = False):
    """(redact function or None, note or None) for one run. MEM-M11: when the privacy gateway does not answer, the question is
    NOT searched (ResearchError) - it used to be searched as typed with only a note. `allow_unredacted=True` is the explicit
    offline/test path (no gateway on the machine); the research_topic entry never sets it and takes no such argument, so a
    caller of the tool cannot switch the check off. A gateway that answers but is degraded (no PII model) is refused too."""
    try:
        up = bool(_gateway_up())
    except Exception:  # noqa: BLE001
        up = False
    if up:
        return _redact, None
    if allow_unredacted:
        return None, "privacy gateway down: the question was searched as typed (explicitly allowed: offline test path)"
    try:
        why = _gateway_state()[1]                 # only to word the refusal: not answering, or answering but degraded
    except Exception:  # noqa: BLE001
        why = "error"
    unreachable = why in ("error", "URLError", "ConnectionRefusedError", "TimeoutError", "OSError", "timeout") or why.startswith("HTTP")
    raise ResearchError(NO_GATEWAY if unreachable else NOT_READY.format(why=why or "not ready"))


def _local_llm(system: str, user: str, *, json_schema: dict | None = None, max_tokens: int = 600, think: bool = False) -> str:
    from plugin.plugins.local_team.core import post_json
    body = {"model": "general", "max_tokens": max_tokens, "temperature": 0.2, "chat_template_kwargs": {"enable_thinking": think},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "out", "schema": json_schema}}
    out = post_json(LOCAL, body, timeout=300)
    return str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def _plan(system: str, user: str) -> str:
    return _local_llm(system, user, json_schema={"type": "object", "properties": {"queries": {"type": "array", "items": {"type": "string"},
                                                                                            "minItems": 1, "maxItems": 5}},
                                                 "required": ["queries"]}, max_tokens=300)


def _writer(notes: list):
    def write(system: str, user: str) -> str:
        from plugin.plugins.local_team import core as team
        from utils.config_manager import get_config_manager
        cfg = get_config_manager().get_model_api_config("agent")
        base, model, key = str(cfg.get("base_url") or ""), str(cfg.get("model") or ""), str(cfg.get("api_key") or "")
        if team.adviser_allowed(base) and model:
            try:
                return team.post_stream(f"{base.rstrip('/')}/chat/completions", {
                    "model": model, "max_tokens": 3500, "temperature": 0.3,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                    headers={"Authorization": f"Bearer {key}"} if key else None, timeout=420)
            except team.TeamError as e:
                notes.append(f"public writer failed ({e}); the local model wrote the report")
        else:
            notes.append("the public model is not routed through the privacy gateway, so the local model wrote the report")
        return _local_llm(system, user, max_tokens=2500)
    return write


@neko_plugin
class ResearchPlugin(NekoPluginBase):
    @lifecycle(id="startup")
    async def startup(self, **_):
        return Ok({"status": "ready", "claim_checker": verify.available()})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_):
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="research_topic",
        name=tr("entry.research_topic.name", default="Research a topic"),
        description=tr("entry.research_topic.description", default=(
            "Research a question on the web within fixed limits and return a report in which every factual sentence cites "
            "a source and was checked against it; unconfirmed statements are labelled. Optionally turn it into a PDF report "
            "or an animated presentation (deliverable=report|presentation). Use for questions that need current or "
            "multiple sources.")),
        input_schema={"type": "object", "properties": {
            "question": {"type": "string", "description": "The research question, in the owner's words."},
            "depth": {"type": "string", "enum": ["quick", "standard"], "default": "standard"},
            "deliverable": {"type": "string", "enum": ["none", "report", "presentation"], "default": "none"},
            "open_after": {"type": "boolean", "default": False}},
            "required": ["question"]},
        timeout=900,
        llm_result_fields=["summary", "stats", "file", "sources"],
    )
    async def research_topic(self, question: str = "", depth: str = "standard", deliverable: str = "none",
                             open_after: bool = False, **_):
        loop = asyncio.get_running_loop()

        def web_search(q: str, n: int) -> list[dict]:
            fut = asyncio.run_coroutine_threadsafe(
                self.plugins.call_entry_json("web_search:search", {"query": q, "max_results": n}, timeout=40.0), loop)
            res = fut.result(60)
            if isinstance(res, Err):
                raise searchmod.SearchError(f"web_search failed: {str(getattr(res, 'error', res))[:120]}")
            data = res.value.get("data", res.value) if isinstance(res.value, dict) else {}
            return [r for r in (data.get("results") or []) if isinstance(r, dict) and r.get("url")]

        search_notes: list[str] = []

        def search(q: str, n: int) -> list[dict]:
            secondary = searchmod.tavily_search if searchmod.tavily_key() else None
            return searchmod.merged_search(q, n, web_search, secondary, search_notes)

        def work() -> dict:
            notes: list[str] = []
            redact, why = _redactor()                 # raises ResearchError (nothing searched) when the gateway is down
            if why:
                notes.append(why)
            out = core.run(question, depth=depth, search=search, fetch=fetch.fetch, plan_llm=_plan, write_llm=_writer(notes),
                           verifier=verify.supported if verify.available() else None, redact=redact,
                           fallback_llm=lambda s, u: _local_llm(s, u, max_tokens=2500))
            out["notes"] = notes + list(dict.fromkeys(search_notes)) + out["notes"]
            out["search_providers"] = ["web_search"] + (["tavily"] if searchmod.tavily_key() else [])
            record = out.pop("record")
            folder = None
            if deliverable in ("report", "presentation"):
                from plugin.plugins.deliverables import core as deliver
                made = deliver.make_report(out["spec"], open_after) if deliverable == "report" \
                    else deliver.make_presentation(core.to_deck(out["spec"]), open_after)
                out["file"], out["qa_passed"], folder = made["file"], made["qa"]["passed"], made["folder"]
            out["record_file"] = _save_record(folder, question, {**out, **record})
            out["summary"] = out["spec"]["summary"]
            out["sources"] = [f"[{s['id']}] {s['title']} — {s['url']}" for s in out["spec"]["sources"]]
            return out

        try:
            return Ok(await asyncio.to_thread(work))
        except ResearchError as exc:
            why = "; ".join(dict.fromkeys(search_notes))[-200:]       # e.g. "web_search failed (...)": the real reason
            return Err(SdkError(str(exc) + (f" Search: {why}" if why else "")))
        except Exception as exc:  # noqa: BLE001 - say what failed, never a value or a URL with a query
            return Err(SdkError(f"research failed ({type(exc).__name__})"))


def _save_record(folder: str | None, question: str, data: dict) -> str:
    """The whole run (queries, sources, passages, the writer's draft, check results) for review and evals."""
    import os
    import time
    from pathlib import Path
    from plugin.plugins.deliverables.tools import slug
    d = Path(folder) if folder else Path(os.environ.get("RESEARCH_OUT_DIR", r"D:\jarvis\outputs\neko\research"))
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("research.json" if folder else f"{time.strftime('%Y%m%d-%H%M%S')}-{slug(question)}.json")
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(p)
