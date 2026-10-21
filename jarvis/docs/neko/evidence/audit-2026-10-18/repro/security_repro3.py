import sys, json, random, string
sys.path.insert(0, "/src/jarvis/kernel")
from jarvis_kernel import scrub as S
random.seed(7)
gsk = "gsk_" + "".join(random.choice(string.ascii_letters + string.digits) for _ in range(52))
hf = "hf_" + "".join(random.choice(string.ascii_letters) for _ in range(34))
for n, s in {"groq": gsk, "huggingface_letters": hf}.items():
    for txt in ("config value " + s, "GROQ_KEY " + s, "Authorization: Bearer " + s):
        c, f, b = S.scrub(txt, deep=True)
        print("R5d", n, repr(txt[:14]), json.dumps({"redacted": s not in c, "findings": f}))
