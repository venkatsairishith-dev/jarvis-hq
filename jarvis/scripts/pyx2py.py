"""IndicTransToolkit 1.1.1 processor.pyx (MIT) -> plain Python, because the package has no Windows wheel and this laptop has no MSVC (the mingw
gcc here is 32-bit). The code is the same; only the Cython declarations are removed. Used by jarvis/scripts/install_indictrans_processor.py."""
import re
import sys

src = open(sys.argv[1], encoding="utf-8").read().split("\n")
TYPES = r"(?:str|int|list|dict|set|tuple|object|bint|float|bool|List\[str\]|Union\[[^\]]*\])"
out = []
i = 0
while i < len(src):
    line = src[i]
    s = line.strip()
    if s.startswith("# cython:"):
        i += 1
        continue
    line = line.replace("cdef class ", "class ")
    # whole-line declarations without a value: `cdef int i`, `cdef int i, j`, `cdef public bint inference`, `cdef dict _flores_codes`
    m = re.match(r"^(\s*)cdef (?:public )?" + TYPES + r"\s+[\w, ]+$", line)
    if m:
        i += 1
        continue
    # declarations with a value: `cdef int i = 0`
    m = re.match(r"^(\s*)cdef (?:public )?" + TYPES + r"\s+(\w+)\s*=(.*)$", line)
    if m:
        out.append(f"{m.group(1)}{m.group(2)} ={m.group(3)}")
        i += 1
        continue
    # method headers (cdef/cpdef), possibly spread over several lines
    m = re.match(r"^(\s*)(?:cdef|cpdef)\s+" + TYPES + r"\s+(\w+)\((.*)$", line)
    if m:
        indent, name, rest = m.group(1), m.group(2), m.group(3)
        header = [rest]
        while not re.search(r"\)\s*(?:except \*)?\s*:\s*$", header[-1]):
            i += 1
            header.append(src[i])
        text = "\n".join(header)
        text = re.sub(r"\)\s*except \*\s*:\s*$", "):", text)
        text = re.sub(r"\)\s*:\s*$", "):", text)
        # parameter types: `str text`, `List[str] batch`, `bint is_target=False`, `int num_return_sequences=1`
        text = re.sub(r"(?<![\w.])" + TYPES + r"\s+(\w+)(?=\s*[,=)\n])", r"\1", text)
        out.append(f"{indent}def {name}({text}")
        i += 1
        continue
    out.append(line)
    i += 1
res = "\n".join(out)
res = res.replace("<str>", "").replace("<int>", "")
open(sys.argv[2], "w", encoding="utf-8").write(res)
print("lines", len(out))
