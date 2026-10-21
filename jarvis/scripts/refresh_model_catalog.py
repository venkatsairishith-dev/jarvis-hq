"""Build jarvis/config/model_catalog.json: what the open models.dev catalog DECLARES about the models our providers serve.

    python jarvis/scripts/refresh_model_catalog.py --source api.json      # a copy you downloaded
    python jarvis/scripts/refresh_model_catalog.py --fetch                # https://models.dev/api.json (public, no key)

models.dev (github.com/anomalyco/models.dev, MIT) is the model database OpenCode uses. We keep only the models that
jarvis/config/providers.json can actually reach (each provider's default_model and the models its model_pattern
allows), with the declared fields the selector needs. Declared is not measured: model_select.py labels these facts
"declared by models.dev" and keeps measured facts (audit log usage, benchmark results) apart.
A provider with no models.dev listing (Dahl, self-hosted Ollama tunnels, Puter, the local llama.cpp servers) gets no
entry, so the selector reports its capabilities as unknown instead of borrowing another host's numbers.
Standard library only. Writes one file; changes no provider setting.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROVIDERS = os.path.join(ROOT, "config", "providers.json")
OUT = os.path.join(ROOT, "config", "model_catalog.json")
URL = "https://models.dev/api.json"
MAX_BYTES = 64 << 20

# our provider name -> models.dev provider id. Missing names have no listing there (see the docstring).
CATALOG_IDS = {"groq": "groq", "openrouter-private": "openrouter", "openrouter-public": "openrouter",
               "cloudflare-workers-ai": "cloudflare-workers-ai", "zai-glm": "zai", "ollama-cloud": "ollama-cloud",
               "kilo": "kilo", "gemini": "google"}
FIELDS = ("name", "family", "tool_call", "attachment", "reasoning", "structured_output", "open_weights", "modalities",
          "limit", "cost", "release_date", "last_updated")


def fetch(url: str = URL) -> bytes:
    if not url.startswith("https://"):
        raise ValueError("https only")
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-catalog/1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("catalog larger than expected; refusing")
    return data


def build(raw: bytes, providers_cfg: dict, source: str, today: str | None = None) -> dict:
    cat = json.loads(raw.decode("utf-8"))
    out = {}
    for p in providers_cfg.get("providers", []):
        cid = CATALOG_IDS.get(p.get("name"))
        listing = (cat.get(cid) or {}).get("models") or {} if cid else {}
        if not listing:
            continue
        pat, default = p.get("model_pattern"), p.get("default_model")
        keep = {}
        for mid, m in sorted(listing.items()):
            if mid == default or (pat and re.search(pat, mid)):
                keep[mid] = {k: m[k] for k in FIELDS if k in m}
        if not keep:
            continue
        out[p["name"]] = {"catalog_provider": cid, "default_model_listed": default in listing if default else None,
                          "models": keep}
    return {"_note": "Declared by models.dev, not measured by JARVIS. Rebuild with jarvis/scripts/refresh_model_catalog.py. "
                     "Costs are list prices in USD per million tokens as published there; whether a call costs money is "
                     "decided by providers.json (paid must stay false), never by this file.",
            "source": source, "licence": "MIT (github.com/anomalyco/models.dev)",
            "source_sha256": hashlib.sha256(raw).hexdigest(), "built": today or datetime.date.today().isoformat(),
            "providers": out}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--source", help="a downloaded copy of models.dev/api.json")
    g.add_argument("--fetch", action="store_true", help="download " + URL)
    ap.add_argument("--providers", default=PROVIDERS)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args(argv)
    raw = fetch() if a.fetch else open(a.source, "rb").read()
    with open(a.providers, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    doc = build(raw, cfg, URL)
    tmp = a.out + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, a.out)
    n = sum(len(v["models"]) for v in doc["providers"].values())
    print(f"wrote {a.out}: {len(doc['providers'])} providers, {n} models, source sha256 {doc['source_sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
