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

from . import core, fetch, verify
from .core import ResearchError

LOCAL = "http://127.0.0.1:8080/v1/chat/completions"
GATEWAY_REDACT = "http://127.0.0.1:48950/redact"


def _redact(text: str) -> str:
    req = urllib.request.Request(GATEWAY_REDACT, data=json.dumps({"text": text}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["text"]


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

        def search(q: str, n: int) -> list[dict]:
            fut = asyncio.run_coroutine_threadsafe(
                self.plugins.call_entry_json("web_search:search", {"query": q, "max_results": n}, timeout=40.0), loop)
            res = fut.result(60)
            if isinstance(res, Err):
                raise ResearchError(f"search failed: {str(getattr(res, 'error', res))[:120]}")
            data = res.value.get("data", res.value) if isinstance(res.value, dict) else {}
            return [r for r in (data.get("results") or []) if isinstance(r, dict) and r.get("url")]

        def work() -> dict:
            notes: list[str] = []
            try:
                redact = _redact if _gateway_up() else None
            except Exception:  # noqa: BLE001
                redact = None
            if redact is None:
                notes.append("privacy gateway down: the question was searched as typed")
            out = core.run(question, depth=depth, search=search, fetch=fetch.fetch, plan_llm=_plan, write_llm=_writer(notes),
                           verifier=verify.supported if verify.available() else None, redact=redact,
                           fallback_llm=lambda s, u: _local_llm(s, u, max_tokens=2500))
            out["notes"] = notes + out["notes"]
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
            return Err(SdkError(str(exc)))
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


def _gateway_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:48950/health", timeout=3) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False
