"""Assemble the hub page: index.html with the sanitised icon sprite and the sparkline library inlined, so the served
page is one file (fonts sit beside it). Usage: python build_hub.py <out dir>   (writes <out>/index.html and fonts/)"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "assets")
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "build")
page = open(os.path.join(HERE, "index.html"), encoding="utf-8").read()
sprite = open(os.path.join(A, "icons-sprite.svg"), encoding="utf-8").read()
spark = open(os.path.join(A, "sparkline.js"), encoding="utf-8").read().replace("//# sourceMappingURL=sparkline.js.map", "")
for marker in ("<!--SPRITE-->", "/*SPARKLINE*/"):
    if page.count(marker) != 1:
        raise SystemExit(f"marker {marker} must appear exactly once")
page = page.replace("<!--SPRITE-->", "<!-- Lucide icons, ISC licence (assets/LICENSE-lucide.txt) -->" + sprite)
page = page.replace("/*SPARKLINE*/", "/* @fnando/sparkline 0.3.10, MIT licence */\n" + spark)
os.makedirs(os.path.join(out, "fonts"), exist_ok=True)
with open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write(page)
for f in ("geist.woff2", "geist-mono.woff2", "OFL.txt"):
    shutil.copy(os.path.join(A, "fonts", f), os.path.join(out, "fonts", f))
print("built", os.path.join(out, "index.html"), len(page), "bytes")
