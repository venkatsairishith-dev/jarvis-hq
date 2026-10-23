"""Runs ONE declarative workflow on ONE test case, in its own process. Standard library only; imports nothing else.

    python -I sandbox_runner.py <job_dir>       # job_dir/job.json in, job_dir/result.json and job_dir/out/* out

The workflow language is a closed set of text and table operations (below). There is no operation that opens a network
connection, starts a process, imports code, evaluates an expression or touches a path outside job_dir/out: a workflow can
only transform the case's input text into output files. The parent starts this with ``-I`` (no user site, no environment
paths), an empty environment, job_dir as the working directory and a hard timeout; on Linux it also caps CPU time and memory.

Operations: set, lines, join, strip, lower, upper, dedupe, sort, filter, extract, map_regex, csv_read, csv_write, select,
where, aggregate, sum, count, template, md_table, json_read, json_get, write. Each takes "from" / "to" variable names; see
OPS for the parameters. Limits: 100 steps, 2 MB per value, 50,000 rows, regex patterns of at most 300 characters.
"""
import csv
import io
import json
import os
import re
import sys
import time
from decimal import Decimal, InvalidOperation

MAX_STEPS, MAX_VALUE, MAX_ROWS, MAX_PATTERN = 100, 2_000_000, 50_000, 300
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$")
OPS = {
    "set": ("to", "value"), "lines": ("from", "to"), "join": ("from", "to", "sep"), "strip": ("from", "to"),
    "lower": ("from", "to"), "upper": ("from", "to"), "dedupe": ("from", "to"), "sort": ("from", "to", "reverse"),
    "filter": ("from", "to", "contains", "regex", "invert", "ignore_case"),
    "extract": ("from", "to", "pattern", "group", "all", "ignore_case"),
    "map_regex": ("from", "to", "pattern", "replace", "ignore_case"),
    "csv_read": ("from", "to", "delimiter"), "csv_write": ("from", "to", "columns"), "select": ("from", "to", "columns"),
    "where": ("from", "to", "column", "equals", "contains", "gt", "lt", "ignore_case"),
    "aggregate": ("from", "to", "group_by", "sum", "count"), "sum": ("from", "to", "column", "places"),
    "count": ("from", "to"), "template": ("to", "text"), "md_table": ("from", "to", "columns"),
    "json_read": ("from", "to"), "json_get": ("from", "to", "path"), "write": ("from", "file"),
}


class WorkflowError(Exception):
    pass


def _limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
    except Exception:  # noqa: BLE001 - Windows: the parent's timeout is the limit
        pass


def _rx(p, ignore_case=False):
    if not isinstance(p, str) or not p or len(p) > MAX_PATTERN:
        raise WorkflowError("a regex pattern is missing or longer than 300 characters")
    try:
        return re.compile(p, re.I if ignore_case else 0)
    except re.error as e:
        raise WorkflowError(f"bad regex: {e}")


def _size(v):
    n = len(v) if isinstance(v, str) else len(json.dumps(v, default=str))
    if n > MAX_VALUE:
        raise WorkflowError("a value grew past 2 MB")
    if isinstance(v, list) and len(v) > MAX_ROWS:
        raise WorkflowError("more than 50,000 rows")
    return v


def _each(v, fn):
    return [fn(x) for x in v] if isinstance(v, list) else fn(v)


def _get(vars_, name):
    if name not in vars_:
        raise WorkflowError(f"no value named {name!r} (inputs and earlier steps define names)")
    return vars_[name]


def _num(x):
    try:
        return Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise WorkflowError(f"not a number: {str(x)[:40]!r}")


