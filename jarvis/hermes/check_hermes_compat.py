"""Does our memory-provider plugin fit a given Hermes Agent source tree? Read-only; imports only agent/memory_provider.py from that tree.

    python jarvis/hermes/check_hermes_compat.py PATH_TO_HERMES_CHECKOUT [PATH_TO_ANOTHER ...]

For each checkout it loads Hermes's real MemoryProvider base class, loads jarvis/hermes/plugins/jarvis_memory against it and checks:
the plugin subclasses the real base; every abstract method is implemented (the class can be built); register(ctx) hands out a NEW instance
on every load (Hermes' multiplexing contract: one instance per profile); and the plugin keeps no module-level mutable state.
Exit 0 only if every checkout passes. It does not start Hermes, so it runs on any Python 3.9+ (the real Hermes needs 3.14).
Used for the 0.22 upgrade plan (jarvis/docs/neko/HERMES_0_22_UPGRADE_2026-10-22.md); run it on the candidate tag's checkout before any deploy.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

PLUGIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plugins", "jarvis_memory", "__init__.py")


def check(src: str) -> dict:
    base_file = os.path.join(src, "agent", "memory_provider.py")
    if not os.path.isfile(base_file):
        return {"ok": False, "why": f"no agent/memory_provider.py under {src}"}
    for name in [k for k in sys.modules if k == "agent" or k.startswith("agent.") or k.startswith("jarvis_memory_under_test")]:
        del sys.modules[name]
    pkg = types.ModuleType("agent")
    pkg.__path__ = []
    sys.modules["agent"] = pkg
    spec = importlib.util.spec_from_file_location("agent.memory_provider", base_file)
    base = importlib.util.module_from_spec(spec)
    sys.modules["agent.memory_provider"] = base
    spec.loader.exec_module(base)
    pspec = importlib.util.spec_from_file_location("jarvis_memory_under_test", PLUGIN)
    plugin = importlib.util.module_from_spec(pspec)
    pspec.loader.exec_module(plugin)
    got: list = []

    class Ctx:
        def register_memory_provider(self, provider):
            got.append(provider)
    problems = []
    if not issubclass(plugin.JarvisMemoryProvider, base.MemoryProvider):
        problems.append("the plugin does not subclass the real MemoryProvider (it fell back to the stand-in base)")
    try:
        plugin.register(Ctx())
        plugin.register(Ctx())
    except TypeError as exc:                                     # an abstract method the new base requires and we do not have
        return {"ok": False, "why": f"cannot build the provider: {exc}"}
    if len(got) != 2 or got[0] is got[1]:
        problems.append("register(ctx) did not hand out a new instance on every load")
    shared = sorted(k for k, v in vars(plugin).items() if isinstance(v, (dict, list, set)) and not k.startswith("__"))
    if shared:
        problems.append(f"module-level mutable state: {shared}")
    return {"ok": not problems, "abstract": sorted(getattr(base.MemoryProvider, "__abstractmethods__", [])), "problems": problems}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    code = 0
    for src in argv:
        res = check(src)
        print(f"{'PASS' if res['ok'] else 'FAIL'}  {src}  {res.get('abstract', '')} {res.get('problems') or res.get('why') or ''}")
        code |= 0 if res["ok"] else 1
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
