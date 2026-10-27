"""Groups the warnings of a render.mjs --report file by kind and screen: python summarize.py .work/report.json"""
import collections
import json
import re
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
groups = collections.defaultdict(set)
for line in report["warnings"]:
    m = re.match(r"(.+?) @(\S+) (\w+) (\w+): (.*)", line)
    if not m:
        print(line)
        continue
    screen, _size, _theme, kind, rest = m.groups()
    if kind == "smallTargets":
        rest = re.sub(r'"[^"]*"', "", rest)
    groups[(kind, screen.split(":")[0], rest[:150])].add(screen)
for (kind, area, rest), screens in sorted(groups.items()):
    print(f"{kind:13} {area:9} {rest}   [{len(screens)} screens, e.g. {sorted(screens)[0]}]")
print("\nfailures:", report["failures"])
