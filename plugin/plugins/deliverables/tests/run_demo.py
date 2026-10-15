"""Build the demo deck and report end to end on this PC and print the check results (Windows, neko-venv, repo root).

    D:\\jarvis\\tools\\neko-venv\\Scripts\\python.exe -m plugin.plugins.deliverables.tests.run_demo [--open]
"""
import json
import sys
from pathlib import Path

from plugin.plugins.deliverables import core

HERE = Path(__file__).parent / "fixtures"


def main() -> int:
    open_after = "--open" in sys.argv
    ok = True
    for name, fn in (("deck", core.make_presentation), ("report", core.make_report)):
        spec = json.loads((HERE / f"demo_{name}.json").read_text(encoding="utf-8"))
        r = fn(spec, open_after)
        q = r["qa"]
        ok &= q["passed"]
        print(f"== {name}: {'PASS' if q['passed'] else 'FAIL'} in {r['seconds']} s -> {r['file']}")
        print("   checks:", json.dumps(q["checks"]))
        print("   counts:", json.dumps(q["counts"]))
        if r.get("animation"):
            print("   animation:", json.dumps({k: r["animation"][k] for k in ("requested", "succeeded", "failed")}))
        for w in q["warnings"][:8]:
            print("   warning:", w)
        print("   contact sheet:", q.get("contact_sheet"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
