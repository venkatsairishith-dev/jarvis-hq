"""Propose-Probe-Compose for JARVIS workflows (the method of Harness², google-research/harness2, Apache-2.0, adapted).

Harness² refines an agent's harness for the task at hand without ground-truth labels: it PROPOSES edits (general
"invariant" ones and task-specific "domain" ones), PROBES each candidate on the same task, and COMPOSES a refined harness
from the behavioural contrasts between the executions; a persistent library keeps the adopted harnesses and
evidence-supported lessons for the next task. Its executors (OpenCode / Codex) run with unrestricted network and, for
Codex, the sandbox disabled, so they are NOT used here. What is used is the method, on JARVIS's own parts:

- the "harness" is a declarative workflow (``sandbox_runner`` language) plus its instructions;
- a probe is ``sandbox.probe``: synthetic cases, explicit checks, a separate isolated process;
- the proposer is pluggable: ``RepairProposer`` (deterministic, no model), or a local-model proposer whose suggestions
  are only accepted as edit patches that pass schema checks;
- composition is greedy and evidence-led: an edit joins the composite only if the probe does not get worse with it;
- adoption needs every case to pass (a validation gate); nothing is adopted on a proposer's say-so;
- lessons are written only for edits whose contrast was observed ("helped" with no case regressing).

Budget accounting mirrors Harness²'s ledger: ``rollouts`` (probe executions) and ``proposer_calls``.

Edits are patches against step ids (``_sid``), so edits proposed against the same base can be composed:
    {"op": "set", "sid": "s3", "key": "ignore_case", "value": true}   {"op": "unset", "sid": "s3", "key": "invert"}
    {"op": "insert_after", "sid": "s2", "step": {...}}                {"op": "remove", "sid": "s4"}
    {"op": "replace", "sid": "s4", "step": {...}}
    {"op": "wrap", "sid": "s2", "key": "pattern", "prefix": "^.*?", "suffix": ".*$"}   (relative: composes with other edits)
    {"op": "replace_in", "sid": "s2", "key": "pattern", "old": "\\w+", "new": "[\\w.-]+"}
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import sandbox

MAX_K = 4
MAX_EDITS_PER_FAMILY = 8


class ProposerProto(Protocol):
    def propose(self, workflow: dict[str, Any], evidence: dict[str, Any], family: str, n: int) -> list[dict[str, Any]]: ...


def with_ids(workflow: dict[str, Any]) -> dict[str, Any]:
    wf = copy.deepcopy(workflow)
    used = {st.get("_sid") for st in wf.get("steps") or [] if isinstance(st, dict)}
    n = 0
    for st in wf.get("steps") or []:
        if isinstance(st, dict) and not st.get("_sid"):
            while f"s{n}" in used:
                n += 1
            st["_sid"] = f"s{n}"
            used.add(st["_sid"])
    return wf


def runnable(workflow: dict[str, Any]) -> dict[str, Any]:
    wf = copy.deepcopy(workflow)
    wf["steps"] = [{k: v for k, v in st.items() if k != "_sid"} for st in wf.get("steps") or [] if isinstance(st, dict)]
    return wf


class PatchError(ValueError):
    pass


def apply_patch(workflow: dict[str, Any], patch: list[dict[str, Any]]) -> dict[str, Any]:
    wf = copy.deepcopy(workflow)
    steps = wf.get("steps") or []
    for p in patch:
        idx = next((i for i, st in enumerate(steps) if st.get("_sid") == p.get("sid")), None)
        if idx is None:
            raise PatchError(f"no step {p.get('sid')!r}")
        op = p.get("op")
        if op == "set":
            if str(p.get("key", "")) in ("op", "_sid"):
                raise PatchError("cannot change a step's operation with set")
            steps[idx][str(p["key"])] = p.get("value")
        elif op == "wrap":                                     # relative edits compose (two "set"s on one key would not)
            key = str(p.get("key", ""))
            val = str(steps[idx].get(key, ""))
            pre, suf = str(p.get("prefix", "")), str(p.get("suffix", ""))
            steps[idx][key] = (val if val.startswith(pre) else pre + val) if pre else val
            steps[idx][key] = steps[idx][key] if not suf or steps[idx][key].endswith(suf) else steps[idx][key] + suf
        elif op == "replace_in":
            key, old = str(p.get("key", "")), str(p.get("old", ""))
            val = str(steps[idx].get(key, ""))
            if not old or old not in val:
                raise PatchError(f"{old!r} is not in step {p.get('sid')!r}")
            steps[idx][key] = val.replace(old, str(p.get("new", "")))
        elif op == "unset":
            steps[idx].pop(str(p.get("key", "")), None)
        elif op == "insert_after":
            new = dict(p.get("step") or {})
            new["_sid"] = f"{p['sid']}.{len(steps)}"
            steps.insert(idx + 1, new)
        elif op == "remove":
            steps.pop(idx)
        elif op == "replace":
            new = dict(p.get("step") or {})
            new["_sid"] = steps[idx]["_sid"]
            steps[idx] = new
        else:
            raise PatchError(f"unknown patch op {op!r}")
    wf["steps"] = steps
    return wf


class RepairProposer:
    """Deterministic edits, no model. Invariant: robustness edits any text workflow may need. Domain: hypotheses about
    this workflow's own data handling (a filter that drops needed rows, a bound that is the wrong way round)."""

    def propose(self, workflow: dict[str, Any], evidence: dict[str, Any], family: str, n: int) -> list[dict[str, Any]]:
        steps = workflow.get("steps") or []
        out: list[dict[str, Any]] = []
        if family == "inv":
            names = list(evidence.get("input_names") or [])
            defined = set(names)
            for st in steps:
                src = st.get("from")
                if src and src not in defined and len(names) == 1 and st.get("op") != "template":
                    out.append({"change": f"read the case's input '{names[0]}' in step {st['_sid']} (it read '{src}', which nothing defines)",
                                "patch": [{"op": "set", "sid": st["_sid"], "key": "from", "value": names[0]}]})
                if st.get("to"):
                    defined.add(st["to"])
            regexy = [st for st in steps if st.get("op") in ("filter", "extract", "map_regex", "where") and not st.get("ignore_case")]
            if regexy:
                out.append({"change": "match text without regard to letter case",
                            "patch": [{"op": "set", "sid": st["_sid"], "key": "ignore_case", "value": True} for st in regexy]})
            for st in steps:
                if st.get("op") == "lines":
                    out.append({"change": f"trim spaces around each line read in step {st['_sid']}",
                                "patch": [{"op": "insert_after", "sid": st["_sid"],
                                           "step": {"op": "strip", "from": st["to"], "to": st["to"]}}]})
                    out.append({"change": f"drop duplicate lines after step {st['_sid']}",
                                "patch": [{"op": "insert_after", "sid": st["_sid"],
                                           "step": {"op": "dedupe", "from": st["to"], "to": st["to"]}}]})
        else:
            for st in steps:
                if st.get("op") == "map_regex" and not str(st.get("pattern", "")).startswith("^"):
                    out.append({"change": f"match the whole line in step {st['_sid']} so only the captured part is kept",
                                "patch": [{"op": "wrap", "sid": st["_sid"], "key": "pattern", "prefix": "^.*?", "suffix": ".*$"}]})
                if st.get("op") in ("map_regex", "extract", "filter") and r"\w+" in str(st.get("pattern", st.get("regex", ""))):
                    key = "regex" if st.get("op") == "filter" else "pattern"
                    out.append({"change": f"let the word pattern in step {st['_sid']} include dots and dashes",
                                "patch": [{"op": "replace_in", "sid": st["_sid"], "key": key, "old": r"\w+", "new": r"[\w.-]+"}]})
                if st.get("op") in ("filter", "where"):
                    out.append({"change": f"remove the {st['op']} in step {st['_sid']} (it may drop data the result needs)",
                                "patch": [{"op": "remove", "sid": st["_sid"]}]})
                if st.get("op") == "filter":
                    out.append({"change": f"invert the filter in step {st['_sid']}",
                                "patch": [{"op": "set", "sid": st["_sid"], "key": "invert", "value": not st.get("invert")}]})
                if st.get("op") == "where" and ("gt" in st or "lt" in st):
                    key, other = ("gt", "lt") if "gt" in st else ("lt", "gt")
                    out.append({"change": f"use the opposite bound ({other}) in step {st['_sid']}",
                                "patch": [{"op": "unset", "sid": st["_sid"], "key": key},
                                          {"op": "set", "sid": st["_sid"], "key": other, "value": st[key]}]})
        return out[:n]


