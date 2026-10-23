import sys, json, base64, os
sys.path.insert(0, "/src/jarvis/kernel")
from jarvis_kernel import scrub as S
body = base64.b64encode(os.urandom(600)).decode()
pgp = "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----\n\n" + "\n".join(body[i:i+64] for i in range(0, len(body), 64)) + "\n-----END PGP PRIVATE KEY BLOCK-----"
c, f, b = S.scrub("x\n" + pgp, deep=True)
print("R5b_pgp_deep", json.dumps({"blocked": b, "findings": f, "body_left": body[:40] in c}))
samples = {"groq": "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4Y5z6", "huggingface": "hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
           "google": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "slack": "xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
           "stripe": "sk_live_" + "51AbCdEfGhIjKlMnOpQrStUv"}
for n, s in samples.items():
    c, f, b = S.scrub("config value " + s, deep=True)
    print("R5c_deep", n, json.dumps({"redacted": s not in c, "findings": f}))
