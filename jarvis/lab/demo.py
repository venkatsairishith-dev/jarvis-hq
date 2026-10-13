"""JARVIS lab demo suite: tasks an agent with shell, files and sub-agents should pass.

Runs inside the WSL box (JarvisLab) as the unprivileged user. Each agent gets each task in a fresh folder; a checker
written here (never the agent's own claim) decides pass or fail. Results go to ~/lab/results/<time>.json.
Usage: python3 demo.py [--agents hermes,opencode,goose] [--tasks build,fix,data,delegate,refactor,desktop] [--timeout 900]
Custom code, justified: Terminal-Bench (the standard terminal-agent benchmark) needs Docker, which this box does not
have; this is a 5-task acceptance check for our own setup, not a benchmark.
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
WORK = os.path.join(HOME, "work", "demo")
RESULTS = os.path.join(HOME, "lab", "results")
PATH = os.path.join(HOME, ".local", "bin") + os.pathsep + os.environ.get("PATH", "")


def sh(cmd, cwd, timeout=120):
    r = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout, env={**os.environ, "PATH": PATH})
    return r.returncode, r.stdout + r.stderr


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def run_py(code, cwd):
    """Run checker code from a file (passing it through the shell mangled newlines)."""
    p = os.path.join(cwd, "_check.py")
    open(p, "w").write(code)
    try:
        return sh("python3 _check.py", cwd)
    finally:
        os.remove(p)


# ---------------------------------------------------------------- tasks: setup(dir) -> state, prompt, check(dir, state, log) -> (ok, why)

def build_setup(d):
    return {}


BUILD_PROMPT = ("In the current folder create duration.py with a function parse_duration(s) that converts strings like "
                "'1h30m', '45s', '2h', '1h2m3s' and '90m' into a number of seconds (int). Raise ValueError for invalid input "
                "such as '', 'abc' or '5x'. Also write test_duration.py with unittest tests, run them with "
                "`python3 -m unittest -v` and make sure they pass.")


def build_check(d, state, log):
    code = ("import sys; sys.path.insert(0, '.'); from duration import parse_duration as p\n"
            "assert p('1h30m') == 5400 and p('45s') == 45 and p('2h') == 7200 and p('1h2m3s') == 3723 and p('90m') == 5400\n"
            "for bad in ('', 'abc', '5x'):\n"
            "    try: p(bad); raise SystemExit('no ValueError for ' + repr(bad))\n"
            "    except ValueError: pass\n"
            "print('hidden tests ok')")
    rc, out = run_py(code, d)
    return rc == 0, out.strip()[-200:]


INVENTORY = '''class Inventory:
    def __init__(self):
        self.items = {}

    def add(self, name, qty):
        if qty <= 0:
            raise ValueError("qty must be positive")
        self.items[name] = qty            # bug: should add to the existing quantity

    def remove(self, name, qty):
        have = self.items.get(name, 0)
        if qty > have:
            raise ValueError("not enough stock")
        self.items[name] = have - qty
        # bug: an item that reaches 0 should disappear from self.items

    def total(self):
        return sum(self.items.values()) + 1   # bug: off by one
'''
INVENTORY_TESTS = '''import unittest
from inventory import Inventory


class T(unittest.TestCase):
    def test_add_accumulates(self):
        i = Inventory(); i.add("apple", 2); i.add("apple", 3)
        self.assertEqual(i.items["apple"], 5)

    def test_remove_to_zero_deletes(self):
        i = Inventory(); i.add("pear", 1); i.remove("pear", 1)
        self.assertNotIn("pear", i.items)

    def test_total(self):
        i = Inventory(); i.add("a", 1); i.add("b", 2)
        self.assertEqual(i.total(), 3)

    def test_errors(self):
        i = Inventory()
        with self.assertRaises(ValueError): i.add("x", 0)
        with self.assertRaises(ValueError): i.remove("x", 1)


if __name__ == "__main__":
    unittest.main()
'''


def fix_setup(d):
    open(os.path.join(d, "inventory.py"), "w").write(INVENTORY)
    open(os.path.join(d, "test_inventory.py"), "w").write(INVENTORY_TESTS)
    return {"test_sha": sha(os.path.join(d, "test_inventory.py"))}


FIX_PROMPT = ("The tests in this folder fail. Run `python3 -m unittest -v`, find the bugs in inventory.py and fix them. "
              "Do NOT change test_inventory.py. Stop when all tests pass.")


def fix_check(d, state, log):
    if sha(os.path.join(d, "test_inventory.py")) != state["test_sha"]:
        return False, "test file was changed"
    rc, out = sh("python3 -m unittest -q", d)
    return rc == 0, out.strip()[-200:]


def data_setup(d):
    rnd = random.Random(7)
    rows = [("order", "region", "product", "units", "unit_price")]
    for i in range(1, 61):
        rows.append((i, rnd.choice(["north", "south", "east", "west"]), rnd.choice(["bolt", "nut", "gear", "spring", "valve"]),
                     rnd.randint(1, 20), rnd.choice([0.5, 1.25, 3.0, 7.75, 12.4])))
    with open(os.path.join(d, "sales.csv"), "w", newline="") as fh:
        csv.writer(fh).writerows(rows)
    rev, units = {}, {}
    for _, region, product, u, price in rows[1:]:
        rev[region] = round(rev.get(region, 0) + u * price, 2)
        units[product] = units.get(product, 0) + u
    return {"revenue_by_region": {k: round(v, 2) for k, v in rev.items()}, "top_product": max(units, key=units.get)}


DATA_PROMPT = ("sales.csv in this folder has columns order, region, product, units, unit_price. Compute revenue "
               "(units * unit_price) per region and the product with the most units sold in total. Write report.json "
               'exactly like {"revenue_by_region": {"north": 123.45, ...}, "top_product": "bolt"} with revenue rounded to '
               "2 decimals. Check your numbers by running code, do not estimate.")


def data_check(d, state, log):
    try:
        got = json.load(open(os.path.join(d, "report.json")))
    except Exception as e:
        return False, f"no readable report.json ({type(e).__name__})"
    want = state
    rr = got.get("revenue_by_region") or {}
    bad = [k for k in want["revenue_by_region"] if abs(float(rr.get(k, -1)) - want["revenue_by_region"][k]) > 0.011]
    if bad or got.get("top_product") != want["top_product"]:
        return False, f"wrong values: regions {bad}, top {got.get('top_product')} (want {want['top_product']})"
    return True, "values match"


def delegate_setup(d):
    return {}


DELEGATE_PROMPT = ("This task tests your ability to delegate. You MUST use your sub-agent / delegation / task tool. "
                   "Delegate to one sub-agent: create primes.py with is_prime(n) for integers. Delegate to another sub-agent: "
                   "create words.py with count_words(text) returning a dict of word -> count (split on whitespace, lowercase). "
                   "Do not write primes.py or words.py yourself. When both exist, write main.py that prints is_prime(97) "
                   "and count_words('A b a') on two lines, run it, and report the output.")
DELEGATION_TOOLS = re.compile(r"delegate_task|\"task\"|subagent|sub_agent|dynamic_task|spawn_agent", re.I)


def delegate_check(d, state, log):
    code = ("import sys; sys.path.insert(0, '.'); from primes import is_prime; from words import count_words\n"
            "assert is_prime(97) and not is_prime(91) and not is_prime(1) and is_prime(2)\n"
            "assert count_words('A b a') == {'a': 2, 'b': 1}\nprint('modules ok')")
    rc, out = run_py(code, d)
    used = bool(DELEGATION_TOOLS.search(log))
    if rc != 0:
        return False, "modules wrong: " + out.strip()[-150:]
    if not used:
        return False, "modules work but no sub-agent tool call found in the agent's log"
    return True, "modules ok and a sub-agent tool was called"


def refactor_setup(d):
    os.makedirs(os.path.join(d, "shop"), exist_ok=True)
    open(os.path.join(d, "shop", "__init__.py"), "w").write("")
    open(os.path.join(d, "shop", "pricing.py"), "w").write(
        "def calc_total(prices, tax=0.1):\n    return round(sum(prices) * (1 + tax), 2)\n")
    open(os.path.join(d, "shop", "cart.py"), "w").write(
        "from shop.pricing import calc_total\n\n\nclass Cart:\n    def __init__(self):\n        self.prices = []\n\n"
        "    def add(self, p):\n        self.prices.append(p)\n\n    def total(self):\n        return calc_total(self.prices)\n")
    open(os.path.join(d, "test_shop.py"), "w").write(
        "import unittest\nfrom shop.pricing import calc_total\nfrom shop.cart import Cart\n\n\nclass T(unittest.TestCase):\n"
        "    def test_total(self):\n        self.assertEqual(calc_total([10, 20]), 33.0)\n\n"
        "    def test_cart(self):\n        c = Cart(); c.add(5); c.add(5)\n        self.assertEqual(c.total(), 11.0)\n\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n")
    return {}


REFACTOR_PROMPT = ("Rename the function calc_total to compute_total everywhere in this project (definition, imports, "
                   "calls and tests). Then run `python3 -m unittest -v` and make sure all tests pass.")


def refactor_check(d, state, log):
    rc, out = sh("grep -rn calc_total --include=*.py . || true", d)
    if out.strip():
        return False, "old name still present: " + out.strip()[:150]
    rc, out = sh("python3 -c 'from shop.pricing import compute_total' && python3 -m unittest -q", d)
    return rc == 0, out.strip()[-200:]


ACTIVITY = os.path.join(HOME, "lab", "activity.jsonl")   # written by the jarvis tool server, with the laptop's answers


def desktop_setup(d):
    return {"t0": time.time(), "agent": os.path.basename(os.path.dirname(d))}


DESKTOP_PROMPT = ("This task uses your own computer (the Agent PC) and your jarvis tools. "
                  "1) Open https://en.wikipedia.org/wiki/Tallinn on your computer with desktop_open. "
                  "2) Find the population of Tallinn (read the page with any tool you have). "
                  "3) Deliver a 3-line note to the owner with save_output named tallinn-note.md: the population and the "
                  "page address. 4) Show it on the owner's screen with show_output. Then reply with the population.")


def desktop_check(d, state, log):
    seen = []
    try:
        with open(ACTIVITY, errors="replace") as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and float(e.get("ts") or 0) >= state["t0"] and e.get("agent") == state["agent"]:
                    seen.append((str(e.get("tool")), str(e.get("detail", "")).lower()))
    except OSError:
        return False, "no activity log"
    opened = any(t == "desktop_open" and "tallinn" in x for t, x in seen)
    saved = any(t == "save_output:done" and "tallinn" in x for t, x in seen)     # the laptop confirmed the file
    shown = any(t == "show_output:done" for t, x in seen)
    if opened and saved and shown:
        return True, "opened the page on its computer; the laptop confirmed the note was saved and shown"
    return False, f"opened={opened} delivered={saved} shown={shown}; jarvis tools used: {sorted({t for t, _ in seen})[:8]}"


TASKS = {
    "build": (build_setup, BUILD_PROMPT, build_check),
    "fix": (fix_setup, FIX_PROMPT, fix_check),
    "data": (data_setup, DATA_PROMPT, data_check),
    "delegate": (delegate_setup, DELEGATE_PROMPT, delegate_check),
    "refactor": (refactor_setup, REFACTOR_PROMPT, refactor_check),
    "desktop": (desktop_setup, DESKTOP_PROMPT, desktop_check),
}


# ---------------------------------------------------------------- agents: how to run one prompt non-interactively in a folder

def goose_env():
    env = {}
    for line in open(os.path.join(HOME, ".jarvis-goose.env")):
        if "=" in line and not line.startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            env[k] = v
    return env


AGENTS = {
    "hermes": lambda prompt, d: (["hermes", "chat", "-q", prompt, "--oneshot", "--quiet", "--in", d, "--max-turns", "40", "--format", "stream-json"], {}),
    "opencode": lambda prompt, d: (["opencode", "run", "--format", "json", "--dir", d, prompt], {}),
    "goose": lambda prompt, d: (["goose", "run", "--no-session", "--max-turns", "40", "--debug", "-t", prompt], goose_env()),
}


def check_only(agent, task):
    """Judge a finished run again (e.g. after a checker fix) without rerunning the agent."""
    setup, prompt, check = TASKS[task]
    d = os.path.join(WORK, agent, task)
    tmp = os.path.join(WORK, "_state", task)
    shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp)
    state = setup(tmp)                       # setups are deterministic: same expected answers, agent's folder untouched
    log = open(os.path.join(d, "_agent.log")).read() if os.path.exists(os.path.join(d, "_agent.log")) else ""
    ok, why = check(d, state, log)
    return {"agent": agent, "task": task, "pass": ok, "why": why, "seconds": None, "exit": None, "timed_out": None, "log_chars": len(log)}


def run_one(agent, task, timeout):
    setup, prompt, check = TASKS[task]
    d = os.path.join(WORK, agent, task)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    state = setup(d)
    cmd, extra_env = AGENTS[agent](prompt, d)
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=d, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "PATH": PATH, **extra_env}, stdin=subprocess.DEVNULL)
        log, rc, timed_out = p.stdout + "\n" + p.stderr, p.returncode, False
    except subprocess.TimeoutExpired as e:
        log = ((e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) + "\n" + \
              ((e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""))
        rc, timed_out = None, True
    secs = round(time.time() - t)
    open(os.path.join(d, "_agent.log"), "w").write(log)
    try:
        ok, why = check(d, state, log)
    except Exception as e:
        ok, why = False, f"checker error {type(e).__name__}: {e}"
    return {"agent": agent, "task": task, "pass": ok, "why": why, "seconds": secs, "exit": rc, "timed_out": timed_out,
            "log_chars": len(log)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents", default="hermes,opencode,goose")
    ap.add_argument("--tasks", default=",".join(TASKS))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--check-only", action="store_true", help="re-judge existing runs, do not start agents")
    a = ap.parse_args()
    os.makedirs(RESULTS, exist_ok=True)
    out = []
    for task in a.tasks.split(","):
        for agent in a.agents.split(","):
            r = check_only(agent, task) if a.check_only else run_one(agent, task, a.timeout)
            r["seconds"] = r["seconds"] or 0
            out.append(r)
            print(f"{task:9s} {agent:9s} {'PASS' if r['pass'] else 'FAIL'}  {r['seconds']:4d}s  {r['why'][:110]}", flush=True)
            name = time.strftime("%Y%m%d-%H%M") + ("-recheck" if a.check_only else "-demo") + ".json"
            json.dump(out, open(os.path.join(RESULTS, name), "w"), indent=1)
    print(f"passed {sum(r['pass'] for r in out)}/{len(out)}")


if __name__ == "__main__":
    main()