@dataclass
class Ledger:
    mode: str
    k: int
    rollouts: int = 0
    proposer_calls: int = 0
    entries: list[dict[str, Any]] = field(default_factory=list)

    def rollout(self, label: str, p: dict[str, Any]) -> None:
        self.rollouts += 1
        self.entries.append({"kind": "rollout", "label": label, "passed": f"{p['passed_cases']}/{p['cases']}",
                             "checks": f"{p['checks_passed']}/{p['checks_total']}", "errors": p["errors"]})

    def session(self, label: str, n: int) -> None:
        self.proposer_calls += 1
        self.entries.append({"kind": "proposer", "label": label, "edits": n})


def _regressed(base: dict[str, Any], cand: dict[str, Any]) -> list[str]:
    b = {d["case"]: d["passed"] for d in base.get("detail") or []}
    return [d["case"] for d in cand.get("detail") or [] if b.get(d["case"]) and not d["passed"]]


def _fixed(base: dict[str, Any], cand: dict[str, Any]) -> list[str]:
    before = {f for d in base.get("detail") or [] for f in d["failures"]}
    after = {f for d in cand.get("detail") or [] for f in d["failures"]}
    return sorted(before - after)[:6]


def refine(workflow: dict[str, Any], cases: list[dict[str, Any]], proposer: ProposerProto | None = None,
           k: int = 2, mode: str = "parallel", cue: str = "") -> dict[str, Any]:
    """One task-level recursion. Returns the adopted workflow (or None), the evidence table, lessons and the ledger."""
    if not cases:
        return {"adopted": None, "status": "needs examples",
                "why": "no test cases: without an example input and explicit checks nothing can be validated"}
    k = max(1, min(int(k), MAX_K))
    proposer = proposer or RepairProposer()
    ledger = Ledger(mode, k)
    base = with_ids(workflow)
    base_probe = sandbox.probe(runnable(base), cases)
    input_names = sorted({str(k) for c in cases for k in (c.get("inputs") or {})})
    base_probe["input_names"] = input_names
    ledger.rollout("base", base_probe)
    table: list[dict[str, Any]] = [{"id": "base", "family": "base", "change": "(as proposed)", "probe": _brief(base_probe)}]
    best = {"id": "base", "workflow": base, "probe": base_probe, "lineage": []}
    rounds = 1 if mode == "parallel" else k
    per_family = k if mode == "parallel" else 1
    current = best
    for rnd in range(1, rounds + 1):
        cands = []
        for fam in ("inv", "dom"):
            try:
                edits = proposer.propose(current["workflow"], current["probe"], fam, per_family) or []
            except Exception as e:  # noqa: BLE001 - a proposer failure is evidence too, never a crash
                edits = []
                table.append({"id": f"r{rnd}-{fam}", "family": fam, "change": f"proposer failed ({type(e).__name__})", "probe": None})
            ledger.session(f"r{rnd}-{fam}", len(edits))
            for j, e in enumerate(edits[:MAX_EDITS_PER_FAMILY], 1):
                try:
                    wf = apply_patch(current["workflow"], list(e.get("patch") or []))
                except PatchError as pe:
                    table.append({"id": f"r{rnd}-{fam}{j}", "family": fam, "change": str(e.get("change", ""))[:200],
                                  "probe": None, "verdict": f"not applied ({pe})"})
                    continue
                cands.append({"id": f"r{rnd}-{fam}{j}", "family": fam, "change": str(e.get("change", ""))[:200],
                              "patch": e.get("patch"), "workflow": wf})
        for c in cands:
            p = sandbox.probe(runnable(c["workflow"]), cases)
            p["input_names"] = input_names
            ledger.rollout(c["id"], p)
            c["probe"] = p
            reg = _regressed(current["probe"], p)
            better = sandbox.quality(p) > sandbox.quality(current["probe"])       # behaviour, not timing noise
            c["verdict"] = "helped" if better and not reg else ("regressed: " + ", ".join(reg)) if reg else "no observed effect"
            c["fixed"] = _fixed(current["probe"], p) if c["verdict"] == "helped" else []
            table.append({"id": c["id"], "family": c["family"], "change": c["change"], "probe": _brief(p),
                          "verdict": c["verdict"], "fixed": c["fixed"]})
        # pairs: two edits that each changed nothing may only work together (Harness² composes from contrasts; with no
        # helpful edit, the cheapest contrast left is a pair). Bounded by k and only within this round's candidates.
        if not any(c["verdict"] == "helped" for c in cands):
            flat = [c for c in cands if c["verdict"] == "no observed effect" and c.get("patch")]

            def sids(c: dict[str, Any]) -> set[str]:
                return {str(p.get("sid")) for p in c["patch"]}
            # most promising first: two edits on the same step, then edits about the task's own data (domain)
            order = sorted(((i, j) for i in range(len(flat)) for j in range(i + 1, len(flat))),
                           key=lambda ij: (not (sids(flat[ij[0]]) & sids(flat[ij[1]])),
                                           (flat[ij[0]]["family"] == "inv") + (flat[ij[1]]["family"] == "inv")))
            tried = 0
            for i, j in order:
                if tried >= k:
                    break
                try:
                    wf2 = apply_patch(apply_patch(current["workflow"], list(flat[i]["patch"])), list(flat[j]["patch"]))
                except PatchError:
                    continue
                tried += 1
                p2 = sandbox.probe(runnable(wf2), cases)
                p2["input_names"] = input_names
                pid = f"r{rnd}-pair{tried}"
                ledger.rollout(pid, p2)
                reg = _regressed(current["probe"], p2)
                verdict = "helped" if sandbox.quality(p2) > sandbox.quality(current["probe"]) and not reg else "no observed effect"
                pc = {"id": pid, "family": "pair", "change": f"{flat[i]['change']} + {flat[j]['change']}",
                      "patch": list(flat[i]["patch"]) + list(flat[j]["patch"]), "workflow": wf2, "probe": p2, "verdict": verdict,
                      "fixed": _fixed(current["probe"], p2) if verdict == "helped" else []}
                table.append({"id": pid, "family": "pair", "change": pc["change"][:200], "probe": _brief(p2),
                              "verdict": verdict, "fixed": pc["fixed"]})
                cands.append(pc)
        # compose: greedily add helpful edits (best first) while the probe does not get worse
        good = sorted([c for c in cands if c["verdict"] == "helped"], key=lambda c: sandbox.score(c["probe"]), reverse=True)
        composed, composed_probe, parts = current["workflow"], current["probe"], []
        part_cands: list[dict[str, Any]] = []
        for c in good:
            try:
                trial = apply_patch(composed, list(c["patch"] or []))
            except PatchError:
                continue
            p = sandbox.probe(runnable(trial), cases)
            p["input_names"] = input_names
            ledger.rollout(f"r{rnd}-compose+{c['id']}", p)
            if sandbox.quality(p) >= sandbox.quality(composed_probe) and not _regressed(composed_probe, p):
                composed, composed_probe, parts = trial, p, parts + [c["id"]]
                part_cands.append(c)
        if parts:
            table.append({"id": f"r{rnd}-composed", "family": "composed", "change": " + ".join(parts),
                          "probe": _brief(composed_probe), "verdict": "composite"})
        lin = current["lineage"]
        pool = [current] + [{"id": c["id"], "workflow": c["workflow"], "probe": c["probe"],
                             "lineage": lin + ([c] if c["verdict"] == "helped" else [])} for c in cands] + (
            [{"id": f"r{rnd}-composed", "workflow": composed, "probe": composed_probe, "lineage": lin + part_cands}]
            if parts else [])
        current = max(pool, key=lambda x: sandbox.score(x["probe"]))
        if current["probe"]["passed_cases"] == current["probe"]["cases"] and current["probe"]["runnable"]:
            break
    best = current
    all_pass = best["probe"]["runnable"] and best["probe"]["passed_cases"] == best["probe"]["cases"]
    lessons = [_lesson(c, base_probe, cue) for c in best["lineage"]]     # only edits on the adopted path, each observed to help
    return {"adopted": runnable(best["workflow"]) if all_pass else None,
            "best": best["id"], "status": "validated" if all_pass else "not validated",
            "why": "" if all_pass else (f"the best candidate passed {best['probe']['passed_cases']} of "
                                        f"{best['probe']['cases']} cases"),
            "best_probe": _brief(best["probe"]), "base_probe": _brief(base_probe), "table": table,
            "lessons": lessons if all_pass else [], "ledger": {"mode": mode, "k": k, "rollouts": ledger.rollouts,
                                                                 "proposer_calls": ledger.proposer_calls},
            "at": round(time.time(), 3)}


def _brief(p: dict[str, Any] | None) -> dict[str, Any] | None:
    if p is None:
        return None
    return {"passed": f"{p['passed_cases']}/{p['cases']}", "checks": f"{p['checks_passed']}/{p['checks_total']}",
            "errors": p["errors"], "steps": p["steps"], "runnable": p["runnable"],
            "failures": [f for d in p.get("detail") or [] for f in d["failures"]][:6] + (p.get("problems") or [])[:3]}


def _lesson(c: dict[str, Any], base_probe: dict[str, Any], cue: str) -> dict[str, Any]:
    p = c["probe"]
    conf = "high" if p["cases"] >= 2 and p["passed_cases"] == p["cases"] else "medium" if p["passed_cases"] else "low"
    return {"cue": cue[:200], "lesson": c["change"], "family": c["family"],
            "evidence": (f"base passed {base_probe['passed_cases']}/{base_probe['cases']} cases "
                         f"({base_probe['checks_passed']}/{base_probe['checks_total']} checks); with this edit "
                         f"{p['passed_cases']}/{p['cases']} ({p['checks_passed']}/{p['checks_total']}); fixed: "
                         + "; ".join(c.get("fixed") or [])[:300]),
            "confidence": conf}