def run(job):
    wf, case = job["workflow"], job["case"]
    outputs = set(wf.get("outputs") or [])
    vars_ = {}
    for name, text in (case.get("inputs") or {}).items():
        vars_[name] = str(text)
    written = []
    steps = wf.get("steps") or []
    if len(steps) > MAX_STEPS:
        raise WorkflowError("more than 100 steps")
    for i, st in enumerate(steps):
        op = st.get("op")
        if op not in OPS:
            raise WorkflowError(f"step {i + 1}: unknown operation {op!r}")
        extra = set(st) - set(OPS[op]) - {"op", "note"}
        if extra:
            raise WorkflowError(f"step {i + 1}: {op} does not take {sorted(extra)}")
        src = _get(vars_, st["from"]) if "from" in OPS[op] and op not in ("set", "template") else None
        ic = bool(st.get("ignore_case"))
        if op == "set":
            val = st.get("value", "")
        elif op == "lines":
            val = [x for x in str(src).splitlines()]
        elif op == "join":
            val = str(st.get("sep", "\n")).join(str(x) for x in src) if isinstance(src, list) else str(src)
        elif op == "strip":
            val = _each(src, lambda x: str(x).strip())
        elif op == "lower":
            val = _each(src, lambda x: str(x).lower())
        elif op == "upper":
            val = _each(src, lambda x: str(x).upper())
        elif op == "dedupe":
            seen, val = set(), []
            for x in src:
                k = json.dumps(x, sort_keys=True, default=str)
                if k not in seen:
                    seen.add(k)
                    val.append(x)
        elif op == "sort":
            val = sorted(src, key=lambda x: json.dumps(x, sort_keys=True, default=str), reverse=bool(st.get("reverse")))
        elif op == "filter":
            if st.get("regex"):
                rx = _rx(st["regex"], ic)
                keep = lambda x: bool(rx.search(str(x)))  # noqa: E731
            else:
                needle = str(st.get("contains", ""))
                keep = (lambda x: needle.lower() in str(x).lower()) if ic else (lambda x: needle in str(x))  # noqa: E731
            val = [x for x in src if keep(x) != bool(st.get("invert"))]
        elif op == "extract":
            rx, g = _rx(st.get("pattern"), ic), int(st.get("group", 0))
            if st.get("all"):
                val = [m.group(g) for x in (src if isinstance(src, list) else [src]) for m in rx.finditer(str(x))]
            else:
                m = rx.search("\n".join(map(str, src)) if isinstance(src, list) else str(src))
                val = m.group(g) if m else ""
        elif op == "map_regex":
            rx = _rx(st.get("pattern"), ic)
            val = _each(src, lambda x: rx.sub(str(st.get("replace", "")), str(x)))
        elif op == "csv_read":
            val = list(csv.DictReader(io.StringIO(str(src)), delimiter=str(st.get("delimiter", ","))[:1] or ","))
        elif op == "csv_write":
            rows = src if isinstance(src, list) else []
            cols = st.get("columns") or (list(rows[0].keys()) if rows and isinstance(rows[0], dict) else [])
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r if isinstance(r, dict) else {cols[0]: r} if cols else {})
            val = buf.getvalue()
        elif op == "select":
            cols = list(st.get("columns") or [])
            val = [{c: r.get(c, "") for c in cols} for r in src]
        elif op == "where":
            col = st.get("column")
            def ok(r):
                v = str(r.get(col, ""))
                v2 = v.lower() if ic else v
                if "equals" in st:
                    return v2 == (str(st["equals"]).lower() if ic else str(st["equals"]))
                if "contains" in st:
                    return (str(st["contains"]).lower() if ic else str(st["contains"])) in v2
                if "gt" in st:
                    return _num(v) > _num(st["gt"])
                if "lt" in st:
                    return _num(v) < _num(st["lt"])
                return True
            val = [r for r in src if ok(r)]
        elif op == "aggregate":
            groups = {}
            gb = st.get("group_by")
            for r in src:
                key = str(r.get(gb, "")) if gb else "all"
                g = groups.setdefault(key, {"group": key, "count": 0, "sum": Decimal(0)})
                g["count"] += 1
                if st.get("sum"):
                    g["sum"] += _num(r.get(st["sum"], 0))
            val = [{"group": g["group"], "count": str(g["count"]), "sum": str(g["sum"])} for g in groups.values()]
        elif op == "sum":
            total = sum((_num(r.get(st["column"], 0)) if isinstance(r, dict) else _num(r) for r in src), Decimal(0))
            places = int(st.get("places", 2))
            val = f"{total:.{places}f}"
        elif op == "count":
            val = str(len(src))
        elif op == "template":
            text = str(st.get("text", ""))
            def sub(m):
                v = vars_.get(m.group(1))
                if v is None:
                    raise WorkflowError(f"template names unknown value {m.group(1)!r}")
                return "\n".join(map(str, v)) if isinstance(v, list) else str(v)
            val = re.sub(r"\{([A-Za-z0-9_.-]{1,60})\}", sub, text)
        elif op == "md_table":
            cols = list(st.get("columns") or (list(src[0].keys()) if src else []))
            lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            lines += ["| " + " | ".join(str(r.get(c, "")).replace("|", "/") for c in cols) + " |" for r in src]
            val = "\n".join(lines)
        elif op == "json_read":
            try:
                val = json.loads(str(src))
            except ValueError as e:
                raise WorkflowError(f"not JSON: {e}")
        elif op == "json_get":
            val = src
            for part in str(st.get("path", "")).split("."):
                if part == "":
                    continue
                if isinstance(val, list) and part.isdigit() and int(part) < len(val):
                    val = val[int(part)]
                elif isinstance(val, dict) and part in val:
                    val = val[part]
                else:
                    val = ""
                    break
        elif op == "write":
            fname = str(st.get("file", ""))
            if not NAME.match(fname) or (outputs and fname not in outputs):
                raise WorkflowError(f"step {i + 1}: may only write the declared outputs, not {fname!r}")
            data = src if isinstance(src, str) else ("\n".join(map(str, src)) if isinstance(src, list) else str(src))
            with open(os.path.join("out", fname), "w", encoding="utf-8") as f:
                f.write(data)
            written.append(fname)
            continue
        vars_[st["to"]] = _size(val)
    return written


def main():
    job_dir = sys.argv[1]
    os.chdir(job_dir)
    _limits()
    os.makedirs("out", exist_ok=True)
    with open("job.json", encoding="utf-8") as f:
        job = json.load(f)
    t0 = time.time()
    try:
        written = run(job)
        res = {"ok": True, "written": written}
    except WorkflowError as e:
        res = {"ok": False, "error": str(e)[:300]}
    except (KeyError, TypeError, ValueError, AttributeError, IndexError, ZeroDivisionError) as e:
        res = {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    res["seconds"] = round(time.time() - t0, 4)
    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(res, f)


if __name__ == "__main__":
    main()
